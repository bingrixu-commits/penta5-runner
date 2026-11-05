"""AI 三位置 + 一合尾 每期任务（cron 每 5 分钟，开奖后启动）。

流程：
1. 等待本期开奖数据就绪（最多 55 秒）；
2. 结算上一期已开奖的封存（只追加，不改封存）；
3. 若本期参考期尚无封存，则在发布窗口内请求一次 DeepSeek（最多 3 次尝试）；
4. 封存 → 标记已发布 → 写面板区块。任何失败只记录，不补发历史期。
"""
from datetime import timedelta
from pathlib import Path
import json
import sys
import time

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from stats_history import beijing_now, load_snapshot
from ai_prediction_guard import publication_rejection
from ai_call_control import AICallsDisabled, require_ai_enabled

import kill3_tail
import panel_context
from panel_io import update_panel
from kill_three_view import apply_kill_three_block

WAIT_SECONDS = 55.0
_LOCK_HANDLE = None
# kill3 专属静态上下文回灌（2026-11-05 试验，用户 m00325「回做」）：12:26 起 selected_six 退役，
# 共享 lib/panel_context.py 不再向各框架 prompt 注入 frameworks['six'] / ['selected_six']。
# 本开关只对 kill3 的 prompt 面板段回灌两段历史静态快照；删除该 JSON 即关闭，不影响其他框架。
FROZEN_CONTEXT_PATH = ROOT / 'ops' / 'kill3-frozen-context-ref20261105148.json'


def _apply_frozen_panel_context(snapshot):
    """只把退役框架的两段历史静态快照补回 kill3 的 panel_context（只读，不改共享模块）。"""
    if not isinstance(snapshot, dict):
        return snapshot
    try:
        payload = json.loads(FROZEN_CONTEXT_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return snapshot
    except Exception as error:
        print('kill3 frozen context: load failed %s: %s'
              % (type(error).__name__, str(error)[:120]))
        return snapshot
    restored = []
    frameworks = snapshot.get('frameworks')
    frozen_frameworks = payload.get('frameworks')
    if isinstance(frameworks, dict) and isinstance(frozen_frameworks, dict):
        for key in ('selected_six', 'six'):
            block = frozen_frameworks.get(key)
            if isinstance(block, dict):
                frameworks[key] = block
                restored.append('frameworks.' + key)
    tiers = snapshot.get('model_tiers')
    frozen_tiers = payload.get('model_tiers')
    if isinstance(tiers, dict) and isinstance(frozen_tiers, dict):
        for key, value in frozen_tiers.items():
            tiers[key] = value
            restored.append('model_tiers.' + key)
    if restored:
        print('kill3 frozen context: injected %s from seal ref %s (retired 988b221)'
              % (', '.join(restored), payload.get('source_ref_period') or '?'))
    return snapshot


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
            raise SystemExit(f'kill3 source not ready: expected={expected} latest={latest}')
        time.sleep(1.0)


def publish_block():
    update_panel(lambda page: apply_kill_three_block(page))


def _acquire_job_lock():
    global _LOCK_HANDLE
    import fcntl
    path = ROOT / 'data' / '.kill3_tail_job.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = path.open('a')
    try:
        fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)


def _resolve_three_avoid(target, deadline, seconds=150.0, reserve=75.0):
    """取目标期的三数共现避开 1/2/3 组。

    三数组由主预测管线在开奖后写入 data/state.json（实测约 +63s），比本任务启动
    （开奖后 wait_for_source + 25s 偏移）更晚，因此这里轮询等待；但最多等到发布
    窗口仍留有 reserve 秒余量（AI 请求 + 封存需要的时间），并受 seconds 上限约束。
    """
    started = time.monotonic()
    budget = (deadline - beijing_now()).total_seconds() - reserve
    stop = min(started + seconds, started + max(20.0, budget))
    while True:
        groups = kill3_tail.resolve_three_avoid(target)
        if groups:
            return groups
        waited = time.monotonic() - started
        if time.monotonic() >= stop:
            raise SystemExit('kill3 three-avoid source missing for target=%s (waited %.0fs)'
                             % (target, waited))
        time.sleep(2.0)


def main():
    _acquire_job_lock()
    require_ai_enabled()
    snapshot = wait_for_source()
    kill3_tail.settle_pending(snapshot)
    latest = snapshot.get('latest') or {}
    reference = str(latest.get('period') or '')
    if len(reference) != 11:
        raise SystemExit(f'kill3 invalid latest period: {reference!r}')
    seals = kill3_tail.load_seals()
    if kill3_tail.find_seal(seals, reference) is not None:
        publish_block()
        print(f'kill3 existing seal preserved; panel restored. ref={reference}')
        return 0
    rejection = publication_rejection(reference, snapshot, beijing_now())
    if rejection:
        print(f'kill3 skipped: {rejection}; ref={reference}')
        return 0
    deadline, target = kill3_tail.deadline_for(reference)
    three_avoid = _resolve_three_avoid(target, deadline)
    print('kill3 three-avoid groups: ' + ' · '.join(
        '%s %s' % (group['label'], group['value']) for group in three_avoid))
    rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
    previous_seal = seals[-1] if seals else None
    previous_tail = (previous_seal.get('tail') or {}) if isinstance(previous_seal, dict) else {}
    previous_positions = (previous_seal.get('positions') or {}) if isinstance(previous_seal, dict) else {}
    latest_numbers = list(latest.get('numbers') or [])[:4]
    previous_hits = {}
    for _pkey, _plabel, _pindex in kill3_tail.POSITIONS:
        _pdigit = previous_positions.get(_pkey)
        if _pdigit is None or len(latest_numbers) <= _pindex:
            continue
        try:
            _pactual = int(latest_numbers[_pindex])
            _pkill = int(_pdigit)
        except (TypeError, ValueError):
            continue
        previous_hits[_pkey] = {'actual': str(_pactual), 'hit': _pactual != _pkill}
    if previous_hits:
        print('kill3 previous position hits: ' + ' · '.join(
            '%s %s%s' % (kill3_tail.POSITION_LABELS.get(k, k), v['actual'],
                         ' hit' if v['hit'] else ' miss') for k, v in previous_hits.items()))
    position_shadow = kill3_tail.position_hold_shadow()
    if position_shadow:
        _cur = position_shadow.get('current') or {}
        _policies = position_shadow.get('policies') or {}
        _cur_rate = _cur.get('rate')
        _hold3 = (_policies.get('hold3') or {}).get('rate')
        print('kill3 position shadow: replay %s periods / %s bets, 现行 %s (换码 %s), hold3 %s (换码 %s)' % (
            position_shadow.get('sample_periods'), _cur.get('bets'),
            ('%.2f%%' % (100 * _cur_rate)) if isinstance(_cur_rate, (int, float)) else '-',
            _cur.get('changes'),
            ('%.2f%%' % (100 * _hold3)) if isinstance(_hold3, (int, float)) else '-',
            (_policies.get('hold3') or {}).get('changes')))
    else:
        print('kill3 position shadow: unavailable')
    panel_snapshot = panel_context.build(
        reference, target,
        kill3_stats=kill3_tail.today_stats(),
        kill3_previous=previous_seal)

    panel_snapshot = _apply_frozen_panel_context(panel_snapshot)
    _panel_text, _panel_sha = panel_context.prompt_text(panel_snapshot)
    print('kill3 panel context sha=%s bytes=%d' % (_panel_sha[:16], len(_panel_text)))
    prompt, audit = kill3_tail.build_prompt(
        reference, target, rows,
        previous_tail=previous_tail if isinstance(previous_tail, dict) else None,
        previous_positions=previous_positions,
        previous_hits=previous_hits,
        position_shadow=position_shadow,
        panel_snapshot=panel_snapshot,
        three_avoid=three_avoid)

    try:
        prediction, generated_at, meta = kill3_tail.request_prediction(
            prompt, reference, target, deadline, rows=rows)
    except Exception as error:
        print(f"kill3 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    fresh = load_snapshot(persist=False)
    rejection = publication_rejection(reference, fresh, beijing_now())
    if rejection:
        print(f'kill3 publication rejected after inference: {rejection}; ref={reference}')
        return 0
    settlements = kill3_tail.load_settlements()
    prediction = kill3_tail.apply_tail_policy(prediction, seals, settlements)
    basis_used = kill3_tail.position_basis_flags(audit, prediction, previous_positions)
    hold_adopted = sorted(key for key, tags in basis_used.items() if 'hold_previous' in tags)
    print('kill3 position basis: ' + ' · '.join(
        '%s %s' % (kill3_tail.POSITION_LABELS.get(key, key), '/'.join(tags))
        for key, tags in basis_used.items()))
    seal = kill3_tail.create_seal(reference, target, latest.get('opentime'), prediction,
                                  prompt, audit, generated_at, meta,
                                  extra={'three_avoid': three_avoid,
                                         'position_hold_shadow': audit.get('position_shadow'),
                                         'position_basis_flags': basis_used,
                                         'hold_adopted': hold_adopted,
                                         'hold_policy': 'model-decided'})
    kill3_tail.save_seal(seal)
    kill3_tail.mark_published(reference)
    try:
        publish_block()
    except Exception as error:
        kill3_tail.mark_publication_failed(reference, f'panel-write-{type(error).__name__}')
        print(f'kill3 panel publication failed: {type(error).__name__}')
        return 1
    display_only = prediction.get('display_only')
    picked = ' · '.join(
        f'{kill3_tail.POSITION_LABELS[key]} 杀{prediction["positions"][key]}'
        + ('(仅展示)' if key == display_only else '')
        for key, _label, _index in kill3_tail.POSITIONS if key in prediction['positions'])
    dropped = kill3_tail.POSITION_LABELS.get(prediction['dropped'], prediction['dropped'])
    avoid_codes = '-'.join(group['value'] for group in three_avoid)
    print(f'kill3 published: ref={reference} target={target} {picked} · '
          f'{prediction["tail"]["label"]}合尾 杀{prediction["tail"]["kill"]} 放弃:{dropped}'
          f' · 三数共现 除对数{avoid_codes}')
    tail_policy = prediction.get('tail_policy') or {}
    if tail_policy:
        print('kill3 tail policy: ' + str(tail_policy.get('note')) + ' · ' + str(tail_policy.get('policy')))
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
        print(f'kill3 job failed: {type(error).__name__}: {str(error)[:160]}')
        raise SystemExit(1)

