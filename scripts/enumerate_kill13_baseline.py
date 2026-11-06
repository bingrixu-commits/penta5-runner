"""kill13 整组随机基线精确枚举（只读证据脚本，不写生产账本）。

枚举口径：10^4 开奖（前 4 球）× C(50,2)=1225 个封存配置（50 个合尾候选中取 2 个不同项）。
整组命中 = 两项杀尾同时命中。逐开奖统计命中候选数 h，则两两同中的配置数为 C(h,2)，
再对全部 10^4 开奖求和；同时独立校验 h==45 恒定、以及 990/1225 闭式值。
"""
from itertools import combinations
import json
import sys
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))

import kill13_tail

OUT = ROOT / 'data' / 'kill13_tail' / 'baseline_enumeration.json'
TAILS = kill13_tail.TAILS
TAIL_KEYS = kill13_tail.TAIL_KEYS
CANDIDATES = [(key, str(code)) for key in TAIL_KEYS for code in range(10)]
CONFIGS = list(combinations(range(len(CANDIDATES)), 2))


def actual_tails(draw):
    return {key: sum(draw[index] for index in TAILS[key]['indices']) % 10
            for key in TAIL_KEYS}


def main():
    config_total = 0
    both_hit = 0
    hit_counts = {}
    for draw in __import__('itertools').product(range(10), repeat=4):
        actual = actual_tails(draw)
        hits = [index for index, (key, kill) in enumerate(CANDIDATES)
                if actual[key] != int(kill)]
        count = len(hits)
        hit_counts[count] = hit_counts.get(count, 0) + 1
        config_total += len(CONFIGS)
        both_hit += count * (count - 1) // 2
    rate = both_hit / float(config_total)
    formula = (45 * 44 / 2.0) / (50 * 49 / 2.0)
    report = {
        'version': 1,
        'rule_version': kill13_tail.RULE_VERSION,
        'basis': kill13_tail.EXPECTED_BASIS,
        'draws': 10000,
        'candidates': len(CANDIDATES),
        'configs': len(CONFIGS),
        'config_draw_pairs': config_total,
        'both_hit_pairs': both_hit,
        'rate_exact': rate,
        'rate_rounded4': round(rate, 4),
        'formula_990_over_1225': formula,
        'formula_matches': abs(rate - formula) < 1e-12,
        'hit_count_distribution': {str(k): v for k, v in sorted(hit_counts.items())},
        'hit_count_is_constant_45': list(hit_counts) == [45],
        'expected_items': 2,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + chr(10), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (report['configs'] == 1225 and report['hit_count_is_constant_45']
          and report['formula_matches'] and report['rate_rounded4'] == 0.8082)
    if not ok:
        print('KILL13_BASELINE_ENUMERATION_FAIL')
        return 1
    print('KILL13_BASELINE_ENUMERATION_OK rate=%.10f rounded4=%.4f' % (rate, rate))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
