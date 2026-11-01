"""AI 三形态排除（面板候选池挑选）+ 二数合 + 三数合 每期任务（cron 每 5 分钟，开奖后启动）。

流程：
1. 等待本期开奖数据就绪（最多 55 秒）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 汇总面板各框架本期已发布四位反向形态作为候选池，让模型从中挑 3 个；
4. 若本期参考期尚无封存，则在发布窗口内请求一次 DeepSeek（最多 3 次尝试）；
5. 封存 → 标记已发布 → 写面板区块。任何失败只记录，不补发历史期。
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

import kill9_tail
import panel_context
from panel_io import update_panel
from kill_nine_view import apply_kill_nine_block

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
            raise SystemExit(f'kill9 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def publish_block():
    update_panel(lambda page: apply_kill_nine_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill9_tail_job.lock'
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
    kill9_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill9 invalid latest period: {reference!r}')
    seals = kill9_tail.load_seals()
    if kill9_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill9 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill9 skipped: {rejection}; ref={reference}')
        return 0
    deadline, target = kill9_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    pool = kill9_tail.build_form_pool(reference, target)
    pool_text = '、'.join(
        '%s(%s)%s' % (entry.get('form'), entry.get('kind'),
                      '*' if entry.get('stale') else '')
        for entry in pool)
    print('kill9 pool: count=%d forms=%s (*=来源期非本期)' % (len(pool), pool_text or '-'))
    if len(pool) < kill9_tail.COUNTED_FORMS:
        print('kill9 pool too small: count=%d (<%d)'
              % (len(pool), kill9_tail.COUNTED_FORMS))
    previous_seal = seals[-1] if seals else None
    panel_snapshot = panel_context.build(
        reference, target,
        kill9_stats=kill9_tail.today_stats(),
        kill9_previous=previous_seal)
    _panel_text, _panel_sha = panel_context.prompt_text(panel_snapshot)
    print('kill9 panel context sha=%s bytes=%d' % (_panel_sha[:16], len(_panel_text)))
    prompt, audit = kill9_tail.build_prompt(
        reference, target, rows, panel_snapshot=panel_snapshot, pool=pool)
    try:
        prediction, generated_at, meta = kill9_tail.request_prediction(
            prompt, reference, target, deadline, pool=pool)
    except Exception as error:
        print(f"kill9 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill9 publication rejected after inference: {rejection}; ref={reference}')
        return 0
    seal = kill9_tail.create_seal(reference, target, latest.get('opentime'), prediction,
                                  prompt, audit, generated_at, meta)
    kill9_tail.save_seal(seal)
    kill9_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill9_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill9 panel publication failed: {type(error).__name__}')
        return 1
    forms_text = ' · '.join(
        '形态%d %s(%s)' % (index, form, kind)
        for index, (form, kind) in enumerate(
            zip(prediction['forms'], prediction['form_kinds']), start=1))
    print(f'kill9 published: ref={reference} target={target} rule={kill9_tail.RULE_VERSION} · '
          f'{forms_text} · '
          f'二数合 {prediction["he2_keep"]} · 三数合 {prediction["he3_keep"]}')
    print(f'kill9 attack: {kill9_tail.build_attack(seal)}')
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
        print(f'kill9 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)
