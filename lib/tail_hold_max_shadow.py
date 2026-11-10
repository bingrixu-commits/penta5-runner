"""合尾杀码「稳定化」hold_max 多档候选影子（只读，不改现网封存/统计/攻击语）。

覆盖 4 个合尾框架：AI自选2~4位置(kill1)、AI三位置(kill3)、固定八码、killer 九项。
候选策略 holdK-until-miss：本组尾码命中后继续沿用，最多连用 K 期；失手或满 K 期改用模型本期新选。
同时对照 hold2 / hold3 / hold5 / hold-until-miss（不设上限）。

数据源：各框架已封存记录里的封存尾码 + 模型本期新选（tail_hold3 / tail_policy 元数据）+
真实开奖，逐期确定性回放。只写 data/tail_hold_max_shadow.json 与面板
TAIL_HOLD_MAX_SHADOW 区块，不调用模型、不改任何封存/统计/攻击语。

口径要点：
* 命中 = 实际尾值 != 杀码（单尾杀 1 码随机基线 90%；两项合取约 81%，三项约 72.9%）。
* 稳定化本身不提高命中率（数学上限），只降低换码频率。
* 现行 = 直接使用封存尾码序列；hold3 = 现网已生效口径，回放应与封存逐期一致（hold3_sequence_match 自检）。
"""
from datetime import datetime
from pathlib import Path
import json

from ai_verified_stats import next_period
from panel_io import update_panel
from stats_history import TZ, business_start

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'tail_hold_max_shadow.json'
MARKER = 'TAIL_HOLD_MAX_SHADOW'
START = '<!-- ' + MARKER + '_START -->'
END = '<!-- ' + MARKER + '_END -->'
ANCHOR = '<!-- POSITION_KILL_SHADOW_END -->'

TAIL_INDEXES = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
TAIL_KEYS = frozenset(TAIL_INDEXES)
TAIL_LABELS = {'all': '千百十个', 'qbs': '千百十', 'qbg': '千百个',
               'qsg': '千十个', 'bsg': '百十个'}
KILLER_FIELDS = (('he_all', 'all'), ('he_q3', 'qbs'), ('he_h3', 'bsg'))
FRAMEWORK_FIELDS = (('he_q3_kill', 'qbs'), ('he_h3_kill', 'bsg'))

# (框架 id, 面板名, 分组粒度, 沿用是否要求上一封存目标期与当前参考期连续)
FRAMEWORKS = (
    ('kill1_tail', 'AI自选2~4位置(kill1)', 'single', False),
    ('kill3_tail', 'AI三位置(kill3)', 'single', False),
    ('framework', '固定八码框架', 'pair', True),
    ('killer', 'killer九项', 'triple', True),
)
POLICIES = (
    ('hold2', 2, '连用2期'),
    ('hold3', 3, '连用3期(现网)'),
    ('hold5', 5, '连用5期'),
    ('hold-until-miss', 9999, '不设上限'),
)
MODE_TEXT = {'single': '单尾杀1码', 'pair': '两项合尾一组', 'triple': '三项合尾一组'}


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _period(value):
    text = str(value or '')
    return text if len(text) == 11 and text.isdigit() else None


def _digit(value):
    return int(value) % 10


def _tail_value(nums, key):
    return sum(nums[index] for index in TAIL_INDEXES[key]) % 10


def _hits(items, nums):
    return tuple(_tail_value(nums, key) != digit for key, digit in items)


def _items(fields):
    return tuple(sorted(fields))


def _row(target, nums, sealed, model, has_hold, ref=None):
    return {'target': target, 'nums': nums, 'sealed': sealed,
            'model': model, 'hold': has_hold, 'ref': ref}


def _dedupe(rows):
    by_target = {}
    for row in rows:
        by_target[row['target']] = row
    return sorted(by_target.values(), key=lambda row: row['target'])


def _r1_data():
    """Return (actuals: target -> [4 ints], killer rows) from r1_stats.json."""
    doc = _read(DATA / 'r1_stats.json', {}) or {}
    actuals = {}
    for entry in doc.get('verified_evaluations') or []:
        if not isinstance(entry, dict):
            continue
        target = _period(entry.get('target_period'))
        raw = entry.get('actual')
        if not target or not isinstance(raw, list) or len(raw) < 4:
            continue
        try:
            actuals[target] = [int(value) for value in raw[:4]]
        except (TypeError, ValueError):
            continue
    seals = doc.get('prediction_seals') or {}
    rows = []
    for ref in sorted(seals):
        seal = seals.get(ref)
        if not isinstance(seal, dict):
            continue
        target = _period(seal.get('target_period'))
        nums = actuals.get(target) if target else None
        if nums is None:
            continue
        try:
            sealed = _items((key, _digit(seal[field])) for field, key in KILLER_FIELDS)
        except (KeyError, TypeError, ValueError):
            continue
        hold = (seal.get('model_metadata') or {}).get('tail_hold3') if isinstance(seal.get('model_metadata'), dict) else None
        model = sealed
        has_hold = False
        if isinstance(hold, dict) and isinstance(hold.get('model_tails'), dict):
            tails = hold['model_tails']
            try:
                model = _items((key, _digit(tails[field])) for field, key in KILLER_FIELDS)
                has_hold = True
            except (KeyError, TypeError, ValueError):
                model = sealed
                has_hold = False
        rows.append(_row(target, nums, sealed, model, has_hold, ref=ref))
    return actuals, _dedupe(rows)


def _framework_rows(actuals):
    doc = _read(DATA / 'r1_framework_seals.json', {}) or {}
    seals = doc.get('prediction_seals') or {}
    rows = []
    for ref in sorted(seals):
        seal = seals.get(ref)
        if not isinstance(seal, dict):
            continue
        target = _period(seal.get('target_period'))
        nums = actuals.get(target) if target else None
        if nums is None:
            continue
        response = seal.get('response') if isinstance(seal.get('response'), dict) else {}
        try:
            sealed = _items((key, _digit(response[field])) for field, key in FRAMEWORK_FIELDS)
        except (KeyError, TypeError, ValueError):
            continue
        hold = (seal.get('model_metadata') or {}).get('tail_hold3') if isinstance(seal.get('model_metadata'), dict) else None
        model = sealed
        has_hold = False
        if isinstance(hold, dict) and isinstance(hold.get('model_tails'), dict):
            tails = hold['model_tails']
            try:
                model = _items((key, _digit(tails[field])) for field, key in FRAMEWORK_FIELDS)
                has_hold = True
            except (KeyError, TypeError, ValueError):
                model = sealed
                has_hold = False
        rows.append(_row(target, nums, sealed, model, has_hold, ref=ref))
    return _dedupe(rows)


def _kill_rows(src):
    settlements = _read(DATA / src / 'settlements.json', {}) or {}
    actuals = {}
    for record in settlements.get('records') or []:
        if not isinstance(record, dict):
            continue
        target = _period(record.get('target_period'))
        raw = record.get('actual')
        if not target or not isinstance(raw, list) or len(raw) < 4:
            continue
        try:
            actuals[target] = [int(value) for value in raw[:4]]
        except (TypeError, ValueError):
            continue
    seals = _read(DATA / src / 'seals.json', {}) or {}
    rows = []
    for seal in seals.get('records') or []:
        if not isinstance(seal, dict):
            continue
        target = _period(seal.get('target_period'))
        nums = actuals.get(target) if target else None
        if nums is None:
            continue
        tail = seal.get('tail') if isinstance(seal.get('tail'), dict) else {}
        tail_key = str(tail.get('key') or '')
        tail_key = tail_key[5:] if tail_key.startswith('tail_') else tail_key
        if tail_key not in TAIL_KEYS:
            continue
        try:
            sealed = ((tail_key, _digit(tail.get('kill'))),)
        except (TypeError, ValueError):
            continue
        policy = seal.get('tail_policy') if isinstance(seal.get('tail_policy'), dict) else None
        model = sealed
        has_hold = False
        if isinstance(policy, dict) and isinstance(policy.get('model_tail'), dict):
            pick = policy['model_tail']
            pick_key = str(pick.get('key') or '')
            pick_key = pick_key[5:] if pick_key.startswith('tail_') else pick_key
            if pick_key in TAIL_KEYS:
                try:
                    model = ((pick_key, _digit(pick.get('kill'))),)
                    has_hold = True
                except (TypeError, ValueError):
                    model = sealed
                    has_hold = False
        rows.append(_row(target, nums, sealed, model, has_hold,
                         ref=str(seal.get('ref_period') or '') or None))
    return _dedupe(rows)


def _collect():
    """Return {framework_id: (rows, contiguous_required)} oldest-first by target."""
    actuals, killer_rows = _r1_data()
    collected = {'killer': killer_rows}
    collected['framework'] = _framework_rows(actuals)
    collected['kill3_tail'] = _kill_rows('kill3_tail')
    collected['kill1_tail'] = _kill_rows('kill1_tail')
    result = {}
    for framework_id, _label, _mode, contiguous in FRAMEWORKS:
        result[framework_id] = (collected.get(framework_id) or [], contiguous)
    return result


def _rate(hits, n):
    return (hits / n) if n else None


def _policy_start(rows):
    """First period where the live hold policy was active.

    Before that period the live seal equals the pure model pick, so the replay
    must keep model picks there and only start recycling from this index on.
    """
    for index, row in enumerate(rows):
        if row['hold']:
            return index
    return len(rows)


def _group_stats(rows, groups, reused, start, end):
    n = hits = changes = group_n = group_hits = reused_periods = 0
    for index in range(start, end):
        items = groups[index]
        flags = _hits(items, rows[index]['nums'])
        n += len(items)
        hits += sum(1 for flag in flags if flag)
        group_n += 1
        group_hits += 1 if all(flags) else 0
        if index > 0 and items != groups[index - 1]:
            changes += 1
        if reused is not None and reused[index]:
            reused_periods += 1
    return {'n': n, 'hits': hits, 'rate': _rate(hits, n), 'changes': changes,
            'group_n': group_n, 'group_hits': group_hits,
            'group_rate': _rate(group_hits, group_n),
            'reuse_periods': reused_periods}


def _simulate(rows, hold_max, contiguous_required, start):
    """Deterministic holdK-until-miss replay. Returns (items, reused) per period.

    Periods before start are forced to the model pick (which equals the sealed
    value there); the recycling rule only applies from start on. State
    (previous items / hits / streak) is still carried across the boundary.
    """
    previous_items = previous_hits = previous_target = None
    streak = 0
    groups = []
    reused_flags = []
    for index, row in enumerate(rows):
        target = row['target']
        if index < start or not row['hold']:
            items = row['model']
            reused = False
        else:
            current_ref = row.get('ref')
            if current_ref:
                contiguous = bool(previous_target and previous_target == current_ref)
            else:
                contiguous = bool(previous_target
                                  and int(target) - int(previous_target) == 1)
            can_reuse = (previous_items is not None and all(previous_hits)
                         and streak < hold_max
                         and (not contiguous_required or contiguous))
            if can_reuse:
                items = previous_items
                reused = True
            else:
                items = row['model']
                reused = False
        flags = _hits(items, row['nums'])
        if previous_items is not None and items == previous_items:
            streak += 1
        else:
            streak = 1
        previous_items = items
        previous_hits = flags
        previous_target = target
        groups.append(items)
        reused_flags.append(reused)
    return groups, reused_flags


def _groups_for(rows, contiguous_required):
    start = _policy_start(rows)
    mapping = {'actual': ([row['sealed'] for row in rows], None)}
    for key, hold_max, _short in POLICIES:
        mapping[key] = _simulate(rows, hold_max, contiguous_required, start)
    return start, mapping


def _window_stats(rows, mapping, start, end=None, with_match=False):
    end = len(rows) if end is None else end
    actual = _group_stats(rows, mapping['actual'][0], None, start, end)
    policies = {}
    for key, _hold_max, _short in POLICIES:
        groups, reused = mapping[key]
        policies[key] = _group_stats(rows, groups, reused, start, end)
    payload = {'periods': max(0, end - start),
               'from_period': rows[start]['target'] if start < len(rows) else None,
               'actual': actual, 'policies': policies}
    if with_match:
        groups = mapping['hold3'][0]
        hold_indexes = [index for index in range(start, end) if rows[index]['hold']]
        matched = sum(1 for index in hold_indexes
                      if groups[index] == rows[index]['sealed'])
        periods = len(hold_indexes)
        payload['hold3_sequence_match'] = {
            'periods': periods, 'matched': matched,
            'rate': _rate(matched, periods),
            'from_period': payload['from_period']}
    return payload


def _entry(rows, forward_start, contiguous_required):
    start, mapping = _groups_for(rows, contiguous_required)
    backfill = _window_stats(rows, mapping, start, with_match=True)
    forward_index = len(rows)
    if forward_start:
        for index in range(start, len(rows)):
            if rows[index]['target'] >= forward_start:
                forward_index = index
                break
    forward = _window_stats(rows, mapping, forward_index)
    return {'backfill': backfill, 'forward': forward}


def build(*, root=ROOT):
    collected = _collect()
    previous = _read(Path(root) / 'data' / 'tail_hold_max_shadow.json', {}) or {}
    start = _period(previous.get('start_period'))
    latest = [rows[-1]['target'] for rows, _contig in collected.values() if rows]
    if not start:
        start = next_period(max(latest)) if latest else None
    frameworks = {}
    for framework_id, label, mode, contiguous in FRAMEWORKS:
        rows = collected.get(framework_id, ([], contiguous))[0]
        if not rows:
            continue
        frameworks[framework_id] = {'label': label, 'mode': mode,
                                    **_entry(rows, start, contiguous)}
    now = datetime.now(TZ)
    return {'version': 1,
            'generated_at': now.isoformat(timespec='seconds'),
            'start_period': start,
            'biz_date': business_start(now).isoformat(),
            'note': '只读影子对照：合尾 holdK-until-miss（命中沿用、失手/满K期换码）；不改任何封存与正式统计。',
            'policies': {key: {'hold_max': hold_max, 'label': short}
                         for key, hold_max, short in POLICIES},
            'frameworks': frameworks}


def _pct(value):
    return ('%.2f%%' % (100.0 * value)) if isinstance(value, (int, float)) else '-'


def _num(value):
    return str(value) if value is not None else '0'


def render_block(data):
    frameworks = data.get('frameworks') or {}
    order = [item for item in FRAMEWORKS if item[0] in frameworks]
    body = []
    for framework_id, label, mode, _contig in order:
        item = frameworks[framework_id]
        back = item.get('backfill') or {}
        actual = back.get('actual') or {}
        policies = back.get('policies') or {}
        forward = item.get('forward') or {}
        match = back.get('hold3_sequence_match') or {}
        match_text = ''
        if match.get('periods'):
            match_text = ' · hold3复现封存 %s/%s' % (_num(match.get('matched')),
                                                     _num(match.get('periods')))
        cells = ['<td class="thm-name"><b>%s</b><div class="thm-foot">%s · 回放 %s 期 / %s 注%s</div></td>'
                 % (label, MODE_TEXT.get(mode, mode), _num(back.get('periods')),
                    _num(actual.get('n')), match_text),
                 '<td class="thm-cur">%s</td>' % _pct(actual.get('rate'))]
        for key, _hold_max, _short in POLICIES:
            policy = policies.get(key) or {}
            reuse = policy.get('reuse_periods')
            title = '沿用 %s 期' % _num(reuse) if reuse is not None else ''
            cells.append('<td title="%s">%s</td>' % (title, _pct(policy.get('rate'))))
        cells.append('<td class="thm-cur">%s</td>' % _num(actual.get('changes')))
        for key, _hold_max, _short in POLICIES:
            cells.append('<td>%s</td>' % _num((policies.get(key) or {}).get('changes')))
        cells.append('<td>%s</td>' % _num(forward.get('periods')))
        body.append('<tr>' + ''.join(cells) + '</tr>')
    policy_cells = ''.join('<th>%s</th>' % short for _key, _hold_max, short in POLICIES)
    forward_start = str(data.get('start_period') or '-')
    return (
        START + '\n<section id="tail-hold-max-shadow" aria-label="合尾杀码稳定化候选影子">'
        '<style>#tail-hold-max-shadow{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;'
        'border:1px solid #c7d2fe;border-radius:12px;background:#eef2ff;color:#1e293b;font:14px/1.6 sans-serif;overflow-wrap:anywhere}'
        '#tail-hold-max-shadow h3{font-size:18px;color:#3730a3;margin:0 0 4px}'
        '#tail-hold-max-shadow .thm-meta{font-size:13px;color:#64748b;margin-bottom:8px}'
        '#tail-hold-max-shadow table{width:100%;border-collapse:collapse;font-size:12.5px}'
        '#tail-hold-max-shadow th,#tail-hold-max-shadow td{padding:5px 6px;text-align:right;border-top:1px dashed #c7d2fe;vertical-align:top}'
        '#tail-hold-max-shadow th:first-child,#tail-hold-max-shadow td:first-child{text-align:left}'
        '#tail-hold-max-shadow .thm-cur{color:#3730a3;font-weight:600}'
        '#tail-hold-max-shadow .thm-name{text-align:left}'
        '#tail-hold-max-shadow .thm-foot{font-size:11px;color:#64748b}</style>'
        '<h3>🧪 合尾杀码稳定化候选（多档 hold_max·影子·不改现网）</h3>'
        '<div class="thm-meta">候选策略 holdK-until-miss：本组尾码命中后继续沿用、最多连用 K 期，失手或满 K 期换码。'
        '回放=自各框架稳定化上线期起、按已封存记录+真实开奖确定性重放；前瞻=从 ' + forward_start + ' 起逐期跟踪。'
        '本区块只做对照，不改变任何封存、攻击语与正式统计。生成 ' + str(data.get('generated_at') or '-') + '</div>'
        '<table><thead><tr>'
        '<th rowspan="2">框架</th><th colspan="5" style="text-align:center">命中率（回放·逐项）</th>'
        '<th colspan="5" style="text-align:center">换码次数（回放·按组）</th><th rowspan="2">前瞻n</th></tr>'
        '<tr><th>现行</th>' + policy_cells + '<th>现行</th>' + policy_cells + '</tr></thead><tbody>'
        + ''.join(body) + '</tbody></table>'
        '<div class="thm-foot">单尾杀1码随机基线 90%；两项合尾一组全中约 81%，三项约 72.9%（近似独立）。'
        '稳定化不会提高命中率（数学上限），只降低换码频率；hold3 是现网已生效口径，其回放应与封存逐期一致（即上表 hold3复现封存）。'
        'AI自选2~4位置/AI三位置为单尾，固定八码为两项一组，killer 为三项一组；组内任一项失手即整组换码。'
        '前瞻 n 满 50 期后再按预登记判据复核，现网口径与封存一律不动。</div>'
        '</section>\n' + END)


def apply_block(html, fragment):
    if html.count(START) == 1 and html.count(END) == 1:
        head = html.index(START)
        tail = html.index(END) + len(END)
        return html[:head] + fragment + html[tail:]
    if html.count(START) or html.count(END):
        raise ValueError('tail-hold-max-shadow-marker-ambiguous')
    if html.count(ANCHOR) == 1:
        position = html.index(ANCHOR) + len(ANCHOR)
        return html[:position] + '\n' + fragment + html[position:]
    if html.count('</body>') == 1:
        return html.replace('</body>', fragment + '\n</body>', 1)
    raise ValueError('tail-hold-max-shadow-anchor-missing')


def _write_atomic(path, text):
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def refresh(*, root=ROOT):
    data = build(root=root)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    _write_atomic(Path(root) / 'data' / 'tail_hold_max_shadow.json', payload)
    try:
        import stats_page_view
        stats_page_view.write_page(root=Path(root))
    except Exception as error:
        print('STATS_PAGE_REFRESH_FAILED error_type=' + type(error).__name__, flush=True)
    return data
