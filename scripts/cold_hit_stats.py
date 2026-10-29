"""冷码 Top N 命中率统计器:命中率 / 最多连中 / 最多连败。

口径与面板一致(系统 cold_observation 实现):
  对最近 window 期的各位置样本,按 (出现次数升序, 当前遗漏降序) 排序,
  取前 N 个号码作为【杀号】;若下一期该位置实际开奖号码【不在】其中,
  记为命中(杀号成功)。理论命中率 = (10-N)/10。

统计维度:
  - 分位置:各位置的命中率与连中/连败
  - 汇总:全部预测的命中率;按"时间顺序 + 位置顺序"串成一条序列算最长连中/连败

输出 data/cold_hit_stats.json,并打印可读表格。
"""
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ, load_snapshot, validate_conflicting_periods  # noqa: E402
from cold_observation import POSITION_KEYS, DIGITS  # noqa: E402

ROOT = Path('/root/penta5-runner')
OUT_PATH = ROOT / 'data/cold_hit_stats.json'
LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
WINDOWS = (30, 20)
TOPS = (1, 2, 3)


def ranking(samples):
    counts = dict.fromkeys(DIGITS, 0)
    last = {}
    for index, value in enumerate(samples):
        counts[value] += 1
        last[value] = index
    size = len(samples)
    items = [(value, counts[value],
              size if last.get(value) is None else size - last[value] - 1) for value in DIGITS]
    items.sort(key=lambda it: (it[1], -it[2]))
    return [it[0] for it in items]


def streaks(outcomes):
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
    return best_win, best_loss


def evaluate(rows, window):
    per_position = {key: {top: [] for top in TOPS} for key in POSITION_KEYS}
    sequenced = {top: [] for top in TOPS}
    for i in range(window, len(rows)):
        trailing = rows[i - window:i]
        actual = rows[i]['numbers']
        for pos_index, key in enumerate(POSITION_KEYS):
            order = ranking([str(r['numbers'][pos_index]) for r in trailing])
            real = str(actual[pos_index])
            for top in TOPS:
                hit = real not in order[:top]
                per_position[key][top].append(hit)
                sequenced[top].append(hit)

    report = {'window': window, 'samples': len(rows) - window, 'tops': {},
              'positions': {}}
    for top in TOPS:
        seq = sequenced[top]
        hits = sum(1 for x in seq if x)
        best_win, best_loss = streaks(seq)
        report['tops'][str(top)] = {
            'bets': len(seq), 'hits': hits,
            'rate': round(hits / len(seq), 6) if seq else None,
            'baseline': round((10 - top) / 10, 4),
            'max_win_streak': best_win, 'max_loss_streak': best_loss,
        }
    for key in POSITION_KEYS:
        entry = {}
        for top in TOPS:
            seq = per_position[key][top]
            hits = sum(1 for x in seq if x)
            best_win, best_loss = streaks(seq)
            entry[str(top)] = {'bets': len(seq), 'hits': hits,
                               'rate': round(hits / len(seq), 6) if seq else None,
                               'max_win_streak': best_win, 'max_loss_streak': best_loss}
        report['positions'][key] = {'label': LABELS[key], **entry}

    # --- 契约字段:hit_rate / buy_hit_rate / n / per_position ---
    # hit_rate 与 rate 同值(rate 保留给面板视图);buy_hit_rate 为互补口径,
    # 满足 hit_rate + buy_hit_rate == 1,可用于交叉校验方向是否正确。
    for bucket in (report['tops'], {k: v for k, v in report['positions'].items()}):
        for value in bucket.values():
            if not isinstance(value, dict):
                continue
            targets = [value] if 'rate' in value else \
                [value[t] for t in ('1', '2', '3') if isinstance(value.get(t), dict)]
            for item in targets:
                rate = item.get('rate')
                item['n'] = report['samples']
                item['hit_rate'] = rate
                item['buy_hit_rate'] = round(1 - rate, 6) if isinstance(rate, (int, float)) else None

    report['per_position'] = report['positions']
    report['n'] = report['samples']
    report['bets'] = report['samples'] * len(POSITION_KEYS)
    return report


def main():
    snapshot = load_snapshot(state_path=ROOT / 'data/state.json',
                             cache_path=ROOT / 'data/stats_draw_history.json',
                             persist=False)
    conflicts = validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    rows = sorted((r for r in snapshot['records'] if r['period'] not in conflicts),
                  key=lambda row: (row['opentime'], row['period']))
    if len(rows) <= max(WINDOWS):
        print(f'insufficient records: {len(rows)}', flush=True)
        return 1

    result = {'generated_at': __import__('datetime').datetime.now(TZ).isoformat(),
              'revision': 'cold-hit-stats-v3-contract',
              'records': len(rows),
              'as_of_period': rows[-1]['period'],
              'windows': [evaluate(rows, window) for window in WINDOWS]}

    temp = OUT_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_PATH)

    for block in result['windows']:
        print(f"\n=== 窗口 {block['window']} 期 | 样本 {block['samples']} 期 ===")
        print(f"{'杀号数':<6}{'总投注':>7}{'命中':>7}{'命中率':>9}{'理论':>8}"
              f"{'最多连中':>9}{'最多连败':>9}")
        for top in ('1', '2', '3'):
            d = block['tops'][top]
            print(f"Top{top:<3}{d['bets']:>7}{d['hits']:>7}{d['rate']:>9.2%}"
                  f"{d['baseline']:>8.0%}{d['max_win_streak']:>9}{d['max_loss_streak']:>9}")
        print(f"  -- 分位置(Top3)--")
        print(f"  {'位置':<6}{'投注':>7}{'命中率':>9}{'最多连中':>9}{'最多连败':>9}")
        for key in POSITION_KEYS:
            p = block['positions'][key]['3']
            print(f"  {block['positions'][key]['label']:<6}{p['bets']:>7}"
                  f"{p['rate']:>9.2%}{p['max_win_streak']:>9}{p['max_loss_streak']:>9}")
    print(f"\nreport -> {OUT_PATH}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
