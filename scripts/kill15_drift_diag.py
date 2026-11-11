#!/usr/bin/env python3
"""kill15 近期『四码漂移』诊断：命中显著性 / 码位变化率 / 子策略语义符合度。只读。"""
import json
import math
import sys
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'scripts'))

import kill15_tail
from stats_history import load_snapshot

KEYS = list(kill15_tail.POSITION_KEYS)
LABELS = kill15_tail.POSITION_LABELS
RULE = kill15_tail.RULE_VERSION
BASELINE_GROUP = 0.6561
BASELINE_SINGLE = 0.9
DEEP_COLD = kill15_tail.DEEP_COLD_OMISSION


def comb_tail(k, n, p):
    """P(X <= k) for binomial(n,p)."""
    if k < 0:
        return 0.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def load_snapshot_rows():
    snapshot = load_snapshot(persist=False)
    periods, rows = [], []
    for record in snapshot.get('records') or []:
        try:
            digits = [int(v) for v in (record.get('numbers') or [])[:4]]
        except (TypeError, ValueError):
            continue
        if len(digits) == 4:
            periods.append(str(record.get('period')))
            rows.append(digits)
    return periods, rows


def omission_before(rows, end_index, position, digit):
    gap = end_index + 1
    for offset in range(1, end_index + 2):
        if rows[end_index + 1 - offset][position] == digit:
            gap = offset - 1
            break
    return gap


def b2_candidates(rows, ref_index, position):
    found = set()
    for draw_index in range(max(0, ref_index - 2), ref_index + 1):
        digit = rows[draw_index][position]
        gap = omission_before(rows, draw_index - 1, position, digit)
        if gap >= DEEP_COLD:
            found.add(digit)
    return found


def side_is_odd(digit):
    return digit % 2 == 1


def side_is_big(digit):
    return digit >= 5


def _value(mapping, digit):
    """omission/hot 映射既可能是 int 键，也可能是 str 键。"""
    if digit in mapping:
        return mapping[digit]
    return mapping.get(str(digit), 0)


def b3_candidate(pool, omission, rows, ref_index, position):
    window = rows[max(0, ref_index - 29):ref_index + 1]
    odd = sum(1 for row in window if row[position] % 2 == 1)
    even = len(window) - odd
    big = sum(1 for row in window if row[position] >= 5)
    small = len(window) - big
    parity_reverse = even <= odd          # 反向侧 = 少的那一侧
    size_reverse = small <= big
    # 取失衡更强的一维作反向侧
    parity_gap = abs(odd - even) / float(max(1, len(window)))
    size_gap = abs(big - small) / float(max(1, len(window)))
    candidates = []
    for digit in pool:
        if parity_gap >= size_gap:
            keep = (side_is_odd(digit) != parity_reverse)
        else:
            keep = (side_is_big(digit) != size_reverse)
        if keep:
            candidates.append(digit)
    if not candidates:
        candidates = list(pool)
    return max(candidates, key=lambda d: (_value(omission, d), -d))


def max_omission_candidate(pool, omission):
    return max(pool, key=lambda d: (_value(omission, d), -d))


def hottest_candidate(pool, hot):
    return max(pool, key=lambda d: (_value(hot, d), -d))


def main():
    periods, rows = load_snapshot_rows()
    index_by_period = {period: index for index, period in enumerate(periods)}
    seals = kill15_tail.load_seals()
    settlements = kill15_tail.load_settlements()
    settled_targets = {str(row.get('target_period')): row for row in settlements}

    # ---------- 1. 命中率显著性 ----------
    v4 = [s for s in seals if s.get('rule_version') == RULE]
    v3 = [s for s in seals if s.get('rule_version') == 'kill15-tail-v3-inertia-guard']
    v4_settled = []
    for seal in v4:
        row = settled_targets.get(str(seal.get('target_period')))
        if row:
            v4_settled.append((seal, row))
    n = len(v4_settled)
    group_hits = sum(1 for _, row in v4_settled if row.get('group_hit'))
    position_bets = position_hits = 0
    for _, row in v4_settled:
        for item in (row.get('positions') or {}).values():
            position_bets += 1
            if item.get('hit'):
                position_hits += 1
    print('=== 1) v4 命中率（%d 期已结算）===' % n)
    if n:
        print('整组 %d/%d = %.1f%%（随机基线 65.61%%，二项 P(X<=%d) = %.3f）'
              % (group_hits, n, 100.0 * group_hits / n, group_hits,
                 comb_tail(group_hits, n, BASELINE_GROUP)))
        print('单码 %d/%d = %.1f%%（随机基线 90%%，二项 P(X<=%d) = %.3f）'
              % (position_hits, position_bets, 100.0 * position_hits / position_bets,
                 position_hits, comb_tail(position_hits, position_bets, BASELINE_SINGLE)))
    window5 = v4_settled[-5:]
    g5 = sum(1 for _, row in window5 if row.get('group_hit'))
    killed5 = sum(1 for _, row in window5 for item in (row.get('positions') or {}).values()
                  if not item.get('hit'))
    window10 = v4_settled[-10:]
    g10 = sum(1 for _, row in window10 if row.get('group_hit'))
    print('最近 5 期整组 %d/5；近 5 期被杀中码位 %d/%d（随机期望 2，P(X>=%d)=%.3f）'
          % (g5, killed5, len(window5) * 4, killed5,
             1 - comb_tail(killed5 - 1, len(window5) * 4, 0.1)))
    print('最近 10 期整组 %d/10 = %.1f%%' % (g10, 100.0 * g10 / max(1, len(window10))))

    # ---------- 2. 四码变化率（漂移 churn）----------
    def churn(seal_rows):
        changes = []
        all4 = 0
        for prev, cur in zip(seal_rows, seal_rows[1:]):
            kills_prev = prev.get('positions') or {}
            kills_cur = cur.get('positions') or {}
            changed = sum(1 for key in KEYS
                          if str((kills_prev.get(key) or {}).get('kill'))
                          != str((kills_cur.get(key) or {}).get('kill')))
            changes.append(changed)
            if changed == 4:
                all4 += 1
        return changes, all4

    v4_changes, v4_all4 = churn([seal for seal, _ in v4_settled])
    v3_settled = []
    for seal in v3:
        row = settled_targets.get(str(seal.get('target_period')))
        if row:
            v3_settled.append(seal)
    v3_changes, v3_all4 = churn(v3_settled)
    print('\n=== 2) 四码逐期变化（churn）===')
    if v4_changes:
        print('v4：平均每期换 %.1f/4 码，整组 4 码全换 %d/%d 期（%.0f%%）'
              % (sum(v4_changes) / float(len(v4_changes)), v4_all4, len(v4_changes),
                 100.0 * v4_all4 / len(v4_changes)))
    if v3_changes:
        print('v3：平均每期换 %.1f/4 码，整组 4 码全换 %d/%d 期（%.0f%%）'
              % (sum(v3_changes) / float(len(v3_changes)), v3_all4, len(v3_changes),
                 100.0 * v3_all4 / len(v3_changes)))

    # ---------- 3. 子策略语义符合度 ----------
    print('\n=== 3) 子策略语义符合度（A=池内遗漏最大；B1=最热；B2=近3期深冷刚出；B3=形态反向）===')
    stats = {key: {'total': 0, 'match': 0} for key in ('A', 'B1', 'B2', 'B3')}
    chosen_hits = {key: {'total': 0, 'hit': 0} for key in ('A', 'B1', 'B2', 'B3')}
    expected_hits = {key: {'total': 0, 'hit': 0} for key in ('A', 'B1', 'B2', 'B3')}
    latest_rows = []
    detail_lines = []
    for seal, row in v4_settled[-8:]:
        ref = str(seal.get('ref_period'))
        ref_index = index_by_period.get(ref)
        if ref_index is None:
            continue
        rows_before = rows[:ref_index + 1]
        pools = seal.get('candidate_pools') or {}
        allocation = seal.get('allocation') or {}
        subs = allocation.get('b_sub') or {}
        line = [seal.get('target_period')]
        detail = []
        for key in KEYS:
            position = kill15_tail.POSITION_INDEX[key]
            item = (seal.get('positions') or {}).get(key) or {}
            kill = item.get('kill')
            omission = kill15_tail.omission_map(rows_before, position)
            hot = kill15_tail.hot_map(rows_before, position)
            pool = [int(d) for d in (pools.get(key) or [])]
            if not pool or kill is None:
                continue
            if key in (allocation.get('a') or []):
                strat = 'A'
                expected = max_omission_candidate(pool, omission)
            else:
                strat = str(subs.get(key) or 'B?')
                if strat == 'B1':
                    expected = hottest_candidate(pool, hot)
                elif strat == 'B2':
                    candidates = b2_candidates(rows, ref_index, position)
                    expected = max(candidates & set(pool), key=lambda d: (_value(omission, d), -d)) \
                        if (candidates & set(pool)) else None
                else:
                    expected = b3_candidate(pool, omission, rows, ref_index, position)
            actual = (row.get('positions') or {}).get(key, {}).get('actual')
            if strat in stats:
                stats[strat]['total'] += 1
                matched = expected is not None and int(kill) == int(expected)
                stats[strat]['match'] += 1 if matched else 0
                chosen_hits[strat]['total'] += 1
                chosen_hits[strat]['hit'] += 1 if item.get('in_pool') and row['positions'][key].get('hit') else 0
                if expected is not None:
                    expected_hits[strat]['total'] += 1
                    expected_hits[strat]['hit'] += 1 if actual != expected else 0
                detail.append('%s%s杀%d(定义%s)实%s%s'
                              % (LABELS[key], strat, kill, expected, actual,
                                 '✓' if row['positions'][key].get('hit') else '✗'))
            line.append('%s%s杀%d%s' % (LABELS[key], strat, kill,
                                        '' if item.get('in_pool') else '!'))
        latest_rows.append(' '.join(line))
        detail_lines.append('%s->%s | %s' % (seal.get('ref_period'),
                                             seal.get('target_period'),
                                             ' | '.join(detail)))

    for strat in ('A', 'B1', 'B2', 'B3'):
        total = stats[strat]['total']
        match = stats[strat]['match']
        chosen = chosen_hits[strat]
        expected = expected_hits[strat]
        print('%-3s 语义符合 %2d/%2d = %5.1f%% | 模型实选命中 %2d/%2d = %5.1f%% '
              '| 若按定义选可中 %2d/%2d = %5.1f%%'
              % (strat, match, total, 100.0 * match / total if total else 0,
                 chosen['hit'], chosen['total'],
                 100.0 * chosen['hit'] / chosen['total'] if chosen['total'] else 0,
                 expected['hit'], expected['total'],
                 100.0 * expected['hit'] / expected['total'] if expected['total'] else 0))

    # ---------- 4. 追号/惯性检查 ----------
    print('\n=== 4) 与上期开奖/上期杀码的重合（追热 or 惯性）===')
    same_as_prev_draw = same_as_prev_kill = 0
    pairs = 0
    for prev, cur in zip(v4_settled, v4_settled[1:]):
        prev_seal, prev_row = prev
        cur_seal, cur_row = cur
        pairs += 4
        for key in KEYS:
            cur_item = (cur_seal.get('positions') or {}).get(key) or {}
            prev_actual = ((prev_row.get('positions') or {}).get(key) or {}).get('actual')
            prev_kill = ((prev_seal.get('positions') or {}).get(key) or {}).get('kill')
            if cur_item.get('kill') is not None and str(cur_item.get('kill')) == str(prev_actual):
                same_as_prev_draw += 1
            if cur_item.get('kill') is not None and str(cur_item.get('kill')) == str(prev_kill):
                same_as_prev_kill += 1
    print('新杀码 = 上期开奖号（同号位）：%d/%d = %.1f%%（随机期望 ~10%%）'
          % (same_as_prev_draw, pairs, 100.0 * same_as_prev_draw / pairs if pairs else 0))
    print('新杀码 = 上期杀码（同号位）：%d/%d = %.1f%%（v4 强制 B 轮换应接近 0）'
          % (same_as_prev_kill, pairs, 100.0 * same_as_prev_kill / pairs if pairs else 0))

    # ---------- 5. 最近 8 期明细 ----------
    print('\n=== 5a) 最近 8 期语义对照（杀 / 定义应选 / 实际）===')
    for line in detail_lines:
        print(' ', line)
    print('\n=== 5b) 最近 8 期命中明细（目标期 / 整组 / 四码）===')
    for seal, row in v4_settled[-8:]:
        pos = row.get('positions') or {}
        detail = ' '.join('%s%s%d→%d%s' % (
            LABELS[key], '' if key in (seal.get('allocation') or {}).get('a') or [] else 'B',
            (pos.get(key) or {}).get('kill'), (pos.get(key) or {}).get('actual'),
            '✓' if (pos.get(key) or {}).get('hit') else '✗') for key in KEYS)
        print(' %s -> %s | %s | %s'
              % (seal.get('ref_period'), seal.get('target_period'),
                 'HIT' if row.get('group_hit') else 'miss', detail))

    # ---------- 6. prompt 是否下发 B1/B2/B3 定义 ----------
    print('\n=== 6) prompt 子策略定义检查 ===')
    if len(rows) >= 70:
        try:
            context = kill15_tail.build_context(rows)
            prompt, _audit = kill15_tail.build_prompt(periods[-1], 'x', rows, context=context)
            for needle in ('B1', 'B2', 'B3', '热码', '反弹', '形态', '遗漏最大'):
                print('  prompt 含 %r: %s' % (needle, needle in prompt))
        except Exception as exc:
            print('  prompt 检查失败:', exc)


if __name__ == '__main__':
    main()
