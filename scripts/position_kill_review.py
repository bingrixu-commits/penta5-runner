"""位置杀码影子对照复核（只读）：前瞻样本 Wilson 95%CI + 换码下降判据。

预登记判据：
1. 前瞻每框架 forward 注数 n≥50（kill1 需 50 期、kill2p/kill3 各 2 注/期需 25 期）；
2. 稳定化各档命中率与现行差值落在 95%CI 内（不显著变差）；
3. 换码次数明显低于现行。

只读 data/position_kill_shadow.json；不改任何封存/统计/攻击语。
"""
from pathlib import Path
import json
import math
from datetime import datetime, timezone, timedelta

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'position_kill_review.json'
TZ = timezone(timedelta(hours=8))
POLICIES = ('hold2', 'hold3', 'hold5', 'hold-until-miss')


def wilson(k, n, z=1.96):
    if not n:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def build():
    data = json.loads((DATA / 'position_kill_shadow.json').read_text(encoding='utf-8'))
    frameworks = {}
    for name, item in data.get('frameworks', {}).items():
        fwd = item.get('forward') or {}
        actual = fwd.get('actual') or {}
        policies = fwd.get('policies') or {}
        a_n = actual.get('n') or 0
        a_hits = actual.get('hits') or 0
        a_rate = actual.get('rate')
        a_changes = actual.get('changes') or 0
        a_lo, a_hi = wilson(a_hits, a_n)
        entry = {'periods': fwd.get('periods'),
                 'actual': {'n': a_n, 'hits': a_hits, 'rate': a_rate,
                            'ci95': [a_lo, a_hi], 'changes': a_changes},
                 'policies': {}}
        for key in POLICIES:
            p = policies.get(key) or {}
            p_n = p.get('n') or 0
            p_hits = p.get('hits') or 0
            p_rate = p.get('rate')
            p_changes = p.get('changes') or 0
            p_lo, p_hi = wilson(p_hits, p_n)
            overlap = (a_lo is not None and p_hi is not None and p_hi >= a_lo)
            churn_drop = a_changes - p_changes
            entry['policies'][key] = {
                'n': p_n, 'hits': p_hits, 'rate': p_rate, 'ci95': [p_lo, p_hi],
                'changes': p_changes, 'churn_drop': churn_drop,
                'ci_overlap_actual': overlap,
                'criteria': {'n_ge_50': p_n >= 50, 'not_worse': overlap,
                             'churn_down': churn_drop > 0},
                'pass': (p_n >= 50 and overlap and churn_drop > 0),
            }
        frameworks[name] = entry
    now = datetime.now(TZ)
    return {'version': 1, 'generated_at': now.isoformat(timespec='seconds'),
            'note': '只读复核：n≥50、95%CI 不显著变差、换码下降三判据。',
            'frameworks': frameworks}


def render(data):
    lines = ['# 位置杀码影子对照复核', '',
             '> 生成：%s（只读，不改任何封存/统计）' % data.get('generated_at'), '',
             '| 框架 | 前瞻期数 | 现行命中率(95%CI) | 策略 | 命中率(95%CI) | 换码↓ | n≥50 | 不显著变差 | 换码下降 | 达标 |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for name, item in data.get('frameworks', {}).items():
        a = item['actual']
        a_rate = ('%.2f%%' % (100 * a['rate'])) if a['rate'] is not None else '-'
        a_ci = '[%.1f%%, %.1f%%]' % (100 * a['ci95'][0], 100 * a['ci95'][1]) if a['ci95'][0] is not None else '-'
        first = True
        for key in POLICIES:
            p = item['policies'][key]
            p_rate = ('%.2f%%' % (100 * p['rate'])) if p['rate'] is not None else '-'
            p_ci = '[%.1f%%, %.1f%%]' % (100 * p['ci95'][0], 100 * p['ci95'][1]) if p['ci95'][0] is not None else '-'
            c = p['criteria']
            mark = lambda b: 'Y' if b else 'N'
            lines.append('| %s | %s | %s %s | %s | %s %s | %d | %s | %s | %s | %s |' % (
                name if first else '', item['periods'] if first else '',
                a_rate, a_ci, key, p_rate, p_ci, p['churn_drop'],
                mark(c['n_ge_50']), mark(c['not_worse']), mark(c['churn_down']), mark(p['pass'])))
            first = False
    lines += ['', '注：单位置杀1码随机基线90%。达标 = 三项判据全过。仅作影子对照，转正须另行定点确认。']
    return '\n'.join(lines)


def main():
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(render(data))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
