#!/usr/bin/env python3
"""KILL_THREE 依据/命中复核（只读）：位置依据标签、hold 采纳、分项与整组命中。

用法：python3 scripts/kill3_v15_review.py [--rule v15]
不写任何数据、不改面板、不调模型；路径按仓库根自动推导，可从任意 cwd 运行。
"""
import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, 'lib'))

DATA = os.path.join(ROOT, 'data', 'kill3_tail') + os.sep


def load(name):
    raw = json.load(open(DATA + name, encoding='utf-8'))
    return raw if isinstance(raw, list) else raw.get('records', [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rule', default='v15', help='rule_version 子串过滤，如 v15')
    args = ap.parse_args()
    seals = [r for r in load('seals.json') if args.rule in str(r.get('rule_version') or '')]
    setts = [r for r in load('settlements.json') if args.rule in str(r.get('rule_version') or '')]
    print('== KILL_THREE %s ==' % args.rule)
    print('封存 %d 期 / 已结算 %d 期' % (len(seals), len(setts)))
    if not seals:
        return
    basis = Counter()
    hold_by_pos = Counter()
    kills = 0
    for seal in seals:
        used = seal.get('position_basis_used') or {}
        hold = seal.get('hold_adopted') or []
        for key, tags in used.items():
            kills += 1
            for tag in tags or ['other']:
                basis[tag] += 1
        for key in hold:
            hold_by_pos[key] += 1
    print('\n-- 计入位置杀码依据（按标签计，一次选择可多标签）--')
    for tag, n in basis.most_common():
        print('  %-24s %3d  (%.1f%%)' % (tag, n, 100.0 * n / max(1, kills)))
    print('  合计标签 %d / 杀码 %d' % (sum(basis.values()), kills))
    print('\n-- 模型采纳 hold（沿用上期同位杀码）--')
    print('  采纳期数 %d / %d  位置次数 %s' % (
        sum(1 for s in seals if s.get('hold_adopted')),
        len(seals), dict(hold_by_pos) or '{}'))

    if setts:
        def rate(items, key_fn):
            hit = sum(1 for r in items if key_fn(r))
            return hit, len(items), (100.0 * hit / len(items)) if items else 0.0
        pos = []
        for r in setts:
            for key in (r.get('counted') or []):
                item = (r.get('positions') or {}).get(key) or {}
                pos.append(item.get('hit'))
        pos_hit = sum(1 for v in pos if v)
        tail = [bool((r.get('tail') or {}).get('hit')) for r in setts]
        grp = [bool(r.get('group_hit')) for r in setts]
        print('\n-- 命中 --')
        print('  位置 %d/%d = %.2f%%' % (pos_hit, len(pos), 100.0 * pos_hit / max(1, len(pos))))
        print('  合尾 %d/%d = %.2f%%' % (sum(tail), len(tail), 100.0 * sum(tail) / max(1, len(tail))))
        print('  整组 %d/%d = %.2f%%' % (sum(grp), len(grp), 100.0 * sum(grp) / max(1, len(grp))))
        seal_by_target = {str(s.get('target_period')): s for s in seals}
        print('\n-- 逐期 --')
        for r in setts:
            src_row = seal_by_target.get(str(r.get('target_period'))) or {}
            used = r.get('position_basis_used') or src_row.get('position_basis_used') or {}
            hold = r.get('hold_adopted')
            if hold is None:
                hold = src_row.get('hold_adopted')
            print('  %s->%s act=%s counted=%s pos=%s tail=%s grp=%s basis=%s hold=%s' % (
                r.get('ref_period'), r.get('target_period'), ''.join(r.get('actual') or []),
                r.get('counted'),
                ''.join('1' if (r.get('positions') or {}).get(k, {}).get('hit') else '0'
                        for k in (r.get('counted') or [])),
                '1' if (r.get('tail') or {}).get('hit') else '0',
                '1' if r.get('group_hit') else '0',
                ','.join('%s:%s' % (k, '/'.join(v)) for k, v in used.items()),
                hold))
        if len(setts) < 20:
            print('\n提示：样本 %d 期 < 20，仅作走势参考，不做结论。' % len(setts))


if __name__ == '__main__':
    main()
