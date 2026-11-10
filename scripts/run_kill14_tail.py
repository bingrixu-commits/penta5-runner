"""定位排除（kill14 独立框架）每期任务（cron 每 5 分钟，开奖后启动）。

流程：
1. 等待本期开奖数据就绪（最多 55 秒）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 若本期参考期尚无封存，则读取近 70 / 50 / 30 / 15 期冷码榜与逐位置连败 / 连中状态，
   在发布窗口内请求一次 DeepSeek（最多 3 次尝试）：四个位置各排除 1 码，
   任一窗口连败的位置强制取该位置冷码第 1 个（候选 = 连败窗口的冷码第 1），其余位置由 AI 自选；
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

import kill14_tail
import panel_context
from panel_io import update_panel
from kill_fourteen_view import apply_kill_fourteen_block

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
            raise SystemExit(f'kill14 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def publish_block():
    update_panel(lambda page: apply_kill_fourteen_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill14_tail_job.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = path.open('a')
    try:
        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)


def _picked_text(seal):
    parts = []
    for item in seal.get('positions') or []:
        if not isinstance(item, dict):
            continue
        basis = str(item.get('basis') or '')
        window = None
        if basis.startswith('cold') and basis.endswith('-rank1'):
            digits = basis[4:-6]
            if digits.isdigit():
                window = int(digits)
        if window is not None:
            note = ('连败强制·%d期冷1' if item.get('enforced')
                    else '%d期冷1') % window
        else:
            note = 'AI自选' if basis == 'ai' else (basis or '-')
        parts.append('%s排%s(%s)' % (item.get('label') or item.get('key'), item.get('kill'), note))
    return ' · '.join(parts)


def main():
    _acquire_job_lock()
    require_ai_enabled()
    snapshot = wait_for_source()
    kill14_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill14 invalid latest period: {reference!r}')
    seals = kill14_tail.load_seals()
    if kill14_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill14 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill14 skipped: {rejection}; ref={reference}')
        return 0
    deadline, target = kill14_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    rows = [row for row in rows if len(row) == 4]
    context = kill14_tail.build_context(rows)
    enforced = context.get('enforced') or []
    print('kill14 cold context: windows=%s rows=%d enforced=%s'
          % ('/'.join(str(window) for window in kill14_tail.STATS_WINDOWS), len(rows),
             ','.join(str(item.get('key')) for item in enforced) or '-'))
    previous_seal = seals[-1] if seals else None
    panel_snapshot = panel_context.build(reference, target, extra_panel_input=True)
    _panel_text, _panel_sha = panel_context.prompt_text(panel_snapshot)
    print('kill14 panel context sha=%s bytes=%d' % (_panel_sha[:16], len(_panel_text)))
    prompt, audit = kill14_tail.build_prompt(
        reference, target, rows,
        context=context,
        previous_seal=previous_seal,
        panel_snapshot=panel_snapshot)
    try:
        prediction, generated_at, meta = kill14_tail.request_prediction(
            prompt, reference, target, deadline,
            value_check=lambda value: kill14_tail.normalize_response(value, context))
    except Exception as error:
        print(f"kill14 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill14 publication rejected after inference: {rejection}; ref={reference}')
        return 0
    seal = kill14_tail.create_seal(reference, target, latest.get('opentime'), prediction, rows,
                                   context=context, prompt=prompt, audit=audit,
                                   generated_at=generated_at, response_meta=meta)
    kill14_tail.save_seal(seal)
    kill14_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill14_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill14 panel publication failed: {type(error).__name__}')
        return 1
    print(f'kill14 published: ref={reference} target={target} rule={kill14_tail.RULE_VERSION} '
          f'{_picked_text(seal)}')
    reason = str(seal.get('reason') or '').strip()
    if reason:
        print('kill14 reason: ' + reason)
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
        print(f'kill14 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)
