"""AI 二位置 + 一合尾 + 二数合/三数合 每期任务（cron 每 5 分钟，开奖后启动）。

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

import kill2_tail
import panel_context
from panel_io import update_panel
from kill_two_view import apply_kill_two_block

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
            raise SystemExit(f'kill2 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def publish_block():
    update_panel(lambda page: apply_kill_two_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill2_tail_job.lock'
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
    kill2_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill2 invalid latest period: {reference!r}')
    seals = kill2_tail.load_seals()
    if kill2_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill2 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill2 skipped: {rejection}; ref={reference}')
        return 0
    deadline, target = kill2_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    previous_seal = seals[-1] if seals else None
    previous_tail = (previous_seal.get('tail') or {}) if isinstance(previous_seal, dict) else {}
    position_limit = kill2_tail.position_restrictions(seals)
    panel_snapshot = panel_context.build(
        reference, target,
        kill2_stats=kill2_tail.today_stats(),
        kill2_previous=previous_seal)
    _panel_text, _panel_sha = panel_context.prompt_text(panel_snapshot)
    print('kill2 panel context sha=%s bytes=%d' % (_panel_sha[:16], len(_panel_text)))
    prompt, audit = kill2_tail.build_prompt(
        reference, target, rows,
        previous_tail=previous_tail if isinstance(previous_tail, dict) else None,
        position_limit=position_limit, panel_snapshot=panel_snapshot)

    def _value_check(item):
        kill2_tail.validate_position_choice(item, position_limit)

    try:
        prediction, generated_at, meta = kill2_tail.request_prediction(
            prompt, reference, target, deadline, value_check=_value_check)
    except Exception as error:
        print(f"kill2 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill2 publication rejected after inference: {rejection}; ref={reference}')
        return 0
    settlements = kill2_tail.load_settlements()
    prediction = kill2_tail.apply_tail_policy(prediction, seals, settlements)
    seal = kill2_tail.create_seal(reference, target, latest.get('opentime'), prediction,
                                  prompt, audit, generated_at, meta,
                                  extra={'position_restriction': position_limit})
    kill2_tail.save_seal(seal)
    kill2_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill2_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill2 panel publication failed: {type(error).__name__}')
        return 1
    display_only = prediction.get('display_only')
    picked = ' · '.join(
        '%s 杀%s%s' % (kill2_tail.POSITION_LABELS[key], prediction['positions'][key],
                       '(仅展示)' if key == display_only else '')
        for key, _label, _index in kill2_tail.POSITIONS if key in prediction['positions'])
    dropped = kill2_tail.POSITION_LABELS.get(prediction.get('dropped'), prediction.get('dropped'))
    print('kill2 published: ref=%s target=%s %s · %s合尾 杀%s · 二数合%s 三数合%s · 放弃:%s'
          % (reference, target, picked, prediction['tail']['label'],
             prediction['tail']['kill'], prediction['he2_keep'],
             prediction['he3_keep'], dropped))
    limited = [f'{kill2_tail.POSITION_LABELS.get(key, key)}换码'
               for key in (position_limit.get('blocked_digits') or {})]
    if limited:
        print('kill2 position limits enforced: ' + ', '.join(limited))
    tail_policy = prediction.get('tail_policy') or {}
    if tail_policy:
        print('kill2 tail policy: ' + str(tail_policy.get('note'))
              + ' · sealed=' + str(prediction['tail'].get('key')) + '/'
              + str(prediction['tail'].get('kill'))
              + ' · model=' + str((tail_policy.get('model_tail') or {}).get('key')) + '/'
              + str((tail_policy.get('model_tail') or {}).get('kill')))
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
        print(f'kill2 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)

