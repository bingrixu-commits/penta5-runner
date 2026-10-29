"""真实命中率报告:只用已结算记录,不做任何推算。

命中定义 = 该位置实际开奖号 != 被排除的号码(杀号成功),随机基线 90%。
所有数字标注来源账本;并与账本自身的 outcomes 字段交叉验证,不一致即为数据问题。

产物:data/real_hitrate.json
调度:由 penta5-position-cold 常驻服务每期开奖后重算。
"""
import json
import os
import sys
from math import erf, sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ  # noqa: E402

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT_PATH = DATA / 'real_hitrate.json'
BASELINE = 0.90
POSITIONS = (('q', 0), ('b', 1), ('s', 2), ('g', 3))
LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}


def wilson(hits, n, z=1.96):
    if n <= 0:
        return (None, None)
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - s) / d, 6), round((c + s) / d, 6))


def pvalue(hits, n, p0=BASELINE):
    if n <= 0:
        return None
    sd = sqrt(p0 * (1 - p0) / n)
    z = (hits / n - p0) / sd
    return round(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))), 6)


def entry(label, bets, hits, baseline=BASELINE):
    if bets <= 0:
        return None
    lo, hi = wilson(hits, bets)
    return {'label': label, 'bets': bets, 'hits': hits,
            'rate': round(hits / bets, 6), 'ci95': [lo, hi],
            'baseline': baseline,
            'delta': round(hits / bets - baseline, 6),
            'p_value': pvalue(hits, bets, baseline)}


def scan_ledger(path):
    """返回 (按版本×位置统计, 按日统计, 交叉验证不一致数, 总记录数)。"""
    document = json.loads(path.read_text(encoding='utf-8'))
    verified = list((document.get('verified') or {}).values())
    by_revision = {}
    daily = {}
    mismatch = 0
    for row in verified:
        publication = row.get('publication') or {}
        numbers = (row.get('actual') or {}).get('numbers')
        if not isinstance(numbers, list) or len(numbers) < 4:
            continue
        revision = publication.get('revision') or '(未标注)'
        positions = (publication.get('response') or {}).get('selected_positions') or {}
        outcomes = row.get('outcomes') or {}
        bucket = by_revision.setdefault(revision, {})
        day = daily.setdefault(str(row.get('biz_date') or ''), [0, 0])
        for key, index in POSITIONS:
            value = positions.get(key)
            if not isinstance(value, int):
                continue
            hit = numbers[index] != value
            ledger_hit = outcomes.get(f'position_{key}')
            if isinstance(ledger_hit, bool) and ledger_hit != hit:
                mismatch += 1
            slot = bucket.setdefault(key, [0, 0])
            slot[0] += 1
            slot[1] += 1 if hit else 0
            day[0] += 1
            day[1] += 1 if hit else 0
    return by_revision, daily, mismatch, len(verified)


def main():
    report = {
        'generated_at': None,
        'revision': 'real-hitrate-v1',
        'baseline': BASELINE,
        'definition': '命中 = 该位置实际开奖号 != 被排除的号码(杀号成功)',
        'sources': [],
        'totals': [],
        'by_position': [],
        'daily': [],
        'cross_checks': [],
    }

    total_bets = total_hits = 0

    # selected_six
    path = DATA / 'selected_six_hit_stats.json'
    if path.exists():
        by_rev, daily, mismatch, count = scan_ledger(path)
        report['cross_checks'].append({'file': path.name, 'verified': count,
                                       'mismatch': mismatch})
        for revision in sorted(by_rev, key=lambda r: -sum(v[0] for v in by_rev[r].values())):
            buckets = by_rev[revision]
            bets = sum(v[0] for v in buckets.values())
            hits = sum(v[1] for v in buckets.values())
            total_bets += bets
            total_hits += hits
            item = entry(f'selected_six / {revision}', bets, hits)
            if item:
                report['totals'].append(item)
            # 只有样本足够的版本才进分位置明细
            if bets >= 100:
                for key, _ in POSITIONS:
                    if key in buckets:
                        sub = entry(f'{revision} · {LABELS[key]}', buckets[key][0], buckets[key][1])
                        if sub:
                            sub['revision'] = revision
                            sub['position'] = key
                            report['by_position'].append(sub)
        report['daily'] = [{'biz': biz, 'bets': v[0], 'hits': v[1],
                            'rate': round(v[1] / v[0], 6)}
                           for biz, v in sorted(daily.items()) if biz and v[0] > 0][-7:]

    if total_bets:
        overall = entry('selected_six 全部版本', total_bets, total_hits)
        if overall:
            report['totals'].append(overall)

    # r1_framework(字段名不同:q_kill1 / b_kill1)
    path = DATA / 'r1_framework_hit_stats.json'
    if path.exists():
        document = json.loads(path.read_text(encoding='utf-8'))
        verified = list((document.get('verified') or {}).values())
        agg = {'q': [0, 0], 'b': [0, 0]}
        mismatch = 0
        for row in verified:
            numbers = (row.get('actual') or {}).get('numbers')
            response = ((row.get('publication') or {}).get('response')) or {}
            outcomes = row.get('outcomes') or {}
            if not isinstance(numbers, list) or len(numbers) < 4:
                continue
            for key, index, field in (('q', 0, 'q_kill1'), ('b', 1, 'b_kill1')):
                value = response.get(field)
                if not isinstance(value, int):
                    continue
                hit = numbers[index] != value
                if isinstance(outcomes.get(field), bool) and outcomes[field] != hit:
                    mismatch += 1
                agg[key][0] += 1
                agg[key][1] += 1 if hit else 0
        report['cross_checks'].append({'file': path.name, 'verified': len(verified),
                                       'mismatch': mismatch})
        for key in ('q', 'b'):
            item = entry(f'r1_framework · {LABELS[key]}', agg[key][0], agg[key][1])
            if item:
                report['totals'].append(item)

    report['generated_at'] = __import__('datetime').datetime.now(TZ).isoformat()

    temp = OUT_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_PATH)

    print('真实命中率(基线 90%):')
    for item in report['totals']:
        lo, hi = item['ci95']
        print(f"  {item['label']:<52}{item['bets']:>6}注 {item['rate']:>7.2%} "
              f"[{lo:.2%},{hi:.2%}] p={item['p_value']}")
    for check in report['cross_checks']:
        print(f"  交叉验证 {check['file']}: 已结算 {check['verified']}, 不一致 {check['mismatch']}")
    print(f"report -> {OUT_PATH}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
