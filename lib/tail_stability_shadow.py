"""合尾杀码「稳定化」候选影子（只读，不改现网封存/统计）。

政策 tail-hold3-until-miss：同一尾类+尾码命中后继续沿用，最多连用 3 期；
失手或连用满 3 期后改用模型本期新选码。

数据源：各框架已封存记录 + 真实开奖（与生产统计同源），逐期确定性回放；
该模块只写 data/tail_stability_shadow.json 与面板衍生区块，不改任何封存、
不调用模型、不影响命中统计。
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import glob
import json
import math

from ai_verified_stats import next_period
from panel_io import update_panel
from stats_history import TZ, business_start

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'tail_stability_shadow.json'
MARKER = 'TAIL_STABILITY_SHADOW'
START = '<!-- ' + MARKER + '_START -->'
END = '<!-- ' + MARKER + '_END -->'
POLICY = 'tail-hold3-until-miss'
POLICY_LABEL = '命中沿用、最多连用3期，失手换码'
ANCHOR = '<!-- HITRATE_BASE_END -->'
TAIL = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
        'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _tail_value(nums, key):
    return sum(nums[index] for index in TAIL[key]) % 10


def _items(mapping):
    return sorted(((key, int(value)) for key, value in (mapping or {}).items()),
                  key=lambda pair: pair[0])


def _collect():
    """Return {framework: [(target, numbers, items)]} oldest-first."""
    result = {}
    # killer 已于 2026-10-31 22:15 启用 triple-hold3；至此五项框架全部转正，影子不再纳入。
    # 固定八码 已于 2026-10-31 22:00 启用 pair-hold3 稳定化，不再列入影子对照。
    # 七项独立 已于 2026-10-31 22:05 启用单尾 hold3 稳定化，不再列入影子对照。
    # 七项独立 v7（22:05）已转正，影子不再纳入；已退役框架不留对照项。
    # AI 三位置 已于 2026-10-31 21:10 转正 v8（hold3-until-miss），不再列入影子对照。
    return result


def _simulate(rows, policy=POLICY):
    held = None
    ages = None
    hits = []
    changes = 0
    previous = None
    last_target = None
    for target, nums, items in rows:
        if not items:
            continue
        if held is None:
            held = list(items)
            ages = [0] * len(held)
        period_hits = [_tail_value(nums, key) != kill for key, kill in held]
        hits.extend(period_hits)
        new_held, new_ages = [], []
        for index, (key, kill) in enumerate(held):
            used = ages[index] + 1
            if period_hits[index] and used < 3:
                new_held.append((key, kill))
                new_ages.append(used)
            else:
                replacement = items[index] if index < len(items) else items[-1]
                new_held.append(replacement)
                new_ages.append(0)
        if previous is not None and set(new_held) != set(previous):
            changes += 1
        previous = new_held
        held, ages = new_held, new_ages
        last_target = target
    count, total = len(hits), sum(1 for hit in hits if hit)
    return {'n': count, 'hits': total, 'rate': (total / count) if count else None,
            'changes': changes, 'last_target': last_target,
            'current': [{'key': key, 'kill': kill} for key, kill in (held or [])]}


def _actual(rows):
    hits = []
    changes = 0
    previous = None
    last_target = None
    for target, nums, items in rows:
        if not items:
            continue
        hits.extend([_tail_value(nums, key) != kill for key, kill in items])
        current = set(items)
        if previous is not None and current != previous:
            changes += 1
        previous = current
        last_target = target
    count, total = len(hits), sum(1 for hit in hits if hit)
    return {'n': count, 'hits': total, 'rate': (total / count) if count else None,
            'changes': changes, 'last_target': last_target}


def _entry(rows, forward_start):
    forward = [row for row in rows if row[0] >= forward_start]
    return {
        'backfill': {'actual': _actual(rows), 'policy': _simulate(rows)},
        'forward': {'periods': len(forward), 'actual': _actual(forward),
                    'policy': _simulate(forward)},
    }


def _period(value):
    text = str(value or '')
    if len(text) == 11 and text.isdigit():
        return text
    return None


def build(*, root=ROOT):
    collected = _collect()
    previous = _read(Path(root) / 'data' / 'tail_stability_shadow.json', {}) or {}
    start = _period(previous.get('start_period'))
    if not start:
        latest = []
        for rows in collected.values():
            if rows:
                latest.append(max(row[0] for row in rows))
        start = next_period(max(latest)) if latest else None
    frameworks = {}
    if start:
        for name, rows in collected.items():
            if rows:
                frameworks[name] = _entry(rows, start)
    now = datetime.now(TZ)
    return {'version': 1, 'generated_at': now.isoformat(timespec='seconds'),
            'policy': POLICY, 'policy_label': POLICY_LABEL,
            'start_period': start, 'biz_date': business_start(now).isoformat(),
            'note': '只读影子对照：命中沿用/最多连3期；不影响任何封存与正式统计。',
            'frameworks': frameworks}


def _pct(value):
    return ('%.2f%%' % (100.0 * value)) if isinstance(value, (int, float)) else '-'


def render_block(data):
    rows = []
    frameworks = data.get('frameworks') or {}
    order = [name for name in ('killer九项', '固定八码', '七项独立v7', 'AI三位置v7')
             if name in frameworks]
    order += [name for name in sorted(frameworks) if name not in order]
    for name in order:
        item = frameworks[name]
        back = item.get('backfill') or {}
        actual, policy = back.get('actual') or {}, back.get('policy') or {}
        fwd = item.get('forward') or {}
        fa, fp = fwd.get('actual') or {}, fwd.get('policy') or {}
        rows.append(
            '<tr><td><b>' + name + '</b><div class="ts-foot">回放 n=' + str(actual.get('n') or 0) + '</div></td>'
            '<td>' + _pct(actual.get('rate')) + '<div class="ts-foot">' + str(actual.get('hits') or 0) + '/' + str(actual.get('n') or 0) + '</div></td>'
            '<td>' + _pct(policy.get('rate')) + '<div class="ts-foot">' + str(policy.get('hits') or 0) + '/' + str(policy.get('n') or 0) + '</div></td>'
            '<td>' + str(actual.get('changes') or 0) + '</td>'
            '<td>' + str(policy.get('changes') or 0) + '</td>'
            '<td>' + str(fwd.get('periods') or 0) + '<div class="ts-foot">' + _pct(fa.get('rate')) + ' → ' + _pct(fp.get('rate')) + '</div></td>'
            '</tr>')
    forward_start = str(data.get('start_period') or '—')
    return (
        START + '\n<section id="tail-stability-shadow" aria-label="合尾稳定化候选影子">'
        '<style>#tail-stability-shadow{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;'
        'border:1px solid #c4b5fd;border-radius:12px;background:#faf5ff;color:#1e293b;font:14px/1.6 sans-serif;overflow-wrap:anywhere}'
        '#tail-stability-shadow h3{font-size:18px;color:#5b21b6;margin:0 0 4px}'
        '#tail-stability-shadow .ts-meta{font-size:13px;color:#64748b;margin-bottom:8px}'
        '#tail-stability-shadow table{width:100%;border-collapse:collapse;font-size:12.5px}'
        '#tail-stability-shadow th,#tail-stability-shadow td{padding:5px 6px;text-align:right;border-top:1px dashed #ddd6fe;vertical-align:top}'
        '#tail-stability-shadow th:first-child,#tail-stability-shadow td:first-child{text-align:left}'
        '#tail-stability-shadow .ts-foot{font-size:11px;color:#64748b}</style>'
        '<h3>🧪 合尾稳定化候选（影子·不改现网）</h3>'
        '<div class="ts-meta">政策：' + POLICY_LABEL + '。回放=同批已封存记录按真实开奖确定性重放；前瞻=从 ' + forward_start +
        ' 起逐期跟踪（按封存回放）。本区块只做对照，不改变任何封存、攻击语与正式统计。生成 ' +
        str(data.get('generated_at') or '-') + '</div>'
        '<table><thead><tr><th>框架</th><th>现行命中率(回放)</th><th>稳定化命中率(回放)</th>'
        '<th>现行换码</th><th>稳定化换码</th><th>上线后前瞻 n（现行→稳定化）</th></tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table>'
        '<div class="ts-foot">单尾杀1码随机基线90%。稳定化不会提高命中率（数学上限），只降低换码频率。'
        'AI 三位置（21:10，v8）、固定八码（22:00，pair-hold3）、七项独立 v7（22:05，hold3）、'
        'killer（22:15，triple-hold3）已全部转正；'
        '本影子停止对照，保留只读历史。旧封存一律保留。</div>'
        '</section>\n' + END)


def apply_block(html, fragment):
    if html.count(START) == 1 and html.count(END) == 1:
        head = html.index(START)
        tail = html.index(END) + len(END)
        return html[:head] + fragment + html[tail:]
    if html.count(START) or html.count(END):
        raise ValueError('tail-stability-shadow-marker-ambiguous')
    if html.count(ANCHOR) == 1:
        position = html.index(ANCHOR) + len(ANCHOR)
        return html[:position] + '\n' + fragment + html[position:]
    if html.count('</body>') == 1:
        return html.replace('</body>', fragment + '\n</body>', 1)
    raise ValueError('tail-stability-shadow-anchor-missing')


def refresh(*, root=ROOT):
    data = build(root=root)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    path = Path(root) / 'data' / 'tail_stability_shadow.json'
    _write_atomic(path, payload)
    fragment = render_block(data)

    def transform(html):
        return apply_block(html, fragment)

    update_panel(transform, path=Path(root) / 'panel.html')
    return data


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
