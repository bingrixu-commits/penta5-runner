"""已结算策略真实表现报告:只用 ledger 里的 verified 记录,不做任何预测。

对每个可独立验证的排除类指标,统计"实际发布的次数"与"排除成功的次数",
并与公平随机基线(排除 1 个号 = 90%)做二项检验。

重要:每个指标的分母必须是**真正发布了该指标的期数**,而不是全部已结算期数
(部分期次并不会给出全部指标)。分母用错会把 90% 算成 55%。

结果写入 data/strategy_settled_report.json,并追加一行到
data/strategy_settled_history.jsonl。
"""
from pathlib import Path
import json
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ  # noqa: E402

ROOT = Path('/root/penta5-runner')
LEDGER = ROOT / 'data/selected_six_hit_stats.json'
OUT_PATH = ROOT / 'data/strategy_settled_report.json'
HISTORY_PATH = ROOT / 'data/strategy_settled_history.jsonl'
BASELINE = 0.9


def wilson(hits, total, z=1.96):
    if total <= 0:
        return None, None
    phat = hits / total
    denom = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    spread = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return round((centre - spread) / denom, 6), round((centre + spread) / denom, 6)


def binom_p(hits, total, baseline):
    if total <= 0:
        return None
    try:
        from scipy import stats as sps
        return float(sps.binomtest(hits, total, baseline).pvalue)
    except Exception:
        mean = total * baseline
        var = total * baseline * (1 - baseline)
        if var <= 0:
            return None
        z = (hits - mean) / (var ** 0.5)
        cdf = 0.5 * (1 + math.erf(abs(z) / (2 ** 0.5)))
        return float(max(0.0, min(1.0, 2 * (1 - cdf))))


# 每个指标:如何取到"发布的值"、以及判定"排除成功"的规则。
# 只有语义经过反验(与 ledger 的 outcomes 完全一致)的指标才纳入基线检验。
EXCLUSION_METRICS = (
    ('position_q', lambda resp: (resp.get('selected_positions') or {}).get('q'),
     lambda numbers, value: numbers[0] != value),
    ('position_s', lambda resp: (resp.get('selected_positions') or {}).get('s'),
     lambda numbers, value: numbers[2] != value),
    ('tail_all', lambda resp: (resp.get('selected_tails') or {}).get('all'),
     lambda numbers, value: sum(numbers) % 10 != value),
    ('tail_bsg', lambda resp: (resp.get('selected_tails') or {}).get('bsg'),
     lambda numbers, value: (numbers[1] + numbers[2] + numbers[3]) % 10 != value),
)

# keep 类指标:保留集合的语义与基线取决于模型定义,这里只报经验比率,不做显著性判定。
KEEP_METRICS = ('he2_keep', 'he3_keep')


def main():
    document = json.loads(LEDGER.read_text(encoding='utf-8'))
    verified = list((document.get('verified') or {}).values())
    if not verified:
        print('no verified entries', flush=True)
        return 0

    periods = sorted({str(entry.get('target_period')) for entry in verified})
    report = {
        'generated_at': datetime.now(TZ).isoformat(),
        'revision': 'strategy-settled-report-v1',
        'ledger': str(LEDGER.name),
        'verified_total': len(verified),
        'range': {'first': periods[0], 'last': periods[-1]},
        'pending': len(document.get('pending') or {}),
        'exclusions': {},
        'keep_empirical': {},
    }

    for name, getter, judge in EXCLUSION_METRICS:
        hits = total = 0
        for entry in verified:
            response = (entry.get('publication') or {}).get('response') or {}
            value = getter(response)
            if value is None or not isinstance(entry.get('actual'), dict):
                continue
            numbers = entry['actual'].get('numbers')
            if not isinstance(numbers, list) or len(numbers) < 4:
                continue
            total += 1
            try:
                if judge(numbers, value):
                    hits += 1
            except (TypeError, ValueError):
                total -= 1
        low, high = wilson(hits, total)
        report['exclusions'][name] = {
            'published': total,
            'success': hits,
            'rate': round(hits / total, 6) if total else None,
            'baseline': BASELINE,
            'delta_vs_baseline': round(hits / total - BASELINE, 6) if total else None,
            'p_value': binom_p(hits, total, BASELINE),
            'ci95': [low, high],
        }

    for name in KEEP_METRICS:
        present = success = 0
        for entry in verified:
            response = (entry.get('publication') or {}).get('response') or {}
            kept = response.get(name)
            if not isinstance(kept, str) or not kept:
                continue
            present += 1
            if (entry.get('outcomes') or {}).get(name) is True:
                success += 1
        report['keep_empirical'][name] = {
            'published': present,
            'success': success,
            'rate': round(success / present, 6) if present else None,
            'note': '保留集合的基线取决于模型定义,此处仅报经验值,不做显著性判定',
        }

    temp = OUT_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_PATH)

    line = {'generated_at': report['generated_at'],
            'verified_total': report['verified_total'],
            'exclusions': {name: {'rate': value['rate'], 'published': value['published'],
                                  'p_value': value['p_value']}
                           for name, value in report['exclusions'].items()}}
    with HISTORY_PATH.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + '\n')

    for name, value in report['exclusions'].items():
        print(f"{name:<12} published={value['published']:<4} rate={value['rate']:.2%} "
              f"baseline={BASELINE:.0%} delta={value['delta_vs_baseline']:+.2%} "
              f"p={value['p_value']}", flush=True)
    print(f"report -> {OUT_PATH}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
