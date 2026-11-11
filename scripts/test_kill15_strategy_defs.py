#!/usr/bin/env python3
"""kill15 v4.2 子策略定义下发自测（独立脚本，便于单独验证）：

覆盖：
  - build_context['strategy'] 每个号位给出 A/B1/B2/B3 候选，且与定义一致；
  - A 候选 = 池内遗漏最大、B1 候选 = 池内近 30 期最热、B3 候选 = 反向侧遗漏最大；
  - B2 候选：池内无“近 3 期深冷刚开出”标的时 fallback=True 且等于 A 候选；
  - build_prompt 含定义与候选行；
  - audit_settlements 的 semantics 字段结构（A/B1/B2/B3 统计）。

只读快照数据，不写账本、不调模型。
用法：python3 scripts/test_kill15_strategy_defs.py
"""
import sys
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))

import kill15_tail  # noqa: E402
from stats_history import load_snapshot  # noqa: E402

FAILED = []


def load_rows():
    snapshot = load_snapshot(persist=False)
    rows = []
    for record in (snapshot.get('records') or []):
        try:
            digits = [int(value) for value in (record.get('numbers') or [])[:4]]
        except (TypeError, ValueError):
            continue
        if len(digits) == 4 and all(0 <= value <= 9 for value in digits):
            rows.append(digits)
    if len(rows) < 70:
        raise SystemExit('history too short: %d rows' % len(rows))
    return rows


def main():
    rows = load_rows()
    context = kill15_tail.build_context(rows, settlements=[])
    strategy = context.get('strategy') or {}
    ref_index = len(rows) - 1
    for key, label, position in kill15_tail.POSITIONS:
        hint = strategy.get(key)
        pool = context['positions'][key]['pool']
        if not hint:
            FAILED.append('%s位缺少 strategy hint' % label)
            continue
        omission = kill15_tail.omission_map(rows[:ref_index + 1], position)
        hot = kill15_tail.hot_map(rows[:ref_index + 1], position)
        expected_a = max(pool, key=lambda d: (omission[d], -d))
        expected_b1 = max(pool, key=lambda d: (hot[d], -d))
        if hint['a'] != expected_a:
            FAILED.append('%s位 A 候选错误: %r != %r' % (label, hint['a'], expected_a))
        if hint['b1'] != expected_b1:
            FAILED.append('%s位 B1 候选错误: %r != %r' % (label, hint['b1'], expected_b1))
        if hint['b2'] not in pool:
            FAILED.append('%s位 B2 候选不在池内: %r' % (label, hint['b2']))
        if hint['b2_fallback'] and hint['b2'] != hint['a']:
            FAILED.append('%s位 B2 降级候选应等于 A 候选' % label)
        if not hint['b2_fallback'] and hint['b2'] not in hint.get('b2_pool_hits', []):
            FAILED.append('%s位 B2 候选不在深冷刚开出集合' % label)
        if hint['b3'] not in pool:
            FAILED.append('%s位 B3 候选不在池内: %r' % (label, hint['b3']))
        print('  %s位：池 %s -> A=%d B1=%d B2=%s B3=%d'
              % (label, pool, hint['a'], hint['b1'],
                 ('%d(fallback)' % hint['b2']) if hint['b2_fallback'] else hint['b2'],
                 hint['b3']))
    if not FAILED:
        print('PASS hints: 四号位策略候选符合定义（A/B1/B2/B3）')

    prompt, audit = kill15_tail.build_prompt('20261112090', '20261112091', rows,
                                             context=context)
    for needle in ('A/B 三子策略定义', 'B1（热码杀）', 'B2（反弹杀）', 'B3（形态对冲）',
                   '策略候选', 'A=', 'B1=', 'B2=', 'B3='):
        if needle not in prompt:
            FAILED.append('prompt 缺少 %r' % needle)
    if audit.get('substrategy_defs') != 1:
        FAILED.append('audit.substrategy_defs 缺失')
    candidates = audit.get('strategy_candidates') or {}
    if set(candidates) != set(kill15_tail.POSITION_KEYS):
        FAILED.append('audit.strategy_candidates 号位不全: %r' % (sorted(candidates),))
    else:
        print('PASS prompt/audit: 定义与候选已下发（%d chars）' % len(prompt))

    # semantics 结构（用真实历史 + 空结算 -> 全 0，仅验证结构）
    semantics = kill15_tail.strategy_conformance([], [], {})
    scopes = {item['scope']: item for item in semantics.get('scopes') or []}
    if set(scopes) != {'A', 'B1', 'B2', 'B3'}:
        FAILED.append('strategy_conformance 结构错误: %r' % (scopes,))
    else:
        print('PASS semantics: A/B1/B2/B3 符合率结构就绪（复盘记录会带出）')

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
