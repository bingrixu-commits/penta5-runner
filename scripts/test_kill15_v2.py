#!/usr/bin/env python3
"""kill15 v2/v3/v4 防塌方、惯性护栏与周期闭环自测：不调用模型、不写正式账本。

覆盖：
  v2 —— 禁止沿用上期组合、A 组上下限、B 子策略分散、池内选码、runaway/Hit-B 轮换、强制自检；
  v3 —— 完整方案（分配 + B 子策略映射）不得与最近 2 期重复；
  v4 —— 每 40 期周期软重置（跨轮清空胜率偏好/顺局连击）、B 子策略每期强制轮换、
        封存周期字段与策略字段、每 30 期抽样复盘核对（audit_settlements 纯函数）。

用法：python3 scripts/test_kill15_v2.py
"""
import copy
import sys
from itertools import combinations, permutations
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))

import kill15_tail  # noqa: E402
from stats_history import load_snapshot  # noqa: E402

KEYS = list(kill15_tail.POSITION_KEYS)
FAILED = []


def expect_raise(response, context, needle, label):
    try:
        kill15_tail.normalize_response(response, context)
    except ValueError as error:
        if needle not in str(error):
            raise AssertionError('%s: 错误信息不含 %r，实际: %s' % (label, needle, error))
        return
    raise AssertionError('%s: 应拒收但通过了' % label)


def load_rows():
    snapshot = load_snapshot(persist=False)
    rows = []
    for record in (snapshot.get('records') or []):
        numbers = list(record.get('numbers') or [])[:4]
        try:
            digits = [int(value) for value in numbers]
        except (TypeError, ValueError):
            continue
        if len(digits) == 4 and all(0 <= value <= 9 for value in digits):
            rows.append(digits)
    if len(rows) < 70:
        raise SystemExit('history too short: %d rows' % len(rows))
    return rows


def settlement(a_keys, b_keys, b_sub, hit=True, b_hit=None, scheme=None):
    positions = {}
    for key in KEYS:
        if key in b_keys:
            positions[key] = {'hit': (b_hit if b_hit is not None else hit)}
        else:
            positions[key] = {'hit': hit}
    group_hit = hit and (b_hit if b_hit is not None else hit)
    alloc = {'a': list(a_keys), 'b': list(b_keys), 'b_sub': dict(b_sub)}
    if scheme is not None:
        alloc = scheme
    return {'rule_version': kill15_tail.RULE_VERSION,
            'allocation': alloc, 'positions': positions, 'group_hit': group_hit}


def scheme_key(allocation):
    return kill15_tail._scheme_key(allocation)


def default_kills(context):
    """Pool-first kill digits; avoid copying last period's four kills exactly."""
    kills = {key: context['positions'][key]['pool'][0] for key in KEYS}
    last_kills = context.get('last_kills') or {}
    if len(last_kills) == len(KEYS) and all(str(kills[key]) == str(last_kills.get(key))
                                            for key in KEYS):
        pool = context['positions']['q']['pool']
        kills['q'] = pool[1] if len(pool) > 1 else pool[0]
    return kills


def make_valid(context):
    """Construct one response that should pass every v2 + v3 + v4 check."""
    last = context.get('last_allocation') or {}
    recent = context.get('recent_allocations') or []
    blocked = {scheme_key(alloc) for alloc in recent if isinstance(alloc, dict)}
    cap = int(context['a_cap'])
    last_a = set(str(key) for key in (last.get('a') or []))
    last_b = set(str(key) for key in (last.get('b') or []))
    last_sub = {str(key): str(value).upper() for key, value in (last.get('b_sub') or {}).items()}
    kills = default_kills(context)
    for combo in combinations(KEYS, cap):
        combo = list(combo)
        b_keys = [key for key in KEYS if key not in combo]
        if not b_keys:
            continue
        if set(combo) == last_a and set(b_keys) == last_b:
            continue
        for subs in permutations(kill15_tail.B_SUBS, len(b_keys)):
            allocation = {'a': combo, 'b': b_keys, 'b_sub': dict(zip(b_keys, subs))}
            if scheme_key(allocation) in blocked:
                continue
            if any(last_sub.get(key) == sub
                   for key, sub in zip(b_keys, subs) if key in last_sub):
                continue
            return {
                'allocation': allocation,
                'kills': dict(kills),
                'self_check': {key: True for key in kill15_tail.SELF_CHECK_KEYS},
                'reason': '自测：按本期指标重新推导',
            }
    raise AssertionError('cannot build a valid response for this context')


def out_of_pool(context, key):
    pool = set(int(digit) for digit in context['positions'][key]['pool'])
    for digit in range(10):
        if digit not in pool:
            return digit
    raise AssertionError('%s 位候选池覆盖了 0-9，无法构造池外码' % key)


def tamper_one_rotation(response, context):
    """Keep b_sub distinct but make one retained B position reuse last period's sub."""
    last_sub = (context.get('last_allocation') or {}).get('b_sub') or {}
    b_keys = list(response['allocation']['b'])
    retained = [key for key in b_keys if str(key) in {str(k) for k in last_sub}]
    if not retained:
        return False
    key = retained[0]
    last_value = {str(k): str(v).upper() for k, v in last_sub.items()}[str(key)]
    used = {last_value}
    for other in b_keys:
        if other == key:
            continue
        options = [s for s in kill15_tail.B_SUBS if s not in used]
        if not options:
            return False
        response['allocation']['b_sub'][other] = options[0]
        used.add(options[0])
    response['allocation']['b_sub'][key] = last_value
    return True


def review_fixture():
    """Three consistent settled periods for audit_settlements()."""
    plans = [
        (['q'], ['b', 's', 'g'], {'b': 'B1', 's': 'B2', 'g': 'B3'}),
        (['s'], ['q', 'b', 'g'], {'q': 'B1', 'b': 'B3', 'g': 'B2'}),
        (['g'], ['q', 'b', 's'], {'q': 'B2', 'b': 'B1', 's': 'B3'}),
    ]
    kills = {'q': 5, 'b': 6, 's': 7, 'g': 8}
    actual = [1, 2, 3, 4]
    seals, settlements, history = [], [], {}
    base = 20261112070
    for index, (a_keys, b_keys, subs) in enumerate(plans):
        ref = str(base + index)
        target = str(base + index + 1)
        allocation = {'a': list(a_keys), 'b': list(b_keys), 'b_sub': dict(subs)}
        positions, settle_positions = {}, {}
        hits = []
        for key, label, pos_index in kill15_tail.POSITIONS:
            hit = actual[pos_index] != kills[key]
            hits.append(hit)
            positions[key] = {'key': key, 'label': label, 'kill': kills[key],
                              'group': 'A' if key in a_keys else 'B',
                              'b_sub': subs.get(key), 'in_pool': True}
            settle_positions[key] = {'key': key, 'label': label, 'kill': kills[key],
                                     'actual': actual[pos_index], 'hit': hit,
                                     'group': 'A' if key in a_keys else 'B',
                                     'b_sub': subs.get(key)}
        seals.append({
            'version': 1, 'rule_version': kill15_tail.RULE_VERSION,
            'prompt_version': kill15_tail.PROMPT_VERSION,
            'ref_period': ref, 'target_period': target,
            'generated_at': '2026-11-12T06:00:00+08:00',
            'published_at': '2026-11-12T06:01:00+08:00',
            'publication_state': 'published',
            'allocation': allocation, 'positions': positions, 'a_cap': 1,
            'candidate_pools': {key: list(range(10)) for key in KEYS},
            'self_check': {key: True for key in kill15_tail.SELF_CHECK_KEYS},
            'policy': {'b_rotation_forced': True, 'cycle_periods': 40},
            'cycle': {'cycle_no': 0, 'cycle_pos': index + 1, 'cycle_periods': 40,
                      'is_cycle_start': index == 0},
        })
        settlements.append({
            'version': 1, 'rule_version': kill15_tail.RULE_VERSION,
            'ref_period': ref, 'target_period': target,
            'open_time': '2026-11-12T06:05:00+08:00',
            'settled_at': '2026-11-12T06:06:00+08:00',
            'allocation': allocation, 'positions': settle_positions,
            'position_hits': sum(1 for hit in hits if hit),
            'group_hit': all(hits),
            'expected_group_rate': kill15_tail.EXPECTED_GROUP_RATE, 'mode': 'panel',
        })
        history[target] = list(actual)
    return seals, settlements, history


def test_review_scheduler_catchup():
    """复盘调度：60 期补跑 bucket 1/2、幂等、90 期补 3、forced 单列（临时目录，不碰正式账本）。"""
    import json as _json
    import tempfile
    names = ('SEALS_PATH', 'SETTLEMENTS_PATH', 'REVIEWS_PATH', 'REVIEW_ALERTS_PATH',
             'audit_settlements')
    saved = {name: getattr(kill15_tail, name) for name in names}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kill15_tail.SEALS_PATH = root / 'seals.json'
            kill15_tail.SETTLEMENTS_PATH = root / 'settlements.json'
            kill15_tail.REVIEWS_PATH = root / 'reviews.json'
            kill15_tail.REVIEW_ALERTS_PATH = root / 'review_alerts.log'
            kill15_tail.audit_settlements = (
                lambda seals, settlements, history, **kwargs: {
                    'status': 'ok', 'rule_version': kill15_tail.RULE_VERSION,
                    'reviewed_periods': kwargs.get('sample') or 30,
                    'from_ref': 'x', 'to_target': 'y',
                    'discrepancies': [], 'warnings': [], 'rates': {}})

            def write_settlements(count):
                rows = [{'rule_version': kill15_tail.RULE_VERSION,
                         'ref_period': str(20261112000 + index),
                         'target_period': str(20261112001 + index),
                         'allocation': {}, 'positions': {}, 'group_hit': True}
                        for index in range(count)]
                kill15_tail.SETTLEMENTS_PATH.write_text(
                    _json.dumps({'version': 1, 'records': rows}), encoding='utf-8')

            write_settlements(60)
            records = kill15_tail.run_scheduled_review(snapshot={})
            if [row.get('review_bucket') for row in records] != [1, 2]:
                FAILED.append('复盘补跑 bucket 错误: %r'
                              % ([row.get('review_bucket') for row in records],))
            elif [row.get('trigger') for row in records] != ['scheduled', 'scheduled']:
                FAILED.append('复盘补跑 trigger 错误')
            elif kill15_tail.run_scheduled_review(snapshot={}):
                FAILED.append('复盘重复执行（幂等失败）')
            else:
                write_settlements(90)
                third = kill15_tail.run_scheduled_review(snapshot={})
                if [row.get('review_bucket') for row in third] != [3]:
                    FAILED.append('90 期未补 bucket 3: %r'
                                  % ([row.get('review_bucket') for row in third],))
                else:
                    forced = kill15_tail.run_scheduled_review(snapshot={}, force=True)
                    if not forced or forced[-1].get('trigger') != 'forced':
                        FAILED.append('forced 复盘未单列')
                    else:
                        print('PASS review-scheduler: 60 期补跑 bucket 1/2、幂等、90 期补 3、forced 单列')
    finally:
        for name, value in saved.items():
            setattr(kill15_tail, name, value)


def main():
    rows = load_rows()
    high_b = [settlement(['q'], ['b', 's', 'g'],
                         {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True)
              for _ in range(3)]
    low_b = [settlement(['q'], ['b', 's', 'g'],
                        {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True, b_hit=False)
             for _ in range(3)]

    # ---- 1. prompt v4 ----
    ctx = kill15_tail.build_context(rows, settlements=high_b)
    prompt, audit = kill15_tail.build_prompt('20261112064', '20261112065', rows, context=ctx)
    for needle in ('长期运行·防塌方核心规则', '禁止惯性复制上期方案', '前第2期方案',
                   '最近 2 期', '每期强制自检清单', 'allocation_rederived',
                   '候选不足', 'self_check', '顺局风控',
                   '本轮周期', 'B 子策略强制轮换', '周期纪律'):
        if needle not in prompt:
            FAILED.append('prompt 缺少 %r' % needle)
    if '%%' in prompt or '%s' in prompt:
        FAILED.append('prompt 残留未格式化占位符')
    if audit.get('version') != kill15_tail.PROMPT_VERSION:
        FAILED.append('audit.version 错误')
    if audit.get('scheme_repeat_window') != kill15_tail.SCHEME_REPEAT_WINDOW:
        FAILED.append('audit.scheme_repeat_window 错误')
    if audit.get('self_check_required') != list(kill15_tail.SELF_CHECK_KEYS):
        FAILED.append('audit.self_check_required 错误')
    if (audit.get('cycle') or {}).get('cycle_periods') != kill15_tail.CYCLE_PERIODS:
        FAILED.append('audit.cycle 错误')
    if audit.get('b_rotation_forced') is not True or audit.get('rotation_required') is not True:
        FAILED.append('audit.rotation_required/b_rotation_forced 错误')
    print('PASS prompt v4 (%d chars, version=%s, cycle=%s)'
          % (len(prompt), audit['version'], audit.get('cycle')))

    # ---- 2. 合法输出通过（v2+v3+v4 全部检查）----
    valid = make_valid(ctx)
    parsed = kill15_tail.normalize_response(valid, ctx)
    if parsed['self_check'].get('pool_confirmed') is not True:
        FAILED.append('normalize 未保留 self_check')
    for key in KEYS:
        if parsed['kills'][key] not in ctx['positions'][key]['pool']:
            FAILED.append('normalize 返回池外码')
    print('PASS valid response accepted (a=%s b=%s subs=%s)'
          % (parsed['allocation']['a'], parsed['allocation']['b'],
             parsed['allocation']['b_sub']))

    # ---- 3. v2 规则1：沿用上期组合 -> 拒收 ----
    last = ctx.get('last_allocation') or {}
    reused = copy.deepcopy(valid)
    reused['allocation'] = {'a': list(last.get('a') or []),
                            'b': list(last.get('b') or []),
                            'b_sub': dict(last.get('b_sub') or {})}
    expect_raise(reused, ctx, '禁止惯性复制上期方案', '规则1 沿用上期组合')
    print('PASS rule1: 沿用上期 A/B 组合被拒收')

    # ---- 4. v3 惯性护栏：与前第 2 期“完整方案”相同 -> 拒收 ----
    history3 = [
        settlement(['q'], ['b', 's', 'g'], {'b': 'B1', 's': 'B2', 'g': 'B3'}),
        settlement(['s'], ['q', 'b', 'g'], {'q': 'B2', 'b': 'B3', 'g': 'B1'}),
        settlement(['g'], ['q', 'b', 's'], {'q': 'B1', 'b': 'B2', 's': 'B3'}),
    ]
    ctx3 = kill15_tail.build_context(rows, settlements=history3)
    if len(ctx3.get('recent_allocations') or []) != kill15_tail.SCHEME_REPEAT_WINDOW:
        FAILED.append('recent_allocations 数量错误: %r' % (len(ctx3.get('recent_allocations') or []),))
    replay_two_ago = {
        'allocation': {'a': ['s'], 'b': ['q', 'b', 'g'],
                       'b_sub': {'q': 'B2', 'b': 'B3', 'g': 'B1'}},
        'kills': default_kills(ctx3),
        'self_check': {key: True for key in kill15_tail.SELF_CHECK_KEYS},
        'reason': '自测：照抄前第2期方案',
    }
    expect_raise(replay_two_ago, ctx3, '禁止惯性方案复用', 'v3 与近2期方案重复')
    print('PASS v3: 与前第2期完整方案相同被拒收（即使 A/B 分配与上期不同）')

    same_split_new_subs = {
        'allocation': {'a': ['s'], 'b': ['q', 'b', 'g'],
                       'b_sub': {'q': 'B2', 'b': 'B3', 'g': 'B1'}},
        'kills': default_kills(ctx3),
        'self_check': {key: True for key in kill15_tail.SELF_CHECK_KEYS},
        'reason': '自测：同分配但轮换子策略',
    }
    # 上期 B 子策略：q=B1/b=B2/s=B3（s 为 A，无子策略）；当前 q/b/g 均需与上期不同
    same_split_new_subs['allocation']['b_sub'] = {'q': 'B3', 'b': 'B1', 'g': 'B2'}
    kill15_tail.normalize_response(same_split_new_subs, ctx3)
    print('PASS v3/v4: 同一分配但 B 子策略已轮换 -> 放行（护栏只锁完整方案）')

    # ---- 5. v2 规则3：B 子策略重复 -> 拒收 ----
    dup = copy.deepcopy(valid)
    dup['allocation']['b_sub'] = {key: 'B1' for key in dup['allocation']['b']}
    expect_raise(dup, ctx, 'B 子策略不得重复', '规则3 B子策略重复')
    print('PASS rule3: B 子策略重复被拒收')

    # ---- 6. v2 规则4：A=0 / 超上限 -> 拒收 ----
    no_a = copy.deepcopy(valid)
    no_a['allocation'] = {'a': [], 'b': list(KEYS),
                          'b_sub': {key: kill15_tail.B_SUBS[index % 3]
                                    for index, key in enumerate(KEYS)}}
    expect_raise(no_a, ctx, 'A 组至少保留 1', '规则4 A=0')
    print('PASS rule4: A=0 被拒收')
    cap = int(ctx['a_cap'])
    if cap < len(KEYS) - 1:
        over = copy.deepcopy(valid)
        over_a = KEYS[:cap + 1]
        over_b = [key for key in KEYS if key not in over_a]
        over['allocation'] = {'a': over_a, 'b': over_b,
                              'b_sub': {key: kill15_tail.B_SUBS[index]
                                        for index, key in enumerate(over_b)}}
        expect_raise(over, ctx, '超过本期上限', 'A 组超上限')
        print('PASS rule4: A 组 %d 个超过上限 %d 被拒收' % (len(over_a), cap))

    # ---- 7. v2 规则5：池外杀码 -> 拒收 ----
    outside = copy.deepcopy(valid)
    outside['kills']['q'] = out_of_pool(ctx, 'q')
    expect_raise(outside, ctx, '不在候选池', '规则5 池外选码')
    print('PASS rule5: 池外杀码被拒收')

    # ---- 8. 强制自检清单：缺失 / false -> 拒收 ----
    missing = copy.deepcopy(valid)
    missing.pop('self_check')
    expect_raise(missing, ctx, '强制自检', '自检缺失')
    false_flag = copy.deepcopy(valid)
    false_flag['self_check']['allocation_rederived'] = False
    expect_raise(false_flag, ctx, '强制自检', '自检 false')
    print('PASS checklist: self_check 缺失/false 被拒收')

    # ---- 9. v4 强制轮换：非顺局、非低命中时也要轮换 ----
    ctx_high = kill15_tail.build_context(rows, settlements=high_b)
    shuffled = make_valid(ctx_high)
    if not tamper_one_rotation(shuffled, ctx_high):
        FAILED.append('v4 强制轮换构造失败：无保留的 B 号位可篡改')
    else:
        expect_raise(shuffled, ctx_high, 'B 子策略必须强制轮换', 'v4 每期强制轮换')
        print('PASS v4: 非顺局/非低命中同样强制 B 子策略轮换')

    # ---- 10. runaway 语义（仅“同分配且连续整组命中”累积）----
    runaway_pool = [settlement(['q'], ['b', 's', 'g'],
                               {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True)
                    for _ in range(kill15_tail.RUNAWAY_LIMIT)]
    state = kill15_tail.runaway_state(runaway_pool)
    if state['runaway_warning'] is not True:
        FAILED.append('runaway 语义失败：14 期同分配全中应触发，实际 %r' % (state,))
    broken = copy.deepcopy(runaway_pool)
    broken[-1]['group_hit'] = False
    if kill15_tail.runaway_state(broken)['runaway_warning'] is not False:
        FAILED.append('runaway 语义失败：出现未命中应中断连击')
    changed = copy.deepcopy(runaway_pool)
    changed[-1]['allocation'] = {'a': ['b'], 'b': ['q', 's', 'g'],
                                 'b_sub': {'q': 'B1', 's': 'B2', 'g': 'B3'}}
    if kill15_tail.runaway_state(changed)['runaway_warning'] is not False:
        FAILED.append('runaway 语义失败：分配变化应中断连击')
    print('PASS runaway 语义：仅“同分配且连续整组命中”才累积，任一中断即清零')

    ctx_run = kill15_tail.build_context(rows, settlements=runaway_pool)
    if ctx_run['runaway']['runaway_warning'] is not True:
        FAILED.append('runaway 构造失败: %r' % (ctx_run['runaway'],))
    else:
        shuffled = make_valid(ctx_run)
        if not tamper_one_rotation(shuffled, ctx_run):
            FAILED.append('runaway 构造失败：无保留的 B 号位可篡改')
        else:
            expect_raise(shuffled, ctx_run, 'B 子策略必须强制轮换', '规则2 runaway')
            print('PASS rule2: runaway=true 时 B 子策略未轮换被拒收')
        parsed_run = kill15_tail.normalize_response(make_valid(ctx_run), ctx_run)
        print('PASS rule2: runaway=true 且已打乱/轮换的合法输出可通过 (a=%s b=%s)'
              % (parsed_run['allocation']['a'], parsed_run['allocation']['b']))

    # ---- 11. 低 Hit-B 同样强制轮换 ----
    ctx_low = kill15_tail.build_context(rows, settlements=low_b)
    hit_b = ctx_low['performance']['hit_b_15']
    if isinstance(hit_b, (int, float)) and hit_b < 0.70:
        fake = make_valid(ctx_low)
        if not tamper_one_rotation(fake, ctx_low):
            FAILED.append('Hit-B<70% 构造失败：无保留的 B 号位可篡改')
        else:
            expect_raise(fake, ctx_low, 'B 子策略必须强制轮换', 'Hit-B<70% 轮换')
            print('PASS low Hit-B: 子策略未轮换被拒收 (hit_b=%.3f)' % hit_b)
    else:
        FAILED.append('Hit-B 构造失败: %r' % (hit_b,))

    # ---- 12. v4 周期软重置 ----
    empty_cycle = kill15_tail.cycle_state([])
    if not empty_cycle['is_cycle_start'] or empty_cycle['cycle_pos'] != 1:
        FAILED.append('cycle_state([]) 错误: %r' % (empty_cycle,))
    before = kill15_tail.cycle_state([{'rule_version': kill15_tail.RULE_VERSION}] * 39)
    if before['is_cycle_start'] or before['cycle_pos'] != 40 or before['cycle_no'] != 0:
        FAILED.append('cycle_state(39) 错误: %r' % (before,))
    reset = [settlement(['q'], ['b', 's', 'g'],
                        {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True)
             for _ in range(kill15_tail.CYCLE_PERIODS)]
    ctx_reset = kill15_tail.build_context(rows, settlements=reset)
    if not ctx_reset['cycle']['is_cycle_start'] or ctx_reset['cycle']['cycle_no'] != 1:
        FAILED.append('周期重置未触发: %r' % (ctx_reset['cycle'],))
    if ctx_reset['performance']['periods'] != 0 or ctx_reset['performance']['hit_a_15'] is not None:
        FAILED.append('周期重置未清空胜率偏好: %r' % (ctx_reset['performance'],))
    if ctx_reset['runaway']['runaway_warning'] is not False:
        FAILED.append('周期重置未清空顺局连击: %r' % (ctx_reset['runaway'],))
    prompt_reset, audit_reset = kill15_tail.build_prompt(
        '20261112064', '20261112065', rows, context=ctx_reset)
    if '周期软重置' not in prompt_reset:
        FAILED.append('周期起点 prompt 缺少“周期软重置”提示')
    print('PASS v4 cycle: 第 41 期进入第 2 轮，胜率偏好/顺局连击清零')

    # ---- 13. 封存字段 ----
    seal = kill15_tail.create_seal('20261112064', '20261112065', '2026-11-12T05:20:00+08:00',
                                   kill15_tail.normalize_response(valid, ctx), rows,
                                   context=ctx, audit=audit,
                                   extra={'mode': 'selftest'})
    if not seal.get('self_check') or not seal.get('policy'):
        FAILED.append('seal 缺 self_check/policy')
    if seal.get('prompt_version') != kill15_tail.PROMPT_VERSION:
        FAILED.append('seal prompt_version 错误')
    if (seal.get('policy') or {}).get('scheme_repeat_window') != kill15_tail.SCHEME_REPEAT_WINDOW:
        FAILED.append('seal policy.scheme_repeat_window 错误')
    if (seal.get('policy') or {}).get('b_rotation_forced') is not True:
        FAILED.append('seal policy.b_rotation_forced 缺失')
    if (seal.get('policy') or {}).get('cycle_periods') != kill15_tail.CYCLE_PERIODS:
        FAILED.append('seal policy.cycle_periods 缺失')
    if (seal.get('cycle') or {}).get('cycle_pos') != ctx['cycle']['cycle_pos']:
        FAILED.append('seal cycle 字段错误: %r' % (seal.get('cycle'),))
    print('PASS seal fields (prompt=%s, cycle=%s, b_rotation_forced=%s)'
          % (seal.get('prompt_version'), seal.get('cycle'),
             (seal.get('policy') or {}).get('b_rotation_forced')))

    # ---- 14. 抽样复盘：一致 -> ok；篡改 -> mismatch ----
    seals, settlements, history = review_fixture()
    good = kill15_tail.audit_settlements(seals, settlements, history)
    if good['status'] != 'ok' or good['discrepancies']:
        FAILED.append('复盘一致样本应为 ok: %r' % (good.get('discrepancies'),))
    else:
        print('PASS review: 3 期一致样本 -> ok（整组 %d/%d）'
              % (good['rates']['group']['hits'], good['rates']['group']['bets']))
    early = kill15_tail.audit_settlements(seals, settlements, history, upto=2)
    if early['reviewed_periods'] != 2 or early.get('to_target') != settlements[1].get('target_period'):
        FAILED.append('复盘 upto 窗口切片错误: %r' % (
            (early.get('reviewed_periods'), early.get('to_target')),))
    else:
        print('PASS review-upto: 补跑 bucket 时只核对前 upto 期（2 期）')
    tampered = copy.deepcopy(settlements)
    tampered[1]['positions']['q']['actual'] = 9
    bad_actual = kill15_tail.audit_settlements(seals, tampered, history)
    if bad_actual['status'] != 'mismatch' or not any(
            '台账开奖' in item for item in bad_actual['discrepancies']):
        FAILED.append('复盘应发现台账开奖被篡改: %r' % (bad_actual.get('discrepancies'),))
    else:
        print('PASS review: 篡改台账开奖 -> mismatch')
    bad_rotation = copy.deepcopy(seals)
    bad_rotation[2]['allocation']['b_sub']['q'] = 'B1'   # 与上一期 q 位相同
    bad_rotation[2]['positions']['q']['b_sub'] = 'B1'
    rotation_audit = kill15_tail.audit_settlements(
        bad_rotation, settlements, history)
    if not any('未强制轮换' in item for item in rotation_audit['discrepancies']):
        FAILED.append('复盘应发现 B 子策略未轮换: %r' % (rotation_audit.get('discrepancies'),))
    else:
        print('PASS review: B 子策略未轮换 -> mismatch')

    # ---- 15. 复盘调度：错过的 bucket 依次补跑、幂等、forced 不计合规 ----
    test_review_scheduler_catchup()

    print()
    if FAILED:
        print('SELFTEST FAILED: %d 项' % len(FAILED))
        for item in FAILED:
            print(' -', item)
        return 1
    print('SELFTEST OK: 全部通过')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print('SELFTEST CRASH: %s: %s' % (type(error).__name__, str(error)[:300]))
        raise SystemExit(1)
