"""位置杀码「稳定化」候选影子（只读，不改现网封存/统计/攻击语）。

候选策略 holdK-until-miss：某计入位置杀码命中（实际开奖号 != 杀码）后继续沿用，
最多连用 K 期；失手或连用满 K 期后改用模型本期新选码。
本影子同时对照多档：hold2 / hold3 / hold5 / hold-until-miss（不设上限）。

数据源：kill1/kill2p/kill2/kill3/kill4 各框架已结算(verified)记录里的 positions/counted/actual，
逐期确定性回放；只写 data/position_kill_shadow.json 与面板 POSITION_KILL_SHADOW 区块，
不调用模型、不改任何封存/统计/攻击语。
"""
from datetime import datetime
from pathlib import Path
import json

from ai_verified_stats import next_period
from panel_io import update_panel
from stats_history import TZ, business_start

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'position_kill_shadow.json'
MARKER = 'POSITION_KILL_SHADOW'
START = '<!-- ' + MARKER + '_START -->'
END = '<!-- ' + MARKER + '_END -->'
ANCHOR = '<!-- TAIL_STABILITY_SHADOW_END -->'
POS_INDEX = {'q': 0, 'b': 1, 's': 2, 'g': 3}
FRAMEWORKS = (
    ('kill1_tail', 'AI单位置(kill1)'),
    ('kill2p_tail', 'AI二位置(kill2p)'),
    ('kill2_tail', 'AI二位置+二三合(kill2)'),
    ('kill3_tail', 'AI三位置(kill3)'),
    ('kill4_tail', 'AI三位置+二三合(kill4)'),
)
POLICIES = (
    ('hold2', 2, '命中沿用·最多连用2期·失手换码'),
    ('hold3', 3, '命中沿用·最多连用3期·失手换码'),
    ('hold5', 5, '命中沿用·最多连用5期·失手换码'),
    ('hold-until-miss', 9999, '命中沿用·不设上限·失手换码'),
)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _period(value):
    text = str(value or '')
    if len(text) == 11 and text.isdigit():
        return text
    return None


def _collect():
    """Return {framework_label: [(target, counted, positions, actual_nums)]} oldest-first."""
    result = {}
    for src, name in FRAMEWORKS:
        records = _read(DATA / src / 'settlements.json', {}) or {}
        records = records.get('records') or []
        rows = []
        for record in records:
            if not record.get('verified'):
                continue
            target = str(record.get('target_period') or '')
            actual = record.get('actual')
            if not target or not isinstance(actual, list) or len(actual) < 4:
                continue
            positions = record.get('positions') or {}
            counted = [key for key in (record.get('counted') or []) if key in POS_INDEX]
            if not counted:
                continue
            rows.append((target, counted, positions, [int(x) for x in actual[:4]]))
        rows.sort(key=lambda row: row[0])
        if rows:
            result[name] = rows
    return result


def _actual(rows):
    """现行 = 封存杀码（模型每期新选 + max_consecutive=3 强制换码）。"""
    n = hits = changes = 0
    prev = {}
    for _target, counted, positions, actual in rows:
        for key in counted:
            kill = (positions.get(key) or {}).get('kill')
            if kill is None:
                continue
            used = str(kill)
            hit = (int(used) != actual[POS_INDEX[key]])
            n += 1
            hits += 1 if hit else 0
            if key in prev and prev[key] != used:
                changes += 1
            prev[key] = used
    return {'n': n, 'hits': hits, 'rate': (hits / n) if n else None, 'changes': changes}


def _simulate(rows, hold_max):
    """候选 holdK-until-miss 逐期确定性回放。"""
    n = hits = changes = 0
    state = {}
    prev = {}
    for _target, counted, positions, actual in rows:
        for key in counted:
            kill = (positions.get(key) or {}).get('kill')
            if kill is None:
                continue
            model = str(kill)
            st = state.get(key)
            if st is not None and st['prev_hit'] and st['run'] < hold_max:
                used = st['digit']
                run = st['run'] + 1
            else:
                used = model
                run = 1
            hit = (int(used) != actual[POS_INDEX[key]])
            n += 1
            hits += 1 if hit else 0
            if key in prev and prev[key] != used:
                changes += 1
            prev[key] = used
            state[key] = {'digit': used, 'run': run, 'prev_hit': hit}
    return {'n': n, 'hits': hits, 'rate': (hits / n) if n else None, 'changes': changes}


def _stats(rows):
    result = {'periods': len(rows), 'actual': _actual(rows), 'policies': {}}
    for key, hold_max, _label in POLICIES:
        result['policies'][key] = _simulate(rows, hold_max)
    return result


def _entry(rows, forward_start):
    forward = [row for row in rows if forward_start and row[0] >= forward_start]
    return {'backfill': _stats(rows), 'forward': _stats(forward)}


def build(*, root=ROOT):
    collected = _collect()
    previous = _read(Path(root) / 'data' / 'position_kill_shadow.json', {}) or {}
    start = _period(previous.get('start_period'))
    if not start:
        latest = []
        for rows in collected.values():
            if rows:
                latest.append(max(row[0] for row in rows))
        start = next_period(max(latest)) if latest else None
    frameworks = {}
    for name, rows in collected.items():
        frameworks[name] = _entry(rows, start)
    now = datetime.now(TZ)
    return {'version': 1,
            'generated_at': now.isoformat(timespec='seconds'),
            'start_period': start,
            'biz_date': business_start(now).isoformat(),
            'note': '只读影子对照：命中沿用（hold2/3/5/不设上限）；不改任何封存与正式统计。',
            'policies': {key: {'hold_max': hold_max, 'label': label}
                         for key, hold_max, label in POLICIES},
            'frameworks': frameworks}


def _pct(value):
    return ('%.2f%%' % (100.0 * value)) if isinstance(value, (int, float)) else '-'


def _num(value):
    return str(value) if value is not None else '0'


def render_block(data):
    order = [name for _src, name in FRAMEWORKS if name in (data.get('frameworks') or {})]
    body = []
    for name in order:
        item = data['frameworks'][name]
        back = item.get('backfill') or {}
        actual = back.get('actual') or {}
        policies = back.get('policies') or {}
        fwd = item.get('forward') or {}
        cells = []
        cells.append('<td class="pk-name"><b>' + name + '</b><div class="pk-foot">回放 '
                     + _num(back.get('periods')) + ' 期 / ' + _num(actual.get('n')) + ' 注</div></td>')
        cells.append('<td class="pk-cur">' + _pct(actual.get('rate')) + '</td>')
        for key, _k, _label in POLICIES:
            p = policies.get(key) or {}
            cells.append('<td>' + _pct(p.get('rate')) + '</td>')
        cells.append('<td class="pk-cur">' + _num(actual.get('changes')) + '</td>')
        for key, _k, _label in POLICIES:
            p = policies.get(key) or {}
            cells.append('<td>' + _num(p.get('changes')) + '</td>')
        cells.append('<td>' + _num(fwd.get('periods')) + '</td>')
        body.append('<tr>' + ''.join(cells) + '</tr>')
    policy_cells = ''.join('<th>' + label.split('·')[1] + '</th>' for _key, _k, label in POLICIES)
    forward_start = str(data.get('start_period') or '—')
    return (
        START + '\n<section id="position-kill-shadow" aria-label="位置杀码稳定化候选影子">'
        '<style>#position-kill-shadow{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;'
        'border:1px solid #a7f3d0;border-radius:12px;background:#f0fdf4;color:#1e293b;font:14px/1.6 sans-serif;overflow-wrap:anywhere}'
        '#position-kill-shadow h3{font-size:18px;color:#047857;margin:0 0 4px}'
        '#position-kill-shadow .pk-meta{font-size:13px;color:#64748b;margin-bottom:8px}'
        '#position-kill-shadow table{width:100%;border-collapse:collapse;font-size:12.5px}'
        '#position-kill-shadow th,#position-kill-shadow td{padding:5px 6px;text-align:right;border-top:1px dashed #bbf7d0;vertical-align:top}'
        '#position-kill-shadow th:first-child,#position-kill-shadow td:first-child{text-align:left}'
        '#position-kill-shadow .pk-cur{color:#065f46;font-weight:600}'
        '#position-kill-shadow .pk-name{text-align:left}'
        '#position-kill-shadow .pk-foot{font-size:11px;color:#64748b}</style>'
        '<h3>🧪 位置杀码稳定化候选（影子·不改现网）</h3>'
        '<div class="pk-meta">候选策略 holdK-until-miss：位置杀码命中后继续沿用、最多连用 K 期，失手或满 K 期换码。'
        '回放=同批已封存记录按真实开奖确定性重放；前瞻=从 ' + forward_start + ' 起逐期跟踪。本区块只做对照，'
        '不改变任何封存、攻击语与正式统计。生成 ' + str(data.get('generated_at') or '-') + '</div>'
        '<table><thead><tr>'
        '<th rowspan="2">框架</th><th colspan="6" style="text-align:center">命中率（回放）</th>'
        '<th colspan="6" style="text-align:center">换码次数（回放）</th><th rowspan="2">前瞻n</th></tr>'
        '<tr><th>现行</th>' + policy_cells
        + '<th>现行</th>' + policy_cells + '</tr></thead><tbody>'
        + ''.join(body) + '</tbody></table>'
        '<div class="pk-foot">单位置杀1码随机基线 90%。稳定化不会提高命中率（数学上限），只降低换码频率；'
        '候选命中沿用可避免「命中码被每期重新选码替换」的无效换码。现行 kill3 另有 max_consecutive=3 强制换码，'
        '是其回放命中率略低于候选直至失手档的原因之一。前瞻 n 满 50 期再按预登记判据复核，现网口径与封存一律不动。</div>'
        '</section>\n' + END)


def apply_block(html, fragment):
    if html.count(START) == 1 and html.count(END) == 1:
        head = html.index(START)
        tail = html.index(END) + len(END)
        return html[:head] + fragment + html[tail:]
    if html.count(START) or html.count(END):
        raise ValueError('position-kill-shadow-marker-ambiguous')
    if html.count(ANCHOR) == 1:
        position = html.index(ANCHOR) + len(ANCHOR)
        return html[:position] + '\n' + fragment + html[position:]
    if html.count('</body>') == 1:
        return html.replace('</body>', fragment + '\n</body>', 1)
    raise ValueError('position-kill-shadow-anchor-missing')


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
    path = Path(root) / 'data' / 'position_kill_shadow.json'
    _write_atomic(path, payload)
    fragment = render_block(data)

    def transform(html):
        return apply_block(html, fragment)

    update_panel(transform, path=Path(root) / 'panel.html')
    return data
