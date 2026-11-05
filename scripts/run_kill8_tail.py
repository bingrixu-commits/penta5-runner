"""AI 双反向冷形态排除 每期任务（手动触发：结算上一期 + 封存下一期 + 刷面板）。

流程：
1. 读取当前快照（手动触发，不等待数据源；源未就绪则报错退出）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 若本期参考期尚无封存，则在发布窗口内请求一次 DeepSeek（最多 3 次尝试）；
4. 封存 → 标记已发布 → 写面板区块。任何失败只记录，不补发历史期。
"""
from pathlib import Path
import sys

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from stats_history import beijing_now, load_snapshot
from ai_prediction_guard import publication_rejection
from ai_call_control import AICallsDisabled, require_ai_enabled

import kill8_tail
import panel_context
from panel_io import update_panel
from kill_eight_view import apply_kill_eight_block

_LOCK_HANDLE = None


def publish_block():
    update_panel(lambda page: apply_kill_eight_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill8_tail_job.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = path.open('a')
    try:
        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)


def main():
    _acquire_job_lock()
    require_ai_enabled()
    snapshot = load_snapshot(persist=False)
    kill8_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill8 invalid latest period: {reference!r}')
    seals = kill8_tail.load_seals()
    if kill8_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill8 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill8 skipped: {rejection}; ref={reference}')
        return 0
    deadline, target = kill8_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    panel_snapshot = panel_context.build(reference, target)
    _panel_text, _panel_sha = panel_context.prompt_text(panel_snapshot)
    print('kill8 panel context sha=%s bytes=%d' % (_panel_sha[:16], len(_panel_text)))
    exclusions = kill8_tail.load_position_exclusions()
    print('kill8 position exclusions: %s' % (exclusions,))
    prompt, audit = kill8_tail.build_prompt(
        reference, target, rows,
        panel_snapshot=panel_snapshot, exclusions=exclusions)
    try:
        prediction, generated_at, meta = kill8_tail.request_prediction(
            prompt, reference, target, deadline, exclusions=exclusions)
    except Exception as error:
        print(f"kill8 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill8 publication rejected after inference: {rejection}; ref={reference}')
        return 0
    seal = kill8_tail.create_seal(reference, target, latest.get('opentime'), prediction,
                                  prompt, audit, generated_at, meta)
    kill8_tail.save_seal(seal)
    kill8_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill8_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill8 panel publication failed: {type(error).__name__}')
        return 1
    print(f'kill8 published: ref={reference} target={target} rule={kill8_tail.RULE_VERSION} · '
          + ' · '.join(f'{kill8_tail.REVERSE_LABELS[kind]} '
                        f'{prediction[kill8_tail.FORM_FIELDS[kind]]}'
                        for kind in kill8_tail.FORM_KINDS))
    print(f'kill8 attack: {kill8_tail.build_attack(seal)}')
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
        print(f'kill8 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)
