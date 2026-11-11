#!/usr/bin/env python3
"""kill15 v2/v3 防塌方与惯性护栏自测：不调用模型、不写正式账本。

覆盖：
  v2 —— 禁止沿用上期组合、A 组上下限、B 子策略分散、池内选码、runaway/Hit-B 轮换、强制自检；
  v3 —— 完整方案（分配 + B 子策略映射）不得与最近 2 期重复。

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
    return {'allocation': alloc, 'positions': positions, 'group_hit': group_hit}


def scheme_key(allocation):
    return kill15_tail._scheme_key(allocation)


def make_valid(context):
    """Construct one response that should pass every v2 + v3 check."""
    last = context.get('last_allocation') or {}
    recent = context.get('recent_allocations') or []
    blocked = {scheme_key(alloc) for alloc in recent if isinstance(alloc, dict)}
    cap = int(context['a_cap'])
    last_a = set(str(key) for key in (last.get('a') or []))
    last_b = set(str(key) for key in (last.get('b') or []))
    last_sub = {str(key): str(value).upper() for key, value in (last.get('b_sub') or {}).items()}
    perf = context.get('performance') or {}
    hit_b = perf.get('hit_b_15')
    runaway = context.get('runaway') or {}
    rotation = ((isinstance(hit_b, (int, float)) and hit_b < 0.70)
                or runaway.get('runaway_warning') is True)
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
            if rotation and any(last_sub.get(key) == sub
                                for key, sub in zip(b_keys, subs) if key in last_sub):
                continue
            return {
                'allocation': allocation,
                'kills': {key: context['positions'][key]['pool'][0] for key in KEYS},
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


def main():
    rows = load_rows()
    high_b = [settlement(['q'], ['b', 's', 'g'],
                         {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True)
              for _ in range(3)]
    low_b = [settlement(['q'], ['b', 's', 'g'],
                        {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True, b_hit=False)
             for _ in range(3)]

    # ---- 1. prompt v3 ----
    ctx = kill15_tail.build_context(rows, settlements=high_b)
    prompt, audit = kill15_tail.build_prompt('20261112064', '20261112065', rows, context=ctx)
    for needle in ('长期运行·防塌方核心规则', '禁止惯性复制上期方案', '前第2期方案',
                   '最近 2 期', '每期强制自检清单', 'allocation_rederived',
                   '候选不足', 'self_check', '顺局风控'):
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
    print('PASS prompt v3 (%d chars, version=%s, recent_schemes=%d)'
          % (len(prompt), audit['version'], len(audit.get('recent_schemes') or [])))

    # ---- 2. 合法输出通过（v2+v3 全部检查）----
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
        'kills': {key: ctx3['positions'][key]['pool'][0] for key in KEYS},
        'self_check': {key: True for key in kill15_tail.SELF_CHECK_KEYS},
        'reason': '自测：照抄前第2期方案',
    }
    expect_raise(replay_two_ago, ctx3, '禁止惯性方案复用', 'v3 与近2期方案重复')
    print('PASS v3: 与前第2期完整方案相同被拒收（即使 A/B 分配与上期不同）')

    same_split_new_subs = copy.deepcopy(replay_two_ago)
    same_split_new_subs['allocation']['b_sub'] = {'q': 'B1', 'b': 'B2', 'g': 'B3'}
    kill15_tail.normalize_response(same_split_new_subs, ctx3)
    print('PASS v3: 同一分配但 B 子策略已轮换 -> 放行（护栏只锁完整方案）')

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

    # ---- 9. v2 规则2：runaway=true 时 B 子策略必须轮换 ----
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
            expect_raise(shuffled, ctx_run, 'B 子策略必须轮换', '规则2 runaway')
            print('PASS rule2: runaway=true 时 B 子策略未轮换被拒收')
        parsed_run = kill15_tail.normalize_response(make_valid(ctx_run), ctx_run)
        print('PASS rule2: runaway=true 且已打乱/轮换的合法输出可通过 (a=%s b=%s)'
              % (parsed_run['allocation']['a'], parsed_run['allocation']['b']))

    # ---- 10. 规则2 扩展：Hit-B<70% 时同样强制轮换 ----
    ctx_low = kill15_tail.build_context(rows, settlements=low_b)
    hit_b = ctx_low['performance']['hit_b_15']
    if isinstance(hit_b, (int, float)) and hit_b < 0.70:
        fake = make_valid(ctx_low)
        if not tamper_one_rotation(fake, ctx_low):
            FAILED.append('Hit-B<70% 构造失败：无保留的 B 号位可篡改')
        else:
            expect_raise(fake, ctx_low, 'B 子策略必须轮换', 'Hit-B<70% 轮换')
            print('PASS low Hit-B: 子策略未轮换被拒收 (hit_b=%.3f)' % hit_b)
    else:
        FAILED.append('Hit-B 构造失败: %r' % (hit_b,))

    # ---- 11. 封存字段 ----
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
    print('PASS seal fields (prompt_version=%s, policy.scheme_repeat_window=%s)'
          % (seal.get('prompt_version'), (seal.get('policy') or {}).get('scheme_repeat_window')))

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
