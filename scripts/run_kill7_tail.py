"""AI 三位置 + 一合尾 + 二数合/三数合 每期任务（cron 每 5 分钟，开奖后启动）。

流程：
1. 等待本期开奖数据就绪（最多 55 秒）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 若本期参考期尚无封存，则在发布窗口内请求一次 DeepSeek（最多 3 次尝试）；
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
from ai_call_control import AICallsDisabled, require_ai_enabled

import kill7_tail
import panel_context
from panel_io import update_panel
from kill_seven_view import apply_kill_seven_block

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
            raise SystemExit(f'kill7 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def publish_block():
    update_panel(lambda page: apply_kill_seven_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill7_tail_job.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = path.open('a')
    try:
        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)


def main():
    _acquire_job_lock()
    require_ai_enabled()
    snapshot = wait_for_source()
    kill7_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill7 invalid latest period: {reference!r}')
    seals = kill7_tail.load_seals()
    if kill7_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill7 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill7 skipped: {rejection}; ref={reference}')
        return 0
    deadline, target = kill7_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    previous_seal = seals[-1] if seals else None
    previous_tail = (previous_seal.get('tail') or {}) if isinstance(previous_seal, dict) else {}
    panel_snapshot = panel_context.build(
        reference, target,
        kill7_stats=kill7_tail.today_stats(),
        kill7_previous=previous_seal)
    _panel_text, _panel_sha = panel_context.prompt_text(panel_snapshot)
    print('kill7 panel context sha=%s bytes=%d' % (_panel_sha[:16], len(_panel_text)))
    prompt, audit = kill7_tail.build_prompt(
        reference, target, rows,
        previous_tail=previous_tail if isinstance(previous_tail, dict) else None,
        panel_snapshot=panel_snapshot)

    try:
        prediction, generated_at, meta = kill7_tail.request_prediction(
            prompt, reference, target, deadline)
    except Exception as error:
        print(f"kill7 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill7 publication rejected after inference: {rejection}; ref={reference}')
        return 0
    seal = kill7_tail.create_seal(reference, target, latest.get('opentime'), prediction,
                                  prompt, audit, generated_at, meta)
    kill7_tail.save_seal(seal)
    kill7_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill7_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill7 panel publication failed: {type(error).__name__}')
        return 1
    picked = ' · '.join(
        '%s 杀%s' % (kill7_tail.POSITION_LABELS[key], prediction['positions'][key])
        for key, _label, _index in kill7_tail.POSITIONS if key in prediction['positions'])
    unselected = '、'.join(kill7_tail.POSITION_LABELS.get(key, key)
                          for key in (prediction.get('unselected') or []))
    print('kill7 published: ref=%s target=%s %s · %s合尾 杀%s · 二数合%s 三数合%s · 未选:%s'
          % (reference, target, picked, prediction['tail']['label'],
             prediction['tail']['kill'], prediction['he2_keep'],
             prediction['he3_keep'], unselected))
    print('kill7 model-free: positions/tail decided by model, no forced hold/rotation.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except AICallsDisabled:
        print('AI API manually stopped; no request launched.')
        raise SystemExit(0)
    except Exception as error:
        print(f'kill7 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)

