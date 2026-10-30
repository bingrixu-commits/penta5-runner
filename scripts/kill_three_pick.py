"""三位置杀码建议:4 个位置里选 3 个,各杀 1 码,并给出该规则的实测命中率。

规则(可回测、无未来信息):
  1. 每隔位置用最近 WINDOW 期样本,按系统冷码口径取第 1 冷码作为杀码;
     命中 = 该位置实际开奖号 != 杀码。
  2. 用最近 LOOKBACK 期该位置的真实杀号命中率给 4 个位置排序,
     取命中率最高的 3 个位置(并列时取当前遗漏更长的)。
  3. 组合命中 = 这 3 个位置的杀号同时成功。

输出:当前建议 + 该规则的滚动回测(组合命中率、理论乘积、连中连败、分位置命中率)。
"""
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ, load_snapshot, validate_conflicting_periods  # noqa: E402
from cold_observation import POSITION_KEYS, DIGITS  # noqa: E402

ROOT = Path('/root/penta5-runner')
OUT_PATH = ROOT / 'data/kill_three_pick.json'
LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
WINDOW = 30
LOOKBACK = 100


def ranking(samples):
    """系统冷码口径:次数升序,其次遗漏降序;返回 [(value, count, omission), ...]。"""
    counts = dict.fromkeys(DIGITS, 0)
    last = {}
    for index, value in enumerate(samples):
        counts[value] += 1
        last[value] = index
    size = len(samples)
    items = [(value, counts[value],
              size if last.get(value) is None else size - last[value] - 1) for value in DIGITS]
    items.sort(key=lambda it: (it[1], -it[2]))
    return items


def streaks(outcomes):
    """Return (best_win, best_loss, current_win, current_loss).

    2026-10-30: 面板新增「当前连中/连败」——取回测序列末尾仍在延续的那一段，
    让用户能看到规则当下的实时状态（最多连中/连败只是历史极值）。
    """
    best_win = best_loss = cur_win = cur_loss = 0
    for ok in outcomes:
        if ok:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        best_win = max(best_win, cur_win)
        best_loss = max(best_loss, cur_loss)
    return best_win, best_loss, cur_win, cur_loss


def main():
    snapshot = load_snapshot(state_path=ROOT / 'data/state.json',
                             cache_path=ROOT / 'data/stats_draw_history.json',
                             persist=False)
    conflicts = validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    rows = sorted((r for r in snapshot['records'] if r['period'] not in conflicts),
                  key=lambda row: (row['opentime'], row['period']))
    n = len(rows)
    if n < WINDOW + LOOKBACK + 30:
        print(f'insufficient records: {n}', flush=True)
        return 1

    # 预计算:每个位置在每一期的第1冷码 与 是否杀中
    kills = [dict() for _ in range(n)]     # kills[i][pos] = (value, omission)
    hits = [dict() for _ in range(n)]      # hits[i][pos] = bool
    for i in range(WINDOW, n):
        actual = rows[i]['numbers']
        trailing = rows[i - WINDOW:i]
        for pos_index, key in enumerate(POSITION_KEYS):
            order = ranking([str(r['numbers'][pos_index]) for r in trailing])
            value, _count, omission = order[0]
            kills[i][key] = (value, omission)
            hits[i][key] = str(actual[pos_index]) != value

    # 滚动回测:每期用前 LOOKBACK 期命中率挑 3 个位置
    combined = []
    per_position_bets = {key: [0, 0] for key in POSITION_KEYS}
    for i in range(WINDOW + LOOKBACK, n):
        rates = {}
        for key in POSITION_KEYS:
            window_hits = [hits[j][key] for j in range(i - LOOKBACK, i)]
            rates[key] = sum(window_hits) / LOOKBACK
        chosen = sorted(POSITION_KEYS,
                        key=lambda k: (-rates[k], -kills[i][k][1]))[:3]
        ok = all(hits[i][k] for k in chosen)
        combined.append(ok)
        for k in chosen:
            per_position_bets[k][0] += 1
            per_position_bets[k][1] += 1 if hits[i][k] else 0

    total = len(combined)
    wins = sum(1 for x in combined if x)
    win_streak, loss_streak, current_win, current_loss = streaks(combined)

    # 当前建议:用最新数据(不含未来)
    latest_rates = {}
    for key in POSITION_KEYS:
        recent = [hits[j][key] for j in range(n - LOOKBACK, n) if key in hits[j]]
        latest_rates[key] = sum(recent) / len(recent) if recent else 0.0
    trailing = rows[n - WINDOW:n]
    current = {}
    for pos_index, key in enumerate(POSITION_KEYS):
        value, count, omission = ranking([str(r['numbers'][pos_index]) for r in trailing])[0]
        current[key] = {'value': value, 'count': count, 'omission': omission,
                        'recent_rate': round(latest_rates[key], 6)}
    chosen_now = sorted(POSITION_KEYS,
                        key=lambda k: (-latest_rates[k], -current[k]['omission']))[:3]

    product = 1.0
    for k in chosen_now:
        product *= latest_rates[k]

    report = {
        'generated_at': __import__('datetime').datetime.now(TZ).isoformat(),
        'revision': 'kill-three-pick-v1',
        'as_of_period': rows[-1]['period'],
        'window': WINDOW, 'lookback': LOOKBACK,
        'rule': '每位置杀第1冷码;按近 LOOKBACK 期各位置杀号命中率取前3;组合全中为命中',
        'recommendation': {
            # 2026-10-30: 展示按固定位置顺序(千、百、十、个)对齐,不按命中率排序;
            # 选择逻辑仍是按最近 LOOKBACK 期命中率取前 3(排序只影响展示顺序)。
            'positions': [{'key': k, 'label': LABELS[k], 'kill': current[k]['value'],
                           'omission': current[k]['omission'],
                           'recent_rate': current[k]['recent_rate']}
                          for k in POSITION_KEYS if k in chosen_now],
            'skipped': [{'key': k, 'label': LABELS[k],
                         'recent_rate': current[k]['recent_rate']}
                        for k in POSITION_KEYS if k not in chosen_now],
            'all_positions': {LABELS[k]: {'kill': current[k]['value'],
                                          'omission': current[k]['omission'],
                                          'recent_rate': current[k]['recent_rate']}
                              for k in POSITION_KEYS},
            'theoretical_product': round(product, 6),
        },
        'backtest': {
            'periods': total, 'combined_hits': wins,
            'combined_rate': round(wins / total, 6) if total else None,
            'expected_product': round(0.9 ** 3, 6),
            'max_win_streak': win_streak, 'max_loss_streak': loss_streak,
            'current_win_streak': current_win, 'current_loss_streak': current_loss,
            'per_position': {LABELS[k]: {'bets': per_position_bets[k][0],
                                         'hits': per_position_bets[k][1],
                                         'rate': round(per_position_bets[k][1] / per_position_bets[k][0], 6)
                                         if per_position_bets[k][0] else None}
                             for k in POSITION_KEYS},
        },
    }

    temp = OUT_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_PATH)

    rec = report['recommendation']
    print(f"截至 {report['as_of_period']}  窗口{WINDOW}期  回看{LOOKBACK}期")
    print("\n=== 本期建议(选 3 个位置,各杀 1 码)===")
    for item in rec['positions']:
        print(f"  {item['label']}  杀 {item['kill']}   (遗漏 {item['omission']} 期, "
              f"近{LOOKBACK}期该位置杀号命中 {item['recent_rate']:.2%})")
    print("  放弃: " + "、".join(item['label'] for item in rec['skipped']))
    print(f"  理论组合命中率: {rec['theoretical_product']:.2%}")
    print("  口径: 位置按 千/百/十/个 对齐; 核算只使用前4球, 第5球不参与。")

    bt = report['backtest']
    print(f"\n=== 该规则滚动回测({bt['periods']} 期)===")
    print(f"  组合命中率: {bt['combined_rate']:.2%}   理论乘积: {bt['expected_product']:.2%}")
    print(f"  最多连中: {bt['max_win_streak']} 手   最多连败: {bt['max_loss_streak']} 手")
    if bt['current_win_streak']:
        print(f"  当前状态: 连中 {bt['current_win_streak']} 手(进行中)")
    else:
        print(f"  当前状态: 连败 {bt['current_loss_streak']} 手(进行中)")
    print("  分位置参与统计:")
    for label, item in bt['per_position'].items():
        print(f"    {label}: 参与 {item['bets']} 次, 命中率 {item['rate']:.2%}")
    print(f"\nreport -> {OUT_PATH}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
