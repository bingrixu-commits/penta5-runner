"""模型显著性监控:版本锁定 + 样本量进度 + 实时判定。

要回答的问题只有一个:当前这个模型版本的"杀 1 个号"命中率,
是否显著高于随机杀号的 90%?而不是某一天冲高就被当成有效。

关键设计:
- **版本锁定**:只统计最新 revision 的样本。换版本即重置计数,
  避免把 v1/v2/v3 混在一起算平均(那是多重比较陷阱)。
- **样本量进度**:给出达到 +2pp(92%)双侧 95% 显著所需的注数,
  以及当前进度。样本不够时结论一律"不可用"。
- **显著性**:对 90% 基线做二项检验,输出 z 与单/双侧 p 值。
- 同时列出近几日命中率,用于说明"单日冲高"通常只是噪声。

命中定义 = 该位置实际开奖号 != 被排除的号(杀号成功),基线 90%。
"""
from pathlib import Path
from math import sqrt, erf
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ  # noqa: E402

ROOT = Path('/root/penta5-runner')
LEDGER = ROOT / 'data/selected_six_hit_stats.json'
OUT_PATH = ROOT / 'data/model_significance.json'
BASELINE = 0.90
TARGET_RATE = 0.92          # 想证明的"真实水平"
MIN_SAMPLE = 200            # 低于此样本不给结论
RECENT_DAYS = 5
# selected_positions 的键随 revision 变化(恰好两个位置),必须按契约映射到
# 四位里的下标,不能固定只统计千/十。
POSITION_INDEX = {'q': 0, 'b': 1, 's': 2, 'g': 3}


def _cdf(z):
    return 0.5 * (1 + erf(abs(z) / sqrt(2)))


def analyse(bets, hits, baseline=BASELINE):
    n = bets
    if n <= 0:
        return None
    rate = hits / n
    sd = sqrt(baseline * (1 - baseline) / n)
    z = (rate - baseline) / sd if sd else 0.0
    cdf = _cdf(z)
    p_two = 2 * (1 - cdf)
    p_one = (1 - cdf) if rate >= baseline else cdf
    return {'n': n, 'hits': hits, 'rate': round(rate, 6),
            'z': round(z, 3), 'p_two_sided': round(min(1.0, p_two), 6),
            'p_one_sided': round(min(1.0, p_one), 6)}


def required_bets(target=TARGET_RATE, baseline=BASELINE, z=1.96):
    """达到 target 水平时双侧 95% 显著所需注数(保守估算)。"""
    return int(round((z ** 2) * baseline * (1 - baseline) / ((target - baseline) ** 2)))


def verdict(stat):
    if stat is None:
        return '无数据'
    if stat['n'] < MIN_SAMPLE:
        return f"样本不足({stat['n']}<{MIN_SAMPLE}),结论不可用"
    rate = stat['rate']
    if stat['p_one_sided'] < 0.05:
        return '显著优于随机' if rate > BASELINE else '显著差于随机'
    return '与随机无法区分'


def main():
    document = json.loads(LEDGER.read_text(encoding='utf-8'))
    verified = list((document.get('verified') or {}).values())

    # 按 revision 收集"杀 1 码"的样本(position_q / position_s)
    per_revision = {}
    for entry in verified:
        publication = entry.get('publication') or {}
        revision = publication.get('revision') or '(未标注)'
        numbers = (entry.get('actual') or {}).get('numbers')
        if not isinstance(numbers, list) or len(numbers) < 4:
            continue
        positions = (publication.get('response') or {}).get('selected_positions') or {}
        record = per_revision.setdefault(revision, {'bets': 0, 'hits': 0,
                                                    'days': set(), 'periods': [],
                                                    'daily': {}})
        for key, kill in positions.items():
            index = POSITION_INDEX.get(key)
            if index is None or not isinstance(kill, int):
                continue
            hit = numbers[index] != kill
            record['bets'] += 1
            record['hits'] += 1 if hit else 0
            biz = entry.get('biz_date')
            if biz:
                record['days'].add(biz)
                day = record['daily'].setdefault(biz, [0, 0])
                day[0] += 1
                day[1] += 1 if hit else 0
            period = str(entry.get('target_period') or '')
            if period:
                record['periods'].append(period)

    if not per_revision:
        print('no samples', flush=True)
        return 1

    # 版本锁定:以样本期号最新的 revision 为"当前版本"
    latest_revision = max(per_revision,
                          key=lambda r: max(per_revision[r]['periods'] or ['0']))
    current = per_revision[latest_revision]
    stat = analyse(current['bets'], current['hits'])

    days_sorted = sorted(current['daily'])
    recent = [{'biz': d, 'bets': current['daily'][d][0],
               'rate': round(current['daily'][d][1] / current['daily'][d][0], 6)}
              for d in days_sorted[-RECENT_DAYS:]]

    target_n = required_bets()
    report = {
        'generated_at': __import__('datetime').datetime.now(TZ).isoformat(),
        'revision': 'model-significance-v1',
        'baseline': BASELINE,
        'target_rate': TARGET_RATE,
        'locked_revision': latest_revision,
        'locked_since': min(current['periods']) if current['periods'] else None,
        'locked_days': len(current['days']),
        'current': stat,
        'verdict': verdict(stat),
        'target_bets': target_n,
        'progress': round(stat['n'] / target_n, 4) if stat else None,
        'remaining_bets': max(0, target_n - (stat['n'] if stat else 0)),
        'recent_daily': recent,
        'all_revisions': {
            rev: {'bets': data['bets'], 'hits': data['hits'],
                  'rate': round(data['hits'] / data['bets'], 6) if data['bets'] else None,
                  'days': len(data['days']),
                  'stats': analyse(data['bets'], data['hits'])}
            for rev, data in per_revision.items()},
        'note': ('只统计锁定版本的样本;换版本即重置。单日命中率波动约 ±3pp,'
                 '不足以判断模型优劣,以整体显著性为准。'),
    }

    temp = OUT_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_PATH)

    print(f"锁定版本: {latest_revision}")
    if stat:
        print(f"  自 {report['locked_since']} 起 {report['locked_days']} 天,"
              f"{stat['n']} 注,命中率 {stat['rate']:.2%}(基线 {BASELINE:.0%})")
        print(f"  z={stat['z']}  双侧p={stat['p_two_sided']}  单侧p={stat['p_one_sided']}")
        print(f"  判定: {report['verdict']}")
        print(f"  样本进度: {stat['n']}/{target_n} ({report['progress']:.1%}),"
              f"还需 {report['remaining_bets']} 注")
    print("  近几日:", ", ".join(f"{d['biz']} {d['rate']:.1%}({d['bets']}注)" for d in recent))
    print("\n各版本(只作参考,不可混算):")
    for rev, item in sorted(report['all_revisions'].items(), key=lambda kv: -kv[1]['bets']):
        rate = '  n/a' if item['rate'] is None else f"{item['rate']:.2%}"
        print(f"  {rev:<44}{item['bets']:>6} 注  {rate}  {item['days']} 天")
    print(f"\nreport -> {OUT_PATH}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
