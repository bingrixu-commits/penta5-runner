"""整组命中率 vs 随机期望 派生统计（只读；面板「命中率基线」区块的数据源）。

口径（写死，避免再被误读）：
- 整组命中率 = 一个框架一期要求**全部项目同时命中**，不是单项准确率。
- 随机期望 = 对现行规则用 10^4 种开奖组合**精确枚举**校准出的纯随机基线：
  杀 1 码单项 90%；四位反向形态 15/16=93.75%；二数合留 4 码 98.08%；
  三数合留 5 码 93.75%、留 6 码 97.44%。整组期望是这些项目联合命中概率（41%~62%），
  不是单项基线，也不是简单相乘（保留码/形态/定位共享同一组开奖号，存在相关性）。
- 只统计开奖前已发布、开奖后已核验的记录；缺预测/待核验/冲突不进分母。
- 换规则版本（revision）即重置基线校准；未知 revision 显示「基线待校准」，不猜。

本模块只读 data/ 下既有统计 JSON 与当日封存账本，不调用模型、不改任何封存。
产物 data/hitrate_baseline.json，由 lib/hitrate_baseline_view.py 渲染进面板；
由 /root/run_stats.py 每轮刷新时调用 main()。
"""
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import math
import os
import tempfile

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'hitrate_baseline.json'
TZ = ZoneInfo('Asia/Shanghai')
BASELINE_BASIS = '现行规则 10^4 开奖组合精确枚举校准（2026-10-31）'

# (key, 规则 revision) -> (整组项目数, 精确随机期望)
RULE_BASELINE = {
    ('killer', 'conditional-transition-v2'): (9, 0.4162),
    ('framework', 'marginal-frequency-v2'): (8, 0.5559),
    ('selected_six', 'selected-six-independent-v4-minimax-forms'): (8, 0.5160),
    ('selected_six', 'selected-six-independent-v6-panel-context'): (8, 0.5160),
    ('selected_six', 'selected-six-independent-v7-single-tail'): (7, 0.5726),
    ('he3_refine', 'he3-refine-v2-balanced'): (1, 0.9375),
    ('he3_refine', 'he3-refine-v3-panel-context'): (1, 0.9375),
    ('kill3_tail', 'kill3-tail-v4'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v5-panel-context'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v6-three-positions'): (6, 0.5478),
    ('kill3_tail', 'kill3-tail-v7-two-counted'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v8-three-positions-hold3'): (6, 0.5478),
    ('kill3_tail', 'kill3-tail-v9-two-counted-hold3'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v10-two-counted-free'): (5, 0.6191),
    ('kill1_tail', 'kill1-tail-v1-one-position-one-form'): (3, 0.7538),
    ('kill2p_tail', 'kill2p-tail-v1-two-positions-one-tail-two-forms'): (5, 0.6203),
    ('kill2p_tail', 'kill2p-tail-v2-panel-context-rich'): (5, 0.6203),
    ('kill2_tail', 'kill2-tail-v1-two-positions-he2he3'): (7, 0.5708),
    ('kill2_tail', 'kill2-tail-v2-two-positions-he2he3-rot3'): (7, 0.5708),
    ('kill4_tail', 'kill4-tail-v1-three-positions-he2he3'): (8, 0.5038),
    ('kill5_tail', 'kill5-tail-v1-three-positions-he2he3'): (8, 0.5038),
    ('kill6_tail', 'kill6-tail-v1-three-positions-he2he3'): (8, 0.5038),
    ('kill7_tail', 'kill7-tail-v1-three-positions-he2he3'): (8, 0.5038),
}
LABELS = {
    'killer': '智能深排 killer（9 项）',
    'framework': '固定八码框架（8 项）',
    'selected_six': '七项独立框架 selected_six（7 项）',
    'he3_refine': '合尾 he3 剩余 5 码',
    'kill3_tail': 'AI 三位置杀码',
    'kill1_tail': 'AI 单位置杀码（3 项）',
    'kill2p_tail': 'AI 二位置杀码（5 项）',
    'kill2_tail': 'AI 二位置杀码 + 二数合/三数合（7 项）',
    'kill4_tail': 'AI 三位置杀码 + 二数合/三数合（8 项）',
    'kill5_tail': 'AI 三位置杀码 + 二数合/三数合（8 项·自由尾码）',
    'kill6_tail': 'AI 三位置杀码 + 二数合/三数合（8 项）',
    'kill7_tail': 'AI 三位置杀码 + 二数合/三数合（8 项·自由尾码）',
}
SINGLE_BASELINES = (
    ('杀 1 码', 0.9),
    ('四位反向形态', 0.9375),
    ('二数合留 4 码', 0.9808),
    ('三数合留 5 码', 0.9375),
    ('三数合留 6 码', 0.9744),
)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _business_start(moment):
    day = moment.date()
    if moment.time() < time(5, 0):
        day = day.fromordinal(day.toordinal() - 1)
    return day


def _period_business_day(period):
    try:
        day = datetime.strptime(str(period)[:8], '%Y%m%d').date()
    except (TypeError, ValueError):
        return None
    index = int(str(period)[8:11])
    moment = datetime.combine(day, time(0, 0)) + __import__('datetime').timedelta(minutes=5 * index, seconds=12)
    return _business_start(moment)


def p_two(hits, n, p0):
    if not n:
        return None
    z = (hits / n - p0) / math.sqrt(p0 * (1 - p0) / n)
    return math.erfc(abs(z) / math.sqrt(2))


def _row(key, rule, n, hits, source, note=''):
    items, expected = RULE_BASELINE.get((key, rule), (None, None))
    rate = (hits / n) if n else None
    delta = (rate - expected) if (rate is not None and expected is not None) else None
    p = p_two(hits, n, expected) if expected is not None else None
    if expected is None:
        verdict = '基线待校准'
    elif n < 200:
        verdict = '样本不足(n<200)'
    elif p is not None and p < 0.05:
        verdict = '偏离随机基线'
    else:
        verdict = '与随机无法区分'
    return {'key': key, 'label': LABELS[key], 'rule': rule, 'items': items,
            'n': n, 'hits': hits, 'rate': rate, 'expected': expected,
            'delta': delta, 'p': p, 'verdict': verdict,
            'source': source, 'note': note}


def _strategy_rows():
    stats = _read(DATA / 'strategy_comparison_stats.json')
    rows = []
    if not isinstance(stats, dict):
        return rows
    models = stats.get('models') or {}
    for key, items in (('killer', 9), ('framework', 8)):
        model = models.get(key) or {}
        overall = model.get('overall') or {}
        n = overall.get('total')
        hits = overall.get('hits')
        rule = model.get('strategy_id')
        if not isinstance(n, int) or not isinstance(hits, int) or not rule:
            continue
        row = _row(key, rule, n, hits, 'data/strategy_comparison_stats.json')
        rows.append(row)
    return rows


def _selected_six_row():
    stats = _read(DATA / 'selected_six_stats.json')
    if not isinstance(stats, dict):
        return []
    overall = stats.get('overall') or {}
    n = overall.get('total')
    hits = overall.get('hits')
    rule = stats.get('rule_revision')
    if not isinstance(n, int) or not isinstance(hits, int) or not rule:
        return []
    row = _row('selected_six', rule, n, hits, 'data/selected_six_stats.json')
    return [row]


def _he3_row():
    stats = _read(DATA / 'he3_refine_stats.json')
    status = _read(DATA / 'he3_refine_status.json') or {}
    if not isinstance(stats, dict):
        return []
    n = stats.get('total')
    hits = stats.get('hits')
    rule = status.get('revision')
    if not isinstance(n, int) or not isinstance(hits, int) or not rule:
        return []
    return [_row('he3_refine', rule, n, hits, 'data/he3_refine_stats.json')]


def _kill3_rows():
    seals = _read(DATA / 'kill3_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill3_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill3_tail', rule, total, hits, 'data/kill3_tail/settlements.json')]


def _kill1_rows():
    seals = _read(DATA / 'kill1_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill1_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill1_tail', rule, total, hits, 'data/kill1_tail/settlements.json')]


def _kill2p_rows():
    seals = _read(DATA / 'kill2p_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill2p_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill2p_tail', rule, total, hits, 'data/kill2p_tail/settlements.json')]


def _kill2_rows():
    seals = _read(DATA / 'kill2_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill2_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill2_tail', rule, total, hits, 'data/kill2_tail/settlements.json')]


def _kill4_rows():
    seals = _read(DATA / 'kill4_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill4_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill4_tail', rule, total, hits, 'data/kill4_tail/settlements.json')]


def _kill6_rows():
    seals = _read(DATA / 'kill6_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill6_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill6_tail', rule, total, hits, 'data/kill6_tail/settlements.json')]


def _kill5_rows():
    seals = _read(DATA / 'kill5_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill5_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill5_tail', rule, total, hits, 'data/kill5_tail/settlements.json')]


def _kill7_rows():
    seals = _read(DATA / 'kill7_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill7_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill7_tail', rule, total, hits, 'data/kill7_tail/settlements.json')]


def _kill5_rows():
    seals = _read(DATA / 'kill5_tail' / 'seals.json') or {}
    settlements = _read(DATA / 'kill5_tail' / 'settlements.json') or {}
    records = seals.get('records') if isinstance(seals, dict) else None
    rows = settlements.get('records') if isinstance(settlements, dict) else None
    if not records or not rows:
        return []
    rule = records[-1].get('rule_version')
    if not rule:
        return []
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in records if isinstance(record, dict)}
    now = datetime.now(TZ)
    business_day = _business_start(now)
    hits = 0
    total = 0
    for record in rows:
        if not isinstance(record, dict) or not record.get('verified'):
            continue
        if 'counted' not in record:
            continue
        if (record.get('rule_version') != rule
                and seal_rule.get(str(record.get('ref_period'))) != rule):
            continue
        day = _period_business_day(record.get('target_period'))
        if day != business_day:
            continue
        total += 1
        hits += int(bool(record.get('group_hit')))
    if not total:
        return []
    return [_row('kill5_tail', rule, total, hits, 'data/kill5_tail/settlements.json')]


def _as_of(rows):
    candidates = []
    six = _read(DATA / 'selected_six_stats.json') or {}
    if six.get('latest_settled_period'):
        candidates.append(str(six['latest_settled_period']))
    he3 = _read(DATA / 'he3_refine_stats.json') or {}
    if he3.get('latest_settled_period'):
        candidates.append(str(he3['latest_settled_period']))
    board = _read(DATA / 'strategy_comparison_stats.json') or {}
    if board.get('latest_known_closed_period'):
        candidates.append(str(board['latest_known_closed_period']))
    return max(candidates) if candidates else None


def build():
    rows = []
    rows.extend(_strategy_rows())
    rows.extend(_selected_six_row())
    rows.extend(_he3_row())
    rows.extend(_kill3_rows())
    rows.extend(_kill1_rows())
    rows.extend(_kill2p_rows())
    rows.extend(_kill2_rows())
    rows.extend(_kill4_rows())
    rows.extend(_kill5_rows())
    rows.extend(_kill6_rows())
    rows.extend(_kill7_rows())
    now = datetime.now(TZ)
    return {
        'version': 1,
        'generated_at': now.isoformat(timespec='seconds'),
        'biz_date': _business_start(now).strftime('%Y-%m-%d'),
        'as_of_period': _as_of(rows),
        'basis': BASELINE_BASIS,
        'min_sample': 200,
        'definition': '整组命中 = 该框架一期要求全部项目同时命中；随机期望按现行规则精确枚举校准',
        'single_baselines': [{'label': label, 'baseline': value} for label, value in SINGLE_BASELINES],
        'rows': rows,
    }


def _atomic_write(report):
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    fd, temporary = tempfile.mkstemp(dir=str(OUT.parent), prefix='.hitrate_baseline.', suffix='.tmp')
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


def main():
    try:
        report = build()
        _atomic_write(report)
        for row in report['rows']:
            print('{key} n={n} hits={hits} rate={rate} expected={expected} verdict={verdict}'.format(
                key=row['key'], n=row['n'], hits=row['hits'],
                rate=('%.2f%%' % (row['rate'] * 100)) if row['rate'] is not None else '-',
                expected=('%.2f%%' % (row['expected'] * 100)) if row['expected'] is not None else '-',
                verdict=row['verdict']), flush=True)
        print('hitrate_baseline written: %s rows=%d as_of=%s' % (OUT, len(report['rows']), report['as_of_period']), flush=True)
        return 0
    except Exception as error:
        print('hitrate_baseline failed: %s: %s' % (type(error).__name__, error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
