#!/usr/bin/env python3
"""框架整组命中率累计报告：版本锁定 + 精确枚举随机基线 + Wilson 区间 + 发布覆盖率。

口径（写死，避免误读）：
- 整组命中 = 一期要求全部项目同时命中；随机期望 = 现行规则用 10^4 开奖组合精确枚举校准值。
- 只统计已结算/已核验记录；缺预测、待核验、冲突期不进分母。
- 逐框架按「当前规则版本」锁定样本；旧版本单独列出，不混算。
- 覆盖率 = 当前版本已核验期数 / 首期到末期之间的计划期数；
  计划期命中率 = 命中数 / 计划期数（把漏发期按 0 命中计入，用于暴露 402/停机造成的真实损失）。
- n < 200 判定「样本不足」，不做优劣结论；p 为双侧二项检验（方差用 p0(1-p0)/n）。

数据源：data/r1_stats.json、r1_framework_hit_stats.json、selected_six_hit_stats.json、
he3_refine_hit_stats.json、cold_framework_*.json、
kill3_tail/settlements.json+seals.json、strategy_comparison_stats.json、*_status.json。
只读；产物 data/framework_hitrate_cumulative.json，由 crontab 每 15 分钟刷新。
"""
import glob
import json
import math
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'framework_hitrate_cumulative.json'
MIN_SAMPLE = 200
BASIS = '现行规则 10^4 开奖组合精确枚举校准（2026-10-31）'

RULE_BASELINE = {
    ('killer', 'conditional-transition-v2'): (9, 0.4162),
    ('framework', 'marginal-frequency-v2'): (8, 0.5559),
    ('selected_six', 'selected-six-independent-v4-minimax-forms'): (8, 0.5160),
    ('selected_six', 'selected-six-independent-v6-panel-context'): (8, 0.5160),
    ('cold_framework', 'cold-framework-v3-30-pos3-tail2-he2x4-he3x5'): (7, 0.5427),
    ('he3_refine', 'he3-refine-v2-balanced'): (1, 0.9375),
    ('he3_refine', 'he3-refine-v3-panel-context'): (1, 0.9375),
    ('kill3_tail', 'kill3-tail-v4'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v5-panel-context'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v6-three-positions'): (6, 0.5478),
    ('kill3_tail', 'kill3-tail-v7-two-counted'): (5, 0.6191),
}
LABELS = {
    'killer': '智能深排 killer（9 项）',
    'framework': '固定八码框架（8 项）',
    'selected_six': '八项独立框架 selected_six（8 项）',
    'cold_framework': '冷码七项框架',
    'he3_refine': '合尾 he3 剩余 5 码',
    'kill3_tail': 'AI 三位置杀码',
}
WINDOWS = {
    'killer': '自账本首期累计',
    'framework': '自账本首期累计',
    'selected_six': '当前规则版本累计',
    'cold_framework': '当前规则版本累计',
    'he3_refine': '当前规则版本累计',
    'kill3_tail': '当前规则版本累计',
}
POS = {'q': 0, 'b': 1, 's': 2, 'g': 3}
TAIL_INDEXES = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
PAIRS = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
TRIPLES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _wilson(hits, n, z=1.96):
    if n <= 0:
        return [None, None]
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - s) / d, 6), round((c + s) / d, 6)]


def _p_two(hits, n, p0):
    if n <= 0 or p0 is None:
        return None
    z = (hits / n - p0) / math.sqrt(p0 * (1 - p0) / n)
    return round(math.erfc(abs(z) / math.sqrt(2)), 6)


def _need_n(p0, delta=0.02):
    za, zb = 1.959964, 0.841621
    p1 = min(0.999, p0 + delta)
    return int(-(-((za * math.sqrt(p0 * (1 - p0)) + zb * math.sqrt(p1 * (1 - p1))) ** 2
                   / (p1 - p0) ** 2) // 1))


def _period_index(period):
    try:
        text = str(period)
        if len(text) < 11:
            return None
        day = date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        index = int(text[8:11])
        if index < 0 or index > 287:
            return None
        return day.toordinal() * 288 + index
    except (TypeError, ValueError):
        return None


def _planned(periods):
    indexes = [_period_index(p) for p in periods]
    indexes = [i for i in indexes if i is not None]
    if not indexes:
        return None
    return max(indexes) - min(indexes) + 1


def _expected(key, rule):
    item = RULE_BASELINE.get((key, rule))
    if item is None:
        return None
    return item[1]


def _items(key, rule):
    item = RULE_BASELINE.get((key, rule))
    return item[0] if item else None


def _verdict(n, p):
    if p is None:
        return '基线待校准'
    if n < MIN_SAMPLE:
        return '样本不足(n<%d)' % MIN_SAMPLE
    if p < 0.05:
        return '偏离随机基线'
    return '与随机无法区分'


def _build(key, rule, records, source, note=''):
    """records: [(target_period, joint_hit_bool, model_or_None)]。"""
    n = len(records)
    hits = sum(1 for _, joint, _ in records if joint)
    expected = _expected(key, rule)
    rate = round(hits / n, 6) if n else None
    delta = round(rate - expected, 6) if (rate is not None and expected is not None) else None
    p = _p_two(hits, n, expected)
    targets = [target for target, _, _ in records]
    planned = _planned(targets)
    coverage = round(n / planned, 6) if (planned and planned > 0) else None
    planned_hit_rate = round(hits / planned, 6) if (planned and planned > 0) else None
    models = {}
    for _, _, model in records:
        if not model:
            continue
        bucket = models.setdefault(model, [0, 0])
        bucket[0] += 1
        bucket[1] += 1 if _ else 0
    model_split = [{'model': model, 'n': value[0], 'hits': value[1],
                    'rate': round(value[1] / value[0], 6) if value[0] else None}
                   for model, value in sorted(models.items(), key=lambda item: -item[1][0])]
    valid = sorted([str(t) for t in targets if t is not None])
    return {
        'key': key, 'label': LABELS[key], 'rule': rule,
        'items': _items(key, rule), 'window': WINDOWS[key],
        'n': n, 'hits': hits, 'rate': rate, 'ci95': _wilson(hits, n),
        'expected': expected, 'delta': delta, 'p': p,
        'verdict': _verdict(n, p), 'planned': planned, 'coverage': coverage,
        'planned_hit_rate': planned_hit_rate,
        'first_period': valid[0] if valid else None,
        'last_period': valid[-1] if valid else None,
        'need_n_2pp': _need_n(expected) if expected is not None else None,
        'model_split': model_split, 'note': note, 'source': source,
    }


def _model_of(publication):
    metadata = (publication or {}).get('model_metadata') or {}
    if isinstance(metadata, dict):
        return metadata.get('requested_model')
    return None


def _rule_from_strategy(key):
    stats = _read(DATA / 'strategy_comparison_stats.json') or {}
    model = (stats.get('models') or {}).get(key) or {}
    return model.get('strategy_id')


def cum_killer():
    doc = _read(DATA / 'r1_stats.json') or {}
    evaluations = doc.get('verified_evaluations') or []
    records = []
    for row in evaluations:
        outcome = row.get('outcome') or {}
        if not isinstance(outcome, dict) or len(outcome) < 9:
            continue
        records.append((row.get('target_period'), all(bool(v) for v in outcome.values()), None))
    rule = _rule_from_strategy('killer') or 'conditional-transition-v2'
    return _build('killer', rule, records, 'data/r1_stats.json',
                  '账本未标注规则版本；按现行规则 conditional-transition-v2 复核全部记录')


def cum_framework():
    doc = _read(DATA / 'r1_framework_hit_stats.json') or {}
    verified = list((doc.get('verified') or {}).values())
    records = []
    for row in verified:
        outcome = row.get('outcomes') or {}
        if not isinstance(outcome, dict) or len(outcome) < 8:
            continue
        records.append((row.get('target_period'), all(bool(v) for v in outcome.values()),
                        _model_of(row.get('publication'))))
    rule = _rule_from_strategy('framework') or 'marginal-frequency-v2'
    return _build('framework', rule, records, 'data/r1_framework_hit_stats.json',
                  '账本未标注规则版本；按现行规则 marginal-frequency-v2 复核全部记录')


def cum_selected_six():
    doc = _read(DATA / 'selected_six_hit_stats.json') or {}
    verified = list((doc.get('verified') or {}).values())
    stats = _read(DATA / 'selected_six_stats.json') or {}
    rule = stats.get('rule_revision') or 'selected-six-independent-v4-minimax-forms'
    records, history = [], {}
    for row in verified:
        publication = row.get('publication') or {}
        revision = publication.get('revision') or '(未标注)'
        outcome = row.get('outcomes') or {}
        if not isinstance(outcome, dict) or not outcome:
            continue
        joint = all(bool(v) for v in outcome.values())
        model = _model_of(publication)
        if revision == rule:
            records.append((row.get('target_period'), joint, model))
        else:
            bucket = history.setdefault(revision, [0, 0])
            bucket[0] += 1
            bucket[1] += 1 if joint else 0
    row = _build('selected_six', rule, records, 'data/selected_six_hit_stats.json')
    row['history'] = [{'revision': revision, 'n': value[0], 'hits': value[1],
                       'rate': round(value[1] / value[0], 6) if value[0] else None}
                      for revision, value in sorted(history.items())]
    return row


def cum_he3():
    doc = _read(DATA / 'he3_refine_hit_stats.json') or {}
    verified = list((doc.get('verified') or {}).values())
    status = _read(DATA / 'he3_refine_status.json') or {}
    rule = status.get('revision') or 'he3-refine-v2-balanced'
    records, history = [], {}
    for row in verified:
        publication = row.get('publication') or {}
        revision = publication.get('revision') or '(未标注)'
        hit = row.get('hit')
        if not isinstance(hit, bool):
            continue
        model = _model_of(publication)
        if revision == rule:
            records.append((row.get('target_period'), hit, model))
        else:
            bucket = history.setdefault(revision, [0, 0])
            bucket[0] += 1
            bucket[1] += 1 if hit else 0
    row = _build('he3_refine', rule, records, 'data/he3_refine_hit_stats.json')
    row['history'] = [{'revision': revision, 'n': value[0], 'hits': value[1],
                       'rate': round(value[1] / value[0], 6) if value[0] else None}
                      for revision, value in sorted(history.items())]
    return row


def _cold_outcome(pred, numbers):
    positions = pred.get('selected_positions') or {}
    tails = pred.get('selected_tails') or {}
    he2 = pred.get('he2_keep')
    he3 = pred.get('he3_keep')
    if len(positions) != 3 or len(tails) != 2 or not he2 or not he3:
        return None
    for key, kill in positions.items():
        if key not in POS or numbers[POS[key]] == int(kill):
            return False
    for key, kill in tails.items():
        indexes = TAIL_INDEXES.get(key)
        if indexes is None:
            return None
        if sum(numbers[i] for i in indexes) % 10 == int(kill):
            return False
    pair_sums = {sum(numbers[i] for i in pair) % 10 for pair in PAIRS}
    if not pair_sums.intersection(int(digit) for digit in he2):
        return False
    triple_sums = {sum(numbers[i] for i in triple) % 10 for triple in TRIPLES}
    if not triple_sums.intersection(int(digit) for digit in he3):
        return False
    return True


def cum_cold(key, prefix):
    pattern = prefix + '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json'
    files = sorted(path for path in DATA.glob(prefix + '*.json')
                   if path.name[len(prefix):].startswith(tuple('0123456789'))
                   and path.name.count('-') == 2)
    records, history, latest_revision = [], {}, None
    for path in files:
        doc = _read(path) or {}
        revision = doc.get('revision')
        if revision:
            latest_revision = revision
        predictions = doc.get('predictions') or {}
        actuals = doc.get('actuals') or {}
        for target, pred in predictions.items():
            actual = actuals.get(target)
            if not isinstance(pred, dict) or not isinstance(actual, dict):
                continue
            numbers = actual.get('numbers')
            if not isinstance(numbers, list) or len(numbers) < 4:
                continue
            outcome = _cold_outcome(pred, [int(value) for value in numbers[:4]])
            if outcome is None:
                continue
            if revision and revision == latest_revision:
                records.append((target, outcome, None))
            else:
                bucket = history.setdefault(revision or '(未标注)', [0, 0])
                bucket[0] += 1
                bucket[1] += 1 if outcome else 0
    rule = latest_revision or ''
    row = _build(key, rule, records, 'data/' + prefix + 'YYYY-MM-DD.json')
    row['history'] = [{'revision': revision, 'n': value[0], 'hits': value[1],
                       'rate': round(value[1] / value[0], 6) if value[0] else None}
                      for revision, value in sorted(history.items())]
    return row


def cum_kill3():
    settlements = _read(DATA / 'kill3_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill3_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill3-tail-v4'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records = []
    for row in settlements.get('records') or []:
        if not isinstance(row, dict) or not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), bool(row.get('group_hit')), None))
    return _build('kill3_tail', rule, records, 'data/kill3_tail/settlements.json')


def build():
    rows = []
    errors = []
    for name, func in (('killer', cum_killer), ('framework', cum_framework),
                       ('selected_six', cum_selected_six), ('he3_refine', cum_he3),
                       ('cold_framework', lambda: cum_cold('cold_framework', 'cold_framework_')),
                       ('kill3_tail', cum_kill3)):
        try:
            row = func()
            if row and row.get('n'):
                rows.append(row)
        except Exception as error:  # keep other frameworks alive
            errors.append('%s: %s: %s' % (name, type(error).__name__, error))
    as_of = None
    for row in rows:
        period = row.get('last_period')
        if period and (as_of is None or str(period) > str(as_of)):
            as_of = period
    now = datetime.now()
    return {
        'version': 1,
        'generated_at': now.isoformat(timespec='seconds'),
        'as_of_period': as_of,
        'basis': BASIS,
        'min_sample': MIN_SAMPLE,
        'definition': '整组命中 = 一期全部项目同时命中；随机期望按现行规则精确枚举；覆盖率=已核验/计划期数',
        'errors': errors,
        'rows': rows,
    }


def main():
    try:
        report = build()
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
        fd, temporary = tempfile.mkstemp(dir=str(OUT.parent), prefix='.framework_hitrate.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, OUT)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        for row in report['rows']:
            print('{key} n={n} hits={hits} rate={rate} base={base} p={p} coverage={cov} verdict={verdict}'.format(
                key=row['key'], n=row['n'], hits=row['hits'],
                rate=('%.2f%%' % (row['rate'] * 100)) if row['rate'] is not None else '-',
                base=('%.2f%%' % (row['expected'] * 100)) if row['expected'] is not None else '-',
                p=row['p'], cov=('%.2f%%' % (row['coverage'] * 100)) if row['coverage'] is not None else '-',
                verdict=row['verdict']), flush=True)
        if report['errors']:
            print('errors: ' + ' | '.join(report['errors']), flush=True)
        print('framework_hitrate_cumulative written: %s rows=%d as_of=%s' % (OUT, len(report['rows']), report['as_of_period']), flush=True)
        return 0
    except Exception as error:
        print('framework_hitrate_cumulative failed: %s: %s' % (type(error).__name__, error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
