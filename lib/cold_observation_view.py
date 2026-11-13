"""Compact descriptive cold-code observation; never changes prediction evidence."""
from datetime import date, datetime, timedelta
from html import escape
from itertools import product
from pathlib import Path
import re

from stats_history import TZ, beijing_now, business_start, parse_period
from cold_observation import WINDOW_SIZE

ROOT = Path('/root/penta5-runner')
REVISION = 'cold-observation-v6-70-crossday'
MARKER = 'COLD_OBSERVATION'
START, END = '<!-- COLD_OBSERVATION_START -->', '<!-- COLD_OBSERVATION_END -->'
ANCHOR = 'PENTA5_STATS_PANEL'
POSITIONS = (('q', '千位'), ('b', '百位'), ('s', '十位'), ('g', '个位'))
TAILS = (('all', '千百十个合尾'), ('qbs', '千百十合尾'), ('qbg', '千百个合尾'),
         ('qsg', '千十个合尾'), ('bsg', '百十个合尾'))
SHAPES = (('parity', '单双形态', '单双'), ('size', '大小形态', '大小'))
_MARKERS = re.compile(r'<!--\s*([A-Z][A-Z0-9_]*)_(START|END)\s*-->')
_OWN_ID = re.compile(r'\bid\s*=\s*[\'\"]cold-observation[\'\"]', re.I)


def _integer(value, maximum=WINDOW_SIZE):
    return type(value) is int and 0 <= value <= maximum


def _period(value):
    parsed = parse_period(value) if isinstance(value, str) else None
    return value if parsed and parsed[0] == value else None


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(TZ) if stamp.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _group(group, values, count, first, last):
    """Reject malformed derived counts instead of displaying plausible numbers."""
    if not isinstance(group, dict) or not isinstance(group.get('items'), list):
        return False
    items = group['items']
    if len(items) != len(values) or any(not isinstance(item, dict) for item in items):
        return False
    if [item.get('value') for item in items] != values:
        return False
    for item in items:
        occurrences, omission = item.get('count'), item.get('current_omission')
        if not _integer(occurrences, count) or not _integer(omission, count):
            return False
        if occurrences == 0:
            if omission != count or item.get('omission_lower_bound') is not True or item.get('last_seen_period') is not None:
                return False
        elif (omission >= count or item.get('omission_lower_bound') is not False
              or not _period(item.get('last_seen_period'))
              or not first <= item['last_seen_period'] <= last):
            return False
    if sum(item['count'] for item in items) != count:
        return False
    cold = group.get('cold')
    latest = group.get('latest')
    if not count:
        return latest is None and cold == []
    if latest not in values or not isinstance(cold, list) or len(cold) != 1:
        return False
    newest = items[values.index(latest)]
    if newest['count'] < 1 or newest['current_omission'] != 0 or newest['last_seen_period'] != last:
        return False
    ranked = sorted(enumerate(items), key=lambda pair: (pair[1]['count'], -pair[1]['current_omission'], pair[0]))
    return cold == [item['value'] for _, item in ranked[:1]]


def _validated(summary):
    if not isinstance(summary, dict):
        return None
    beginning, updated = _stamp(summary.get('business_start')), _stamp(summary.get('updated_at'))
    day = summary.get('biz_date')
    try:
        day_valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except (ValueError, TypeError):
        day_valid = False
    if (type(summary.get('version')) is not int or summary['version'] != 1
            or summary.get('revision') != REVISION or summary.get('stats_error') is not False
            or not day_valid or beginning is None or updated is None
            or beginning.strftime('%Y-%m-%d') != day or beginning.time().isoformat() != '05:00:00'
            or not beginning <= updated < beginning + timedelta(days=1)
            or not _integer(summary.get('sample_count')) or summary.get('window_size') != WINDOW_SIZE
            or type(summary.get('window_size')) is not int):
        return None
    count = summary['sample_count']
    first, last, reference = (_period(summary.get(key)) for key in ('first_period', 'last_period', 'reference_period'))
    if ((count and (first is None or last is None or first > last or reference != last))
            or (not count and (summary.get('first_period') is not None or summary.get('last_period') is not None))
            or (summary.get('reference_period') is not None and reference is None)
            or not isinstance(summary.get('history_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', summary['history_sha256'])):
        return None
    for family, labels, alphabet in (('positions', POSITIONS, '0123456789'), ('tails', TAILS, '0123456789'),
                                     ('shapes', SHAPES, None)):
        groups = summary.get(family)
        if not isinstance(groups, dict) or set(groups) != {item[0] for item in labels}:
            return None
        for spec in labels:
            values = list(alphabet) if alphabet else [''.join(chars) for chars in product(spec[2], repeat=4)]
            if not _group(groups[spec[0]], values, count, first, last):
                return None
    return {'summary': summary, 'updated': updated.strftime('%Y-%m-%d %H:%M:%S')}


_STYLE = '''<style>
#cold-observation{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a5b4fc;border-radius:12px;background:#fafbff;color:#334155;font:14px/1.65 sans-serif;min-width:0;overflow-wrap:anywhere}
#cold-observation *{box-sizing:border-box}
#cold-observation h3{font-size:18px;color:#3730a3;margin:0 0 4px}
#cold-observation h4{font-size:15px;margin:12px 0 6px;color:#1e293b;scroll-margin-top:56px}
#cold-observation .cold-meta,#cold-observation .cold-note{font-size:13px;color:#64748b}
#cold-observation .cold-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,275px),1fr));gap:8px;min-width:0}
#cold-observation .cold-card{min-width:0;padding:9px 10px;border:1px solid #dbe4fb;border-radius:8px;background:#fff}
#cold-observation .cold-label{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:5px}
#cold-observation .cold-latest{color:#64748b;font-size:13px}
#cold-observation .cold-items{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px}
#cold-observation .cold-pick{grid-template-columns:minmax(0,1fr)}
#cold-observation .cold-pick .cold-item{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:7px 9px}
#cold-observation .cold-item{min-width:0;background:#f1f5fb;padding:4px 5px;border-radius:5px;font-size:13px;line-height:1.5}
#cold-observation .cold-item.is-min{background:#fff1f2;box-shadow:inset 0 0 0 1px #fecdd3}
#cold-observation .cold-item.is-min .cold-value{color:#be123c}
#cold-observation .cold-head{display:inline-flex;align-items:center;gap:5px}
#cold-observation .cold-tag{display:inline-block;padding:0 6px;border-radius:999px;background:#be123c;color:#fff;font-size:11px;font-style:normal;font-weight:700;line-height:1.6}
#cold-observation .cold-value{font-size:16px;color:#3730a3;letter-spacing:1px;white-space:nowrap}
#cold-observation .cold-item small{display:block;color:#475569;font-size:12px;white-space:nowrap}
#cold-observation details{margin-top:5px;font-size:13px}
#cold-observation summary{cursor:pointer;color:#475569;user-select:none}
#cold-observation details .cold-items{margin-top:5px}
#cold-observation .cold-note{margin-top:10px}
@media(max-width:430px){#cold-observation{padding:11px;width:calc(100% - 16px)}#cold-observation .cold-grid{grid-template-columns:minmax(0,1fr)}#cold-observation h3{font-size:17px}}
</style>'''


def _item(item, is_min=False):
    omission = ('≥' if item['omission_lower_bound'] else '') + str(item['current_omission'])
    return ('<div class="cold-item' + (' is-min' if is_min else '') + '"><span class="cold-head"><b class="cold-value">'
            + escape(item['value']) + '</b>' + ('<em class="cold-tag">最少</em>' if is_min else '')
            + '</span><small>出现 ' + str(item['count']) + ' 次</small><small>遗漏 '
            + omission + ' 期</small></div>')


def _card(group, label, key):
    items = {item['value']: item for item in group['items']}
    latest = escape(group['latest']) if group['latest'] is not None else '—'
    cold_values = set(group['cold'])
    cold = ''.join(_item(items[value], True) for value in group['cold'])
    expanded = ''.join(_item(item, item['value'] in cold_values) for item in group['items'])
    if not cold:
        cold = '<div class="cold-meta">暂无开奖样本，等待更新。</div>'
        expanded = '<div class="cold-meta">暂无样本，暂不显示出现次数与遗漏。</div>'
    return ('<article class="cold-card" data-cold-group="' + key + '"><div class="cold-label"><b>'
            + label + '</b><span class="cold-latest">最新：' + latest + '</span></div>'
            '<div class="cold-items cold-pick">' + cold + '</div><details id="cold-details-' + key
            + '"><summary>展开全部 ' + str(len(items)) + ' 种</summary><div class="cold-items">'
            + expanded + '</div></details></article>')


def render_observation(summary):
    """Render a data-only observation section with a fixed rolling window."""
    checked = _validated(summary)
    if checked is None:
        return (START + '\n<section id="cold-observation" role="status" style="margin:12px auto;'
                'padding:12px;max-width:1240px;width:calc(100% - 24px);box-sizing:border-box;border:1px solid #fcd34d;'
                'border-radius:10px;background:#fffbeb;color:#92400e;font:14px/1.7 sans-serif;">'
                '<b>冷码观察</b><br>观察数据核验失败，等待下次刷新；暂不显示次数和遗漏。</section>\n' + END)
    data = checked['summary']
    groups = []
    for family, labels, title in (('positions', POSITIONS, '四个位置'), ('tails', TAILS, '五种合尾'),
                                   ('shapes', SHAPES, '四位形态')):
        groups.append('<h4 id="cold-' + family + '">' + title + '</h4><div class="cold-grid">'
                      + ''.join(_card(data[family][spec[0]], spec[1], family + '-' + spec[0]) for spec in labels)
                      + '</div>')
    period_range = (data['first_period'] + ' ～ ' + data['last_period']) if data['sample_count'] else '暂无可用样本'
    return (START + '\n<section id="cold-observation" data-stats-business-date="' + escape(data['biz_date'], quote=True)
            + '">' + _STYLE + '<h3>冷码观察 · 近' + str(WINDOW_SIZE) + '期每项第1名</h3><div class="cold-meta">'
            + '跨日滚动窗口（每日 05:00 不清零） · 实际 ' + str(data['sample_count'])
            + ' 期，最多' + str(WINDOW_SIZE) + '期<br>样本范围：' + escape(period_range) + '<br>最新开奖参考期：'
            + escape(data['reference_period'] or '—') + ' · 更新：' + checked['updated'] + '（北京时间）</div>'
            + ''.join(groups) + '<div class="cold-note">每个项目每期只显示近' + str(WINDOW_SIZE) + '期冷码总排序第1名：先按出现次数从少到多，再按当前遗漏由长到短，仍并列时按固定顺序排序，最后取整个列表第1个。'
            '“遗漏 ≥N”表示这N期样本中未出现，窗口之前不作推断。只使用前4球；大小以0—4为小、5—9为大。'
            '<br>取最近' + str(WINDOW_SIZE) + '期（跨日滚动，每日 05:00 不清零），未满' + str(WINDOW_SIZE) + '期按实际样本统计。冷码只描述历史样本，不代表下期更容易或更难出现。</div>'
            '</section>\n' + END)


def _inspect(content):
    if not isinstance(content, str):
        raise TypeError('Panel HTML must be text')
    found, stack = {}, []
    for match in _MARKERS.finditer(content):
        name, side = match.groups()
        if side == 'START':
            if name in found:
                raise ValueError('Duplicate panel marker')
            found[name] = [match.start(), None, None, len(stack)]
            stack.append(name)
        else:
            if not stack or stack[-1] != name:
                raise ValueError('Unbalanced panel marker')
            stack.pop()
            found[name][1:3] = [match.end(), content[found[name][0]:match.end()]]
    if stack:
        raise ValueError('Unbalanced panel marker')
    return found


def apply_observation(content, block):
    """Publish outside every sealed range; protect all other named fragments."""
    before, incoming = _inspect(content), _inspect(block)
    if (set(incoming) != {MARKER} or incoming[MARKER][2] != block.strip()
            or len(_OWN_ID.findall(block)) != 1):
        raise ValueError('Invalid cold observation block')
    if ANCHOR not in before or before[ANCHOR][3] != 0:
        raise ValueError('Top-level base statistics anchor required')
    own = before.get(MARKER)
    if own is not None:
        if own[3] != 0 or len(_OWN_ID.findall(content)) != 1:
            raise ValueError('Invalid existing cold observation placement')
        updated = content[:own[0]] + block + content[own[1]:]
    else:
        if _OWN_ID.search(content):
            raise ValueError('Cold observation id exists without owned markers')
        position = before[ANCHOR][1]
        updated = content[:position] + '\n' + block + '\n' + content[position:]
    after = _inspect(updated)
    protected = {name: row[2] for name, row in before.items() if name != MARKER}
    if protected != {name: row[2] for name, row in after.items() if name != MARKER}:
        raise ValueError('Cold observation changed another panel block')
    return updated


def _failure_summary(snapshot, code='cold-observation-view-invalid-data'):
    now = snapshot.get('now') if isinstance(snapshot, dict) else None
    now = now.astimezone(TZ) if isinstance(now, datetime) and now.tzinfo is not None else beijing_now()
    start = business_start(now)
    return {'version': 1, 'revision': REVISION, 'stats_error': True, 'error_code': code,
            'biz_date': start.strftime('%Y-%m-%d'), 'business_start': start.isoformat(),
            'updated_at': now.isoformat(), 'reference_period': None, 'first_period': None,
            'last_period': None, 'sample_count': 0, 'window_size': WINDOW_SIZE, 'history_sha256': None,
            'positions': {}, 'tails': {}, 'shapes': {}}


def refresh_cold_observation(snapshot, *, root=ROOT, writer=None):
    from ai_verified_stats import atomic_json
    from cold_observation import build_observation
    if writer is None:
        from panel_io import update_panel
        writer = update_panel
    root = Path(root)
    try:
        summary = build_observation(snapshot)
        if _validated(summary) is None and not (isinstance(summary, dict) and summary.get('stats_error') is True):
            summary = _failure_summary(snapshot)
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        summary = _failure_summary(snapshot)
    block = render_observation(summary)
    atomic_json(root / 'data' / 'cold_observation.json', summary)
    writer(lambda content: apply_observation(content, block), path=root / 'panel.html')
    return summary
