"""AI 双定位杀码 + 三数共现避开三组 + 二数合/三数合 每期任务（cron 每 5 分钟，开奖后启动）。

口径 v8：AI 每期从 千/百/十/个 中固定选 2 个不同位置、各排除 1 码；整组固定 7 项 =
2 个位置 + 三数共现避开 1/2/3 组（系统按目标期给定）+ 二数合保留 4 码 + 三数合保留 5 码
（取 kill10 同目标期已发布封存）。无合尾、无单双形态。

流程：
1. 等待本期开奖数据就绪（最多 55 秒）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 若本期参考期尚无封存，则在发布窗口内请求一次 DeepSeek（最多 3 次尝试）；
4. 取三数共现 3 组（state.currentPrediction）与 kill10 同目标期 he2/he3 封存（带等待）；
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

import kill1_tail
import panel_context
from panel_io import update_panel
from kill_one_view import apply_kill_one_block

WAIT_SECONDS = 55.0
THREE_AVOID_WAIT_SECONDS = 30.0
KILL10_WAIT_SECONDS = 75.0
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
            raise SystemExit(f'kill1 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def wait_three_avoid(target, seconds=THREE_AVOID_WAIT_SECONDS):
    """与 kill3 同源的三数共现 3 组；state.currentPrediction 在开奖后约 7 秒生成。"""
    started = time.monotonic()
    while True:
        groups = kill1_tail.resolve_three_avoid(target)
        if groups:
            return groups
        if time.monotonic() - started > seconds:
            return []
        time.sleep(2.0)


def wait_kill10_keeps(target, reference, deadline, seconds=KILL10_WAIT_SECONDS):
    """取 kill10 同目标期已发布封存的 he2_keep/he3_keep；超时返回 None（不编造）。"""
    limit = min(deadline - timedelta(seconds=20), beijing_now() + timedelta(seconds=seconds))
    while True:
        keeps = kill1_tail.kill10_keeps(target, reference)
        if keeps:
            return keeps
        now = beijing_now()
        if now >= limit:
            return None
        time.sleep(2.0)


def publish_block():
    update_panel(lambda page: apply_kill_one_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill1_tail_job.lock'
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
    kill1_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill1 invalid latest period: {reference!r}')
    seals = kill1_tail.load_seals()
    if kill1_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill1 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill1 skipped: {rejection}; ref={reference}')
        return 0
    deadline, target = kill1_tail.deadline_for(reference)
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    previous_seal = seals[-1] if seals else None
    panel_snapshot = panel_context.build(
        reference, target,
        kill1_stats=kill1_tail.today_stats(),
        kill1_previous=previous_seal)
    _panel_text, _panel_sha = panel_context.prompt_text(panel_snapshot)
    print('kill1 panel context sha=%s bytes=%d' % (_panel_sha[:16], len(_panel_text)))
    three_avoid = wait_three_avoid(target)
    if not three_avoid:
        print(f'kill1 skipped: three-avoid-groups-not-ready; ref={reference} target={target}')
        return 1
    print('kill1 three-avoid: ' + ' · '.join(item['label'] + '=' + item['value']
                                             for item in three_avoid))
    prompt, audit = kill1_tail.build_prompt(
        reference, target, rows,
        three_avoid=three_avoid,
        panel_snapshot=panel_snapshot)
    try:
        prediction, generated_at, meta = kill1_tail.request_prediction(
            prompt, reference, target, deadline)
    except Exception as error:
        print(f"kill1 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    keeps = wait_kill10_keeps(target, reference, deadline)
    if not keeps:
        print(f'kill1 skipped: kill10-he2-he3-not-ready; ref={reference} target={target}')
        return 1
    print('kill1 kill10 keeps: he2=%s he3=%s (ref=%s published=%s)'
          % (keeps['he2_keep'], keeps['he3_keep'],
             keeps.get('ref_period'), keeps.get('published_at')))
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill1 publication rejected after inference: {rejection}; ref={reference}')
        return 0
    extra = {
        'three_avoid': three_avoid,
        'three_avoid_source': 'state.currentPrediction.extraKills',
        'he2_keep': keeps['he2_keep'],
        'he3_keep': keeps['he3_keep'],
        'kill10_ref_period': keeps.get('ref_period'),
        'kill10_target_period': keeps.get('target_period'),
        'kill10_published_at': keeps.get('published_at'),
    }
    seal = kill1_tail.create_seal(reference, target, latest.get('opentime'), prediction,
                                  prompt, audit, generated_at, meta, extra=extra)
    kill1_tail.save_seal(seal)
    kill1_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill1_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill1 panel publication failed: {type(error).__name__}')
        return 1
    picked = ' · '.join('%s 杀%s' % (kill1_tail.POSITION_LABELS[key],
                                      prediction['positions'][key])
                         for key in prediction.get('counted') or [])
    print(f'kill1 published: ref={reference} target={target} '
          f'rule={kill1_tail.RULE_VERSION} {picked} · '
          f'除对数{"-".join(item["value"] for item in three_avoid)} · '
          f'二数合{keeps["he2_keep"]}三数合{keeps["he3_keep"]}各')
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
        print(f'kill1 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)
