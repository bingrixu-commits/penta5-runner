#!/usr/bin/env python3
"""kill15 v2 防塌方校验逻辑自测：不调用模型、不写正式账本。

用法：python3 scripts/test_kill15_v2.py
"""
import copy
import json
import sys
from itertools import combinations
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


def settlement(a_keys, b_keys, b_sub, hit=True, b_hit=None):
    positions = {}
    for key in KEYS:
        if key in b_keys:
            positions[key] = {'hit': (b_hit if b_hit is not None else hit)}
        else:
            positions[key] = {'hit': hit}
    group_hit = hit and (b_hit if b_hit is not None else hit)
    return {'allocation': {'a': list(a_keys), 'b': list(b_keys), 'b_sub': dict(b_sub)},
            'positions': positions,
            'group_hit': group_hit}


def make_valid(context):
    """Construct one response that should pass every v2 check."""
    last = context.get('last_allocation') or {}
    cap = int(context['a_cap'])
    last_a = set(last.get('a') or [])
    last_b = set(last.get('b') or [])
    chosen_a = chosen_b = None
    for combo in combinations(KEYS, cap):
        combo = list(combo)
        b_keys = [key for key in KEYS if key not in combo]
        if not b_keys:
            continue
        if set(combo) == last_a and set(b_keys) == last_b:
            continue
        chosen_a, chosen_b = combo, b_keys
        break
    if chosen_a is None:
        raise AssertionError('cannot build allocation')
    last_sub = {str(key): str(value) for key, value in (last.get('b_sub') or {}).items()}
    b_sub = {}
    used = set()
    for key in chosen_b:
        options = [s for s in kill15_tail.B_SUBS
                   if s != last_sub.get(key) and s not in used]
        if not options:
            options = [s for s in kill15_tail.B_SUBS if s not in used]
        if not options:
            raise AssertionError('cannot assign b_sub')
        b_sub[key] = options[0]
        used.add(options[0])
    return {
        'allocation': {'a': chosen_a, 'b': chosen_b, 'b_sub': b_sub},
        'kills': {key: context['positions'][key]['pool'][0] for key in KEYS},
        'self_check': {key: True for key in kill15_tail.SELF_CHECK_KEYS},
        'reason': '自测：按本期指标错落分配',
    }


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
    last_value = {str(k): str(v) for k, v in last_sub.items()}[str(key)]
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
    low_b = [settlement(['q'], ['b', 's', 'g'],
                        {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True, b_hit=False)
             for _ in range(3)]
    high_b = [settlement(['q'], ['b', 's', 'g'],
                         {'b': 'B1', 's': 'B2', 'g': 'B3'}, hit=True)
              for _ in range(3)]

    # ---- 1. prompt v2 ----
    ctx = kill15_tail.build_context(rows, settlements=high_b)
    prompt, audit = kill15_tail.build_prompt('20261112062', '20261112063', rows, context=ctx)
    for needle in ('长期运行·防塌方核心规则', '禁止惯性复制上期方案', '顺局风控',
                   '每期强制自检清单', 'allocation_rederived', '候选不足', 'self_check'):
        if needle not in prompt:
            FAILED.append('prompt 缺少 %r' % needle)
    if '%%' in prompt or '%s' in prompt:
        FAILED.append('prompt 残留未格式化占位符')
    if audit.get('version') != kill15_tail.PROMPT_VERSION:
        FAILED.append('audit.version 错误')
    if audit.get('self_check_required') != list(kill15_tail.SELF_CHECK_KEYS):
        FAILED.append('audit.self_check_required 错误')
    print('PASS prompt v2 (%d chars, version=%s)' % (len(prompt), audit['version']))

    # ---- 2. 合法输出通过 ----
    valid = make_valid(ctx)
    parsed = kill15_tail.normalize_response(valid, ctx)
    if parsed['self_check'].get('pool_confirmed') is not True:
        FAILED.append('normalize 未保留 self_check')
    for key in KEYS:
        if parsed['kills'][key] not in ctx['positions'][key]['pool']:
            FAILED.append('normalize 返回池外码')
    print('PASS valid response accepted (a=%s b=%s)' % (parsed['allocation']['a'],
                                                        parsed['allocation']['b']))

    # ---- 3. 规则1：沿用上期组合 -> 拒收 ----
    last = ctx.get('last_allocation') or {}
    reused = copy.deepcopy(valid)
    reused['allocation'] = {'a': list(last.get('a') or []),
                            'b': list(last.get('b') or []),
                            'b_sub': dict(last.get('b_sub') or {})}
    expect_raise(reused, ctx, '禁止惯性复制上期方案', '规则1 沿用上期组合')
    print('PASS rule1: 沿用上期 A/B 组合被拒收')

    # ---- 4. 规则3：B 子策略重复 -> 拒收 ----
    dup = copy.deepcopy(valid)
    b_keys = dup['allocation']['b']
    dup['allocation']['b_sub'] = {key: 'B1' for key in b_keys}
    expect_raise(dup, ctx, 'B 子策略不得重复', '规则3 B子策略重复')
    print('PASS rule3: B 子策略重复被拒收')

    # ---- 5. 规则4：A=0 -> 拒收 / 超上限 -> 拒收 ----
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

    # ---- 6. 规则5：池外杀码 -> 拒收 ----
    outside = copy.deepcopy(valid)
    outside['kills']['q'] = out_of_pool(ctx, 'q')
    expect_raise(outside, ctx, '不在候选池', '规则5 池外选码')
    print('PASS rule5: 池外杀码被拒收')

    # ---- 7. 强制自检清单：缺失 / false -> 拒收 ----
    missing = copy.deepcopy(valid)
    missing.pop('self_check')
    expect_raise(missing, ctx, '强制自检', '自检缺失')
    false_flag = copy.deepcopy(valid)
    false_flag['self_check']['pool_confirmed'] = False
    expect_raise(false_flag, ctx, '强制自检', '自检 false')
    print('PASS checklist: self_check 缺失/false 被拒收')

    # ---- 8. 规则2：runaway=true 时 B 子策略必须轮换 ----
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

    # ---- 9. 规则2 扩展：Hit-B<70% 时同样强制轮换 ----
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

    # ---- 10. 封存字段 ----
    seal = kill15_tail.create_seal('20261112062', '20261112063', '2026-11-12T05:10:00+08:00',
                                   kill15_tail.normalize_response(valid, ctx), rows,
                                   context=ctx, audit=audit,
                                   extra={'mode': 'selftest'})
    if not seal.get('self_check') or not seal.get('policy'):
        FAILED.append('seal 缺 self_check/policy')
    if seal.get('prompt_version') != kill15_tail.PROMPT_VERSION:
        FAILED.append('seal prompt_version 错误')
    print('PASS seal fields (prompt_version=%s)' % seal.get('prompt_version'))

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
