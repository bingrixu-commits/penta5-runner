"""定位冷码（cold30，纯确定性）每期任务（cron 每 5 分钟，开奖后启动）。

流程：
1. 等待本期开奖数据就绪（最多 55 秒）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 若本期参考期尚无封存，则在发布窗口内本地重算近 30 期四个位置的冷码并封存；
4. 封存 -> 标记已发布 -> 写面板区块。任何失败只记录，不补发历史期。

不设沿用/换码：每期严格按滚动窗口重算，四位置各取最冷 1 码。
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

import cold30_tail
from panel_io import update_panel
from cold_thirty_view import apply_cold_thirty_block

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
            raise SystemExit(f'cold30 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def publish_block():
    update_panel(lambda page: apply_cold_thirty_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.cold30_tail_job.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = path.open('a')
    try:
        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)


def main():
    _acquire_job_lock()
    snapshot = wait_for_source()
    cold30_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'cold30 invalid latest period: {reference!r}')
    seals = cold30_tail.load_seals()
    if cold30_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'cold30 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'cold30 skipped: {rejection}; ref={reference}')
        return 0
    _deadline, target = cold30_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    rows = [row for row in rows if len(row) == 4]
    prediction = cold30_tail.select_cold_positions(rows)
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'cold30 publication rejected after selection: {rejection}; ref={reference}')
        return 0
    seal = cold30_tail.create_seal(reference, target, latest.get('opentime'),
                                   prediction, rows)
    cold30_tail.save_seal(seal)
    cold30_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        cold30_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'cold30 panel publication failed: {type(error).__name__}')
        return 1
    positions_text = ' · '.join('%s 杀%s' % (item['label'], item['kill'])
                                for item in prediction['positions'])
    print(f'cold30 published: ref={reference} target={target} rule={cold30_tail.RULE_VERSION} '
          f'{positions_text} · 窗口{cold30_tail.WINDOW}期')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print(f'cold30 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)
