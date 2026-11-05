#!/usr/bin/env python3
"""命中率归因（只读派生）：近 N 期逐项命中率 vs 精确随机基线，按框架/口径/模型档分组。

口径（写死，避免误读）
- 单项：杀 1 码 / 形态排除 / 和尾杀 / 保留集，逐项独立统计，不与整组命中率混用。
- 精确基线：对 10^4 种开奖组合按该期封存参数逐期枚举，再对窗口取均值；
  不是单项概率相乘，也不是用整组基线代替单项基线。
- 只统计开奖前已发布、开奖后已核验的封存；待核验/冲突不进分母。
- 整组（by_model / by_rule）基线取 lib/hitrate_baseline.py 的 10^4 精确枚举登记值。

产物 data/hitrate_attribution.json，由 lib/hitrate_attribution_view.py 渲染进面板；
由 cron 每 10 分钟刷新（数据未变时只是重写同内容）。
"""
import argparse
import json
import math
import os
import sys
import tempfile
from collections import Counter, defaultdict
from itertools import combinations, product
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from stats_history import beijing_now, load_snapshot, parse_time

WINDOW = 100
OUTPUT = 'data/hitrate_attribution.json'
MIN_GROUP = 30

POSITION_KEYS = ('q', 'b', 's', 'g')
TAIL_POSITIONS = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                  'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
POSITION_LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
TAIL_LABELS = {'all': '千百十个合尾', 'qbs': '千百十合尾', 'qbg': '千百个合尾',
               'qsg': '千十个合尾', 'bsg': '百十个合尾'}

ORDER = {
    'killer': ('k1', 'k2', 'k3', 'k4', 'he_all', 'he_q3', 'he_h3', 'pat1', 'pat2'),
    'framework': ('q_kill1', 'b_kill1', 'he_h3_kill', 'he_q3_kill',
                  'pattern_ds', 'pattern_dx', 'he2_keep', 'he3_keep'),
}
LABELS = {
    'k1': '千位杀码', 'k2': '百位杀码', 'k3': '十位杀码', 'k4': '个位杀码',
    'he_all': '四位和尾杀', 'he_q3': '前三和尾杀', 'he_h3': '后三和尾杀',
    'pat1': '单双形态排除', 'pat2': '大小形态排除',
    'q_kill1': '千位杀1码', 'b_kill1': '百位杀1码',
    'he_h3_kill': '后三和尾杀', 'he_q3_kill': '前三和尾杀',
    'pattern_ds': '单双形态排除', 'pattern_dx': '大小形态排除',
    'he2_keep': '二数和保留4码', 'he3_keep': '三数和保留集',
}

_DIGITS = [d for d in product(range(10), repeat=4)]
TOTAL = len(_DIGITS)
POS_COUNT = [[0] * 10 for _ in range(4)]
SUM_COUNT = {key: [0] * 10 for key in TAIL_POSITIONS}
PAIR_COUNT = Counter()
TRIPLE_COUNT = Counter()
PARITY_COUNT = Counter()
SIZE_COUNT = Counter()
for _d in _DIGITS:
    for _i in range(4):
        POS_COUNT[_i][_d[_i]] += 1
    for _key, _idx in TAIL_POSITIONS.items():
        SUM_COUNT[_key][sum(_d[_i] for _i in _idx) % 10] += 1
    PAIR_COUNT[frozenset(sum(_d[_i] for _i in _p) % 10
                         for _p in combinations(range(4), 2))] += 1
    TRIPLE_COUNT[frozenset(sum(_d[_i] for _i in _p) % 10
                           for _p in combinations(range(4), 3))] += 1
    PARITY_COUNT[''.join('单' if _v % 2 else '双' for _v in _d)] += 1
    SIZE_COUNT[''.join('大' if _v >= 5 else '小' for _v in _d)] += 1


def _rate(hits, n):
    return None if not n else hits / float(n)


def _zscore(hits, n, expected_total):
    var = expected_total * (1.0 - expected_total / n) if n else 0.0
    if var <= 0:
        return None
    return (hits - expected_total) / math.sqrt(var)


def _p_two(z):
    return None if z is None else math.erfc(abs(z) / math.sqrt(2.0))


def _kill_pos(index, digit):
    return (TOTAL - POS_COUNT[index][digit]) / float(TOTAL)


def _kill_sum(key, digit):
    return (TOTAL - SUM_COUNT[key][digit]) / float(TOTAL)


def _pattern_excl(text):
    if not isinstance(text, str) or len(text) != 4:
        return None
    norm = text.replace('奇', '单').replace('偶', '双')
    if set(norm) <= {'单', '双'}:
        return (TOTAL - PARITY_COUNT.get(norm, 0)) / float(TOTAL)
    if set(norm) <= {'大', '小'}:
        return (TOTAL - SIZE_COUNT.get(norm, 0)) / float(TOTAL)
    return None


def _keep_intersect(counter, text):
    try:
        keep = {int(ch) for ch in str(text)}
    except (TypeError, ValueError):
        return None
    if not keep:
        return None
    miss = sum(count for values, count in counter.items() if not (values & keep))
    return 1.0 - miss / float(TOTAL)


def _digit(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 9 else None


def killer_baseline(seal, item):
    if len(item) == 2 and item[0] == 'k' and item[1] in '1234':
        index = int(item[1]) - 1
        kills = (seal.get('kills') or {}).get(item) if isinstance(seal.get('kills'), dict) else None
        if not isinstance(kills, list) or not kills:
            return None
        digit = _digit(kills[0])
        return None if digit is None else _kill_pos(index, digit)
    if item == 'he_all':
        digit = _digit(seal.get('he_all'))
        return None if digit is None else _kill_sum('all', digit)
    if item == 'he_q3':
        digit = _digit(seal.get('he_q3'))
        return None if digit is None else _kill_sum('qbs', digit)
    if item == 'he_h3':
        digit = _digit(seal.get('he_h3'))
        return None if digit is None else _kill_sum('bsg', digit)
    if item == 'pat1':
        return _pattern_excl(seal.get('p1'))
    if item == 'pat2':
        return _pattern_excl(seal.get('p2'))
    return None


def framework_baseline(seal, item):
    response = seal.get('response') if isinstance(seal.get('response'), dict) else {}
    if item == 'q_kill1':
        digit = _digit(response.get('q_kill1'))
        return None if digit is None else _kill_pos(0, digit)
    if item == 'b_kill1':
        digit = _digit(response.get('b_kill1'))
        return None if digit is None else _kill_pos(1, digit)
    if item == 'he_h3_kill':
        digit = _digit(response.get('he_h3_kill'))
        return None if digit is None else _kill_sum('bsg', digit)
    if item == 'he_q3_kill':
        digit = _digit(response.get('he_q3_kill'))
        return None if digit is None else _kill_sum('qbs', digit)
    if item == 'pattern_ds':
        return _pattern_excl(response.get('pattern_ds'))
    if item == 'pattern_dx':
        return _pattern_excl(response.get('pattern_dx'))
    if item == 'he2_keep':
        return _keep_intersect(PAIR_COUNT, response.get('he2_keep'))
    if item == 'he3_keep':
        return _keep_intersect(TRIPLE_COUNT, response.get('he3_keep'))
    return None


def _model_tier(seal):
    meta = seal.get('model_metadata') if isinstance(seal.get('model_metadata'), dict) else {}
    for key in ('requested_model', 'returned_model', 'model'):
        value = meta.get(key)
        if isinstance(value, str) and value:
            return value
    return 'unknown'


def _rule(seal, key):
    if key == 'killer':
        audit = seal.get('model_context_audit') if isinstance(seal.get('model_context_audit'), dict) else {}
        body = audit.get('body') if isinstance(audit.get('body'), dict) else {}
        strategy = body.get('strategy') if isinstance(body.get('strategy'), dict) else {}
        return str(strategy.get('strategy_id') or 'conditional-transition-v2')
    return 'marginal-frequency-v2'


def _outcomes_for(key, seal, numbers):
    if key == 'killer':
        from ai_verified_stats import prediction_outcome
        return prediction_outcome(seal, list(numbers))
    from framework_hit_stats import _outcomes
    return _outcomes(seal.get('response') or {}, list(numbers))


def _seals(root, key):
    path = {'killer': 'data/r1_stats.json',
            'framework': 'data/r1_framework_seals.json'}[key]
    payload = json.loads((root / path).read_text(encoding='utf-8'))
    seals = payload.get('prediction_seals') if isinstance(payload, dict) else None
    return seals if isinstance(seals, dict) else {}


def _group_summary(entries, baseline):
    n = len(entries)
    hits = sum(1 for item in entries if item)
    rate = _rate(hits, n)
    z = None
    if baseline is not None and n:
        z = (hits - baseline * n) / math.sqrt(max(1e-9, baseline * (1 - baseline) * n))
    return {'n': n, 'hits': hits, 'rate': None if rate is None else round(rate, 4),
            'baseline': None if baseline is None else round(baseline, 4),
            'z': None if z is None else round(z, 2)}


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False, encoding='utf-8')
    temporary = handle.name
    try:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='penta5 hit-rate attribution (exact per-item baselines)')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--window', type=int, default=WINDOW)
    args = parser.parse_args(argv)
    root = Path(args.root)
    now = beijing_now()

    snapshot = load_snapshot(state_path=root / 'data/state.json',
                             cache_path=root / 'data/stats_draw_history.json', persist=False)
    draws = {row['period']: row for row in snapshot['records'] if isinstance(row, dict)}

    from hitrate_baseline import RULE_BASELINE
    labels = dict(LABELS)
    for key in TAIL_POSITIONS:
        labels['tail_' + key] = TAIL_LABELS[key] + '杀'
    for key in POSITION_KEYS:
        labels['position_' + key] = POSITION_LABELS[key] + '杀码'
    labels['reverse_parity'] = '单双反向排除'
    labels['reverse_size'] = '大小反向排除'

    report = {'version': 1, 'revision': 'hitrate-attribution-v1',
              'generated_at': now.isoformat(), 'window': args.window,
              'basis': '单项命中率 vs 每期封存参数在 10^4 开奖空间上的精确枚举基线均值；'
                       '整组基线取 hitrate_baseline 登记值；z 为二项近似',
              'frameworks': {}, 'attention': []}
    baselines = {'killer': killer_baseline, 'framework': framework_baseline}
    names = {'killer': ('智能排除', 'killer'), 'framework': ('R1 框架', 'framework')}

    for key in ('killer', 'framework'):
        seals = _seals(root, key)
        order = list(ORDER.get(key) or ())
        rows = []
        for target in sorted(seals, reverse=True):
            seal = seals[target]
            if not isinstance(seal, dict):
                continue
            if str(seal.get('publication_state')) != 'published':
                continue
            try:
                published = parse_time(str(seal.get('published_at')))
            except Exception:
                continue
            record = draws.get(str(seal.get('target_period')))
            if not isinstance(record, dict):
                continue
            try:
                opened = parse_time(str(record.get('opentime')))
            except Exception:
                continue
            if published >= opened:
                continue
            try:
                outcomes = _outcomes_for(key, seal, record.get('numbers') or [])
            except Exception:
                continue
            if not isinstance(outcomes, dict):
                continue
            rows.append((seal, outcomes))
            if len(rows) >= args.window:
                break
        if not rows:
            continue
        if not order:
            seen = {}
            for _seal, outcomes in rows:
                for item in outcomes:
                    seen.setdefault(item, None)
            order = sorted(seen, key=lambda name: list(labels).index(name) if name in labels else 99)
        rows.reverse()  # oldest -> newest

        items = []
        by_rule = defaultdict(list)
        by_model = defaultdict(list)
        for item in order:
            hits, probs = 0, []
            for seal, outcomes in rows:
                if item not in outcomes:
                    continue
                hits += int(outcomes[item])
                prob = baselines[key](seal, item)
                if prob is not None:
                    probs.append(prob)
            n = sum(1 for _seal, outcomes in rows if item in outcomes)
            if not n:
                continue
            expected = sum(probs) if probs else None
            z = None
            if expected is not None and 0 < expected < n:
                z = (hits - expected) / math.sqrt(sum(p * (1 - p) for p in probs))
            items.append({'key': item, 'label': labels.get(item, item), 'n': n, 'hits': hits,
                          'rate': round(hits / float(n), 4),
                          'baseline': None if expected is None else round(expected / n, 4),
                          'delta_pp': None if expected is None else round((hits - expected) / n * 100, 2),
                          'z': None if z is None else round(z, 2), 'p': _p_two(z)})

        group_hits = sum(1 for _seal, outcomes in rows if outcomes and all(outcomes.values()))
        rule_of = [_rule(seal, key) for seal, _o in rows]
        tiers = [_model_tier(seal) for seal, _o in rows]
        for rule in sorted(set(rule_of)):
            subset = [outcomes for (seal, outcomes), value in zip(rows, rule_of) if value == rule]
            baseline = (RULE_BASELINE.get((names[key][1], rule)) or (None, None))[1]
            entry = _group_summary([all(o.values()) for o in subset if o], baseline)
            entry['rule'] = rule
            by_rule[rule] = entry
        for tier in sorted(set(tiers)):
            subset = [outcomes for (seal, outcomes), value in zip(rows, tiers) if value == tier]
            baseline = (RULE_BASELINE.get((names[key][1], rule_of[-1])) or (None, None))[1]
            entry = _group_summary([all(o.values()) for o in subset if o], baseline)
            entry['model'] = tier
            by_model[tier] = entry

        registered = RULE_BASELINE.get((names[key][1], rule_of[-1] if rule_of else ''))
        group_baseline = None if registered is None else registered[1]
        report['frameworks'][key] = {
            'label': names[key][0], 'rule': rule_of[-1] if rule_of else '-',
            'window': len(rows), 'as_of': str(rows[-1][0].get('target_period')),
            'n': len(rows), 'group_hits': group_hits,
            'group_rate': round(group_hits / float(len(rows)), 4),
            'group_baseline': group_baseline,
            'rule_mixed': len(set(rule_of)) > 1,
            'items': items,
            'by_rule': [by_rule[name] for name in sorted(by_rule)],
            'by_model': [by_model[name] for name in sorted(by_model)],
        }
        for item in items:
            if item['n'] >= 50 and item['z'] is not None and abs(item['z']) >= 1.96:
                report['attention'].append({'framework': names[key][0], 'item': item['label'],
                                            'delta_pp': item['delta_pp'], 'z': item['z'],
                                            'n': item['n']})

    periods = [str(data.get('as_of')) for data in report['frameworks'].values()
               if data.get('as_of')]
    report['as_of_period'] = max(periods) if periods else ''
    report['summary'] = {'attention_count': len(report['attention']),
                         'attention': report['attention']}
    _atomic_json(root / OUTPUT, report)
    print('hitrate_attribution %s window=%d frameworks=%s attention=%d'
          % (now.isoformat(), args.window, ','.join(report['frameworks']), len(report['attention'])))
    for key, data in report['frameworks'].items():
        print('  %-10s n=%-3d group=%.1f%% base=%s' % (
            key, data['n'], data['group_rate'] * 100,
            '-' if data['group_baseline'] is None else '%.1f%%' % (data['group_baseline'] * 100)))
        for item in data['items']:
            print('    %-16s n=%-3d %6.1f%% base=%6.1f%% d=%+6.2fpp z=%s' % (
                item['label'], item['n'], item['rate'] * 100,
                (item['baseline'] or 0) * 100, item['delta_pp'] or 0,
                '-' if item['z'] is None else '%.2f' % item['z']))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        sys.exit(0)
