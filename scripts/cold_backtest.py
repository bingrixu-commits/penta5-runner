"""滚动回测:冷码口径 vs 最热 vs 随机基线。只读开奖数据,不改任何策略。

对每个窗口(30/20 期),用前 N 期滚动生成各口径的排序,检验下一期该位置
的实际开奖是否命中,并与公平随机基线(猜 1 码 10%,买 2 码 20%)做二项检验。
结果写入 data/cold_backtest.json,并追加一行到 data/cold_backtest_history.jsonl。

口径说明:
  cold_count    = 现有实现(cold_observation._group):出现次数少者更冷
  cold_omission = 遗漏优先:最久未出现者更冷(次数仅作次序)
  hot           = 对照组:窗口内出现次数最多者
"""
from pathlib import Path
import json
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ, beijing_now, load_snapshot, validate_conflicting_periods  # noqa: E402
from cold_observation import POSITION_KEYS, DIGITS  # noqa: E402

ROOT = Path('/root/penta5-runner')
OUT_PATH = ROOT / 'data/cold_backtest.json'
HISTORY_PATH = ROOT / 'data/cold_backtest_history.jsonl'
WINDOWS = (30, 20)
LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
BASELINE = {'top1': 0.10, 'top2': 0.20}


def _rank(items, order):
    if order == 'count':
        return sorted(items, key=lambda it: (it['count'], -it['omission']))
    if order == 'omission':
        return sorted(items, key=lambda it: (-it['omission'], it['count']))
    return sorted(items, key=lambda it: (-it['count'], it['omission']))


def _window_items(samples):
    counts = dict.fromkeys(DIGITS, 0)
    last = {}
    for index, value in enumerate(samples):
        counts[value] += 1
        last[value] = index
    size = len(samples)
    return [{'value': value,
             'count': counts[value],
             'omission': size if last.get(value) is None else size - last[value] - 1}
            for value in DIGITS]


def _binomial_p(hits, total, baseline):
    """两侧二项检验 p 值。优先 scipy 精确检验,缺失时用正态近似兜底。"""
    if total <= 0:
        return None
    try:
        from scipy import stats as _sps
        return float(_sps.binomtest(hits, total, baseline).pvalue)
    except Exception:
        mean = total * baseline
        variance = total * baseline * (1 - baseline)
        if variance <= 0:
            return None
        z = (hits - mean) / (variance ** 0.5)
        cdf = 0.5 * (1 + math.erf(abs(z) / (2 ** 0.5)))
        return float(max(0.0, min(1.0, 2 * (1 - cdf))))


def backtest(rows, window):
    metrics = {}
    for key in POSITION_KEYS:
        metrics[key] = {}
        for name in ('cold_count#1', 'cold_count#2', 'cold_omission#1', 'cold_omission#2',
                     'hot#1', 'hot#2'):
            metrics[key][name] = 0
        metrics[key]['n'] = 0
    totals = {name: 0 for name in ('cold_count#1', 'cold_count#2', 'cold_omission#1',
                                   'cold_omission#2', 'hot#1', 'hot#2')}
    samples_used = 0
    for i in range(window, len(rows)):
        trailing = rows[i - window:i]
        actual = rows[i]['numbers']
        samples_used += 1
        for pos_index, key in enumerate(POSITION_KEYS):
            items = _window_items([str(r['numbers'][pos_index]) for r in trailing])
            real = str(actual[pos_index])
            by_count = _rank(items, 'count')
            by_omission = _rank(items, 'omission')
            by_hot = _rank(items, 'hot')
            metrics[key]['n'] += 1
            for name, seq in (('cold_count#1', by_count), ('cold_omission#1', by_omission), ('hot#1', by_hot)):
                if real == seq[0]['value']:
                    metrics[key][name] += 1
                    totals[name] += 1
            for name, seq in (('cold_count#2', by_count), ('cold_omission#2', by_omission), ('hot#2', by_hot)):
                if real in [it['value'] for it in seq[:2]]:
                    metrics[key][name] += 1
                    totals[name] += 1
    predictions = samples_used * len(POSITION_KEYS)
    summary = {}
    for name, hits in totals.items():
        base = BASELINE['top1'] if name.endswith('#1') else BASELINE['top2']
        summary[name] = {
            'hits': hits,
            'total': predictions,
            'rate': round(hits / predictions, 6) if predictions else None,
            'baseline': base,
            'p_value': None if predictions == 0 else round(_binomial_p(hits, predictions, base), 6),
        }
    positions = {}
    for key in POSITION_KEYS:
        n = metrics[key]['n'] or 1
        positions[LABELS[key]] = {
            'n': metrics[key]['n'],
            'cold_count#1': round(metrics[key]['cold_count#1'] / n, 6),
            'cold_omission#1': round(metrics[key]['cold_omission#1'] / n, 6),
            'hot#1': round(metrics[key]['hot#1'] / n, 6),
        }
    return {'window': window, 'samples': samples_used, 'predictions': predictions,
            'strategies': summary, 'positions': positions}


def main():
    snapshot = load_snapshot(state_path=ROOT / 'data/state.json',
                             cache_path=ROOT / 'data/stats_draw_history.json',
                             persist=False)
    conflicts = validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    rows = sorted((r for r in snapshot['records'] if r['period'] not in conflicts),
                  key=lambda row: (row['opentime'], row['period']))
    now = snapshot.get('now') or beijing_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)

    report = {
        'generated_at': datetime.now(TZ).isoformat(),
        'revision': 'cold-backtest-v1',
        'records': len(rows),
        'conflicts_excluded': len(conflicts),
        'range': None if not rows else {'first': rows[0]['period'], 'last': rows[-1]['period']},
        'windows': [],
    }
    for window in WINDOWS:
        if len(rows) <= window:
            report['windows'].append({'window': window, 'error': 'insufficient-records'})
            continue
        report['windows'].append(backtest(rows, window))

    temp = OUT_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_PATH)

    if report['windows'] and 'strategies' in report['windows'][0]:
        line = {'generated_at': report['generated_at'], 'records': len(rows),
                'windows': {w['window']: {name: value['rate'] for name, value in w['strategies'].items()}
                            for w in report['windows'] if 'strategies' in w}}
        with HISTORY_PATH.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + '\n')

    for window in report['windows']:
        if 'strategies' not in window:
            print(f"window={window['window']} skipped: {window.get('error')}", flush=True)
            continue
        parts = [f"{name}={value['rate']:.2%}(p={value['p_value']})"
                 for name, value in window['strategies'].items() if name.endswith('#1')]
        print(f"window={window['window']} samples={window['samples']} " + ' '.join(parts), flush=True)
    print(f"report -> {OUT_PATH}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
