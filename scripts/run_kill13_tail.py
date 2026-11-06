"""合尾双冷杀号（kill13，纯确定性）每期任务（cron 每 5 分钟，开奖后启动）。

流程：
1. 等待本期开奖数据就绪（最多 55 秒）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 若本期参考期尚无封存，则在发布窗口内本地重算近 50 期冷榜、取最冷 2 合尾并稳定化沿用；
4. 封存 → 标记已发布 → 写面板区块。任何失败只记录，不补发历史期。
"""
from datetime import timedelta
from pathlib import Path
import sys
import time

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from stats_history import beijing_now, load_snapshot
from ai_prediction_guard import publication_rejection

import kill13_tail
from panel_io import update_panel
from kill_thirteen_view import apply_kill_thirteen_block

WAIT_SECONDS = 55.0
_LOCK_HANDLE = None


def expected_reference(now):
    """Slot 001 starts 00:05; midnight belongs to the prior day's 288."""
    slot = (now.hour * 60 + now.minute) // 5
    if slot == 0:
        return (now - timedelta(days=1)).strftime('%Y%m%d') + '288'
    return now.strftime('%Y%m%d') + f'{slot:03d}'


def wait_for_source(seconds=WAIT_SECONDS):
    started = time.monotonic()
    while True:
        snapshot = load_snapshot(persist=False)
        expected = expected_reference(beijing_now())
        latest = str((snapshot.get('latest') or {}).get('period') or '')
        if latest == expected:
            return snapshot
        if time.monotonic() - started > seconds:
            raise SystemExit(f'kill13 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def publish_block():
    update_panel(lambda page: apply_kill_thirteen_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill13_tail_job.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = path.open('a')
    try:
        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)


def main():
    _acquire_job_lock()
    snapshot = wait_for_source()
    kill13_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill13 invalid latest period: {reference!r}')
    seals = kill13_tail.load_seals()
    if kill13_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill13 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill13 skipped: {rejection}; ref={reference}')
        return 0
    _deadline, target = kill13_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    rows = [row for row in rows if len(row) == 4]
    previous_seal = seals[-1] if seals else None
    previous_tails = previous_seal.get('tails') if isinstance(previous_seal, dict) else None
    prediction = kill13_tail.select_cold_two(rows, previous_tails)
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill13 publication rejected after selection: {rejection}; ref={reference}')
        return 0
    seal = kill13_tail.create_seal(reference, target, latest.get('opentime'),
                                   prediction, rows)
    kill13_tail.save_seal(seal)
    kill13_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill13_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill13 panel publication failed: {type(error).__name__}')
        return 1
    tails_text = ' · '.join('%s合尾 杀%s' % (item['label'], item['kill'])
                            for item in prediction['tails'])
    policy = prediction.get('tail_policy') or {}
    print(f'kill13 published: ref={reference} target={target} rule={kill13_tail.RULE_VERSION} '
          f'{tails_text} · 沿用{policy.get("kept_count")}项/换码{policy.get("swapped_count")}项')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print(f'kill13 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)
