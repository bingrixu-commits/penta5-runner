"""Data-only rendering of the active book while retaining dual-book validation."""
from datetime import date, datetime, timedelta
from hashlib import sha256
from html import escape
import json
import re

from cold_observation_view import _inspect
from stats_history import TZ, parse_period
from framework_fixed_pnl import simulate_day

REVISION = 'framework-pnl-v2-fixed5000'
MARKER = 'SIMULATED_PNL'
START, END = '<!-- SIMULATED_PNL_START -->', '<!-- SIMULATED_PNL_END -->'
ANCHOR = 'COLD_OBSERVATION'
MODELS = (('selected', '三定位七项框架'), ('six', '六项独立框架'))
# Retired books stay in the immutable ledger and its integrity checks. They
# are no longer active products, so neither current nor historic cards expose
# them. The refresh layer may still settle an already assigned pending stake.
DISPLAY_MODELS = (('six', '六项独立框架'),)
STEPS = (150, 300, 600, 1200)
CONFIG = {'revision': REVISION, 'stake_unit': 'per-group-fixed-5000', 'group_count': 5000,
          'steps_cents': list(STEPS), 'gross_multiplier': 9900, 'currency': 'CNY'}
_OWN_ID = re.compile(r'\bid\s*=\s*[\'\"]simulated-pnl[\'\"]', re.I)
_LIMIT = 10**15
_STATUSES = frozenset(('settled', 'pending', 'skip', 'conflict', 'blocked'))


def _int(value, low=0, high=_LIMIT):
    return type(value) is int and low <= value <= high


def _period(value):
    parsed = parse_period(value) if isinstance(value, str) else None
    return value if parsed and parsed[0] == value else None


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(TZ) if result.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _money(cents, signed=False):
    sign = '+' if signed and cents > 0 else '-' if cents < 0 else ''
    whole, fraction = divmod(abs(cents), 100)
    return sign + format(whole, ',') + '.' + str(fraction).zfill(2)


def _totals(data):
    if not isinstance(data, dict):
        return False
    keys = ('settled_count', 'win_count', 'loss_count', 'cost_cents', 'return_cents',
            'max_drawdown_cents', 'four_loss_cycles')
    if any(not _int(data.get(key)) for key in keys) or not _int(data.get('net_cents'), -_LIMIT):
        return False
    return (data['settled_count'] == data['win_count'] + data['loss_count']
            and data['net_cents'] == data['return_cents'] - data['cost_cents']
            and data['four_loss_cycles'] * 4 <= data['loss_count'])


def _target(event):
    value = event.get('target_period', event.get('target'))
    if 'target_period' in event and 'target' in event and event['target_period'] != event['target']:
        return None
    return _period(value)


def _model(data, model, day, now):
    """Cross-check money against 5,000 fixed groups at the per-group stake."""
    if (not isinstance(data, dict) or data.get('model') != model or data.get('biz_date') != day
            or data.get('stats_error') is not False or not isinstance(data.get('events'), list)
            or len(data['events']) > 1000 or not _totals(data.get('totals'))
            or any(not _int(data.get(key), 0, 1000) for key in ('pending_count', 'skipped_count', 'blocked_count'))
            or not _int(data.get('current_step'), 0, 3)
            or data.get('current_stake_cents') != STEPS[data['current_step']]
            or type(data.get('current_stake_cents')) is not int):
        return None
    # Reconstruct the original statuses before rerunning the money engine. A
    # pending event can have later eligible rows marked blocked; those original
    # statuses are retained as source_status by the engine, never inferred here.
    original = []
    for row in data['events']:
        if not isinstance(row, dict) or not isinstance(row.get('status'), str) or row['status'] not in _STATUSES:
            return None
        item = dict(row)
        if 'source_status' in item:
            if not isinstance(item['source_status'], str) or item['source_status'] not in _STATUSES:
                return None
            item['status'] = item.pop('source_status')
        original.append(item)
    try:
        replayed = simulate_day(model, original, now)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    if any(data.get(key) != replayed[key] for key in
           ('totals', 'pending_count', 'skipped_count', 'blocked_count', 'current_step',
            'current_stake_cents', 'latest_settled_period')):
        return None
    money_fields = ('status', 'step', 'stake_cents', 'cost_cents', 'return_cents', 'net_cents',
                    'committed_cost_cents', 'cumulative_net_cents', 'reset_reason')
    for existing, rebuilt in zip(data['events'], replayed['events']):
        if any(existing.get(key) != rebuilt.get(key) for key in money_fields):
            return None
        if (any(not _int(existing.get(key), -_LIMIT) for key in
                ('cost_cents', 'return_cents', 'net_cents', 'committed_cost_cents', 'cumulative_net_cents'))
                or existing.get('reset_reason') not in (None, 'hit', 'four-losses')):
            return None
    running = peak = max_drawdown = wins = losses = cost = returned = cycles = 0
    previous = None
    latest = None
    settled = []
    for event in data['events']:
        if not isinstance(event, dict) or event.get('status') not in _STATUSES:
            return None
        target = _target(event)
        if target is None or (previous is not None and target <= previous):
            return None
        previous = target
        status = event['status']
        if status in ('settled', 'pending'):
            step, stake, count = event.get('step'), event.get('stake_cents'), event.get('number_count')
            if (not _int(step, 0, 3) or type(stake) is not int or stake != STEPS[step]
                    or not _int(count, 1, 10000)):
                return None
        if status == 'settled':
            if (type(event.get('hit')) is not bool
                    or not all(_int(event.get(key), -_LIMIT) for key in
                               ('cost_cents', 'return_cents', 'net_cents', 'cumulative_net_cents'))):
                return None
            actual_cost = 5000 * stake
            actual_return = 9900 * stake if event['hit'] else 0
            if (event['cost_cents'] != actual_cost or event['return_cents'] != actual_return
                    or event['net_cents'] != actual_return - actual_cost):
                return None
            running += event['net_cents']
            peak = max(peak, running)
            max_drawdown = max(max_drawdown, peak - running)
            if event['cumulative_net_cents'] != running:
                return None
            wins += int(event['hit'])
            losses += int(not event['hit'])
            cycles += int(not event['hit'] and step == 3)
            cost += actual_cost
            returned += actual_return
            latest = target
            settled.append(event)
        else:
            if any(event.get(key, 0) != 0 or type(event.get(key, 0)) is not int
                   for key in ('cost_cents', 'return_cents', 'net_cents')):
                return None
            if status == 'pending' and event.get('committed_cost_cents') != 5000 * stake:
                return None
    totals = data['totals']
    expected = {'settled_count': wins + losses, 'win_count': wins, 'loss_count': losses,
                'cost_cents': cost, 'return_cents': returned, 'net_cents': running,
                'max_drawdown_cents': max_drawdown, 'four_loss_cycles': cycles}
    if any(totals[key] != value for key, value in expected.items()) or data.get('latest_settled_period') != latest:
        return None
    return data


def _validated(summary):
    if not isinstance(summary, dict):
        return None
    day, beginning, updated = (summary.get('biz_date'), _stamp(summary.get('business_start')),
                               _stamp(summary.get('updated_at')))
    try:
        valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except (ValueError, TypeError):
        valid_day = False
    config = summary.get('config')
    if (type(summary.get('version')) is not int or summary['version'] != 1
            or summary.get('revision') != REVISION or summary.get('stats_error') is not False
            or not valid_day or beginning is None or updated is None
            or beginning.strftime('%Y-%m-%d') != day or beginning.time().isoformat() != '05:00:00'
            or not beginning <= updated < beginning + timedelta(days=1)
            or config != CONFIG or not isinstance(config, dict)
            or any(type(value) is not int for value in config.get('steps_cents', []))
            or type(config.get('group_count')) is not int or type(config.get('gross_multiplier')) is not int):
        return None
    digest = sha256(json.dumps(config, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':')).encode('utf-8')).hexdigest()
    if summary.get('config_sha256') != digest or not isinstance(summary.get('models'), dict):
        return None
    if set(summary['models']) != {model for model, _ in MODELS}:
        return None
    return {'summary': summary, 'updated': updated.strftime('%Y-%m-%d %H:%M:%S'),
            'models': {key: _model(summary['models'][key], key, day, updated) for key, _ in MODELS}}


STYLE = '''<style>
#simulated-pnl{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a5b4fc;border-radius:12px;background:#fafbff;color:#334155;font:14px/1.65 sans-serif;min-width:0;overflow-wrap:anywhere}
#simulated-pnl *{box-sizing:border-box}#simulated-pnl h3{font-size:18px;color:#3730a3;margin:0 0 4px}#simulated-pnl h4{font-size:16px;color:#0f172a;margin:0 0 6px}
#simulated-pnl .pnl-meta,#simulated-pnl .pnl-note{font-size:13px;color:#64748b}#simulated-pnl .pnl-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:10px;margin-top:10px}
#simulated-pnl .pnl-card{min-width:0;padding:12px;border:1px solid #dbe4fb;border-radius:9px;background:#fff}#simulated-pnl .pnl-net{font-size:23px;font-weight:700;line-height:1.5;margin:4px 0 8px;overflow-wrap:anywhere}
#simulated-pnl .pnl-profit{color:#b91c1c}#simulated-pnl .pnl-loss{color:#047857}#simulated-pnl .pnl-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 10px;margin:0}
#simulated-pnl .pnl-metrics div{min-width:0}#simulated-pnl dt{font-size:12px;color:#64748b}#simulated-pnl dd{font-size:14px;margin:0;font-variant-numeric:tabular-nums}#simulated-pnl .pnl-exclusions{font-size:13px;margin-top:8px;color:#475569}
#simulated-pnl details{margin-top:10px;min-width:0}#simulated-pnl summary{cursor:pointer;color:#475569}#simulated-pnl .pnl-scroll{overflow-x:auto;max-width:100%;margin-top:7px;overscroll-behavior-x:contain}
#simulated-pnl table{border-collapse:collapse;font-size:12px;width:100%;min-width:1000px;white-space:nowrap;font-variant-numeric:tabular-nums}#simulated-pnl th,#simulated-pnl td{text-align:right;padding:6px 9px;border-bottom:1px solid #e2e8f0}#simulated-pnl th:first-child,#simulated-pnl td:first-child{text-align:left}#simulated-pnl th{color:#475569;background:#f1f5fb}
#simulated-pnl .pnl-note{margin-top:10px}#simulated-pnl .pnl-empty,#simulated-pnl .pnl-unavailable{background:#fffbeb;color:#92400e;padding:8px;border-radius:6px}
@media(max-width:760px){#simulated-pnl .pnl-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:430px){#simulated-pnl{padding:11px;width:calc(100% - 16px)}#simulated-pnl .pnl-card{padding:10px}#simulated-pnl h3{font-size:17px}#simulated-pnl .pnl-net{font-size:21px}}
</style>'''


def _row(event):
    status, target = event['status'], _target(event)
    if status == 'settled':
        result = '中奖' if event['hit'] else '未中'
        values = [target, str(event['step'] + 1), '5000',
                  _money(event['stake_cents']), _money(event['cost_cents']), result,
                  _money(event['return_cents']), _money(event['net_cents'], True),
                  _money(event['cumulative_net_cents'], True)]
    elif status == 'pending':
        values = [target, str(event['step'] + 1), '5000',
                  _money(event['stake_cents']), '待核验：' + _money(event['committed_cost_cents']),
                  '待核验，未入账', '—', '—', '—']
    else:
        label = {'skip': '跳过', 'conflict': '证据冲突', 'blocked': '顺序待确认'}[status]
        values = [target, '—', '—', '—', '—', label + '，未入账', '—', '—', '—']
    return '<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in values) + '</tr>'


def _card(data, key, title):
    if data is None:
        return '<article class="pnl-card"><h4>' + title + '</h4><div class="pnl-unavailable">模拟账本暂不可用，等待核验；暂不显示投入、返奖和盈亏。</div></article>'
    totals = data['totals']
    net = totals['net_cents']
    net_label = '模拟盈利' if net > 0 else '模拟亏损' if net < 0 else '模拟净盈亏'
    net_class = ' pnl-profit' if net > 0 else ' pnl-loss' if net < 0 else ''
    entries = [('已结算模拟投入', '¥' + _money(totals['cost_cents'])),
               ('已结算模拟返奖', '¥' + _money(totals['return_cents'])),
               ('已结算期数', str(totals['settled_count']) + ' 期'),
               ('中奖 / 未中', str(totals['win_count']) + ' / ' + str(totals['loss_count'])),
               ('四手全败轮数', str(totals['four_loss_cycles']) + ' 轮'),
               ('模拟最大回撤', '¥' + _money(totals['max_drawdown_cents']))]
    pending = sum(event.get('committed_cost_cents', 0) for event in data['events'] if event['status'] == 'pending')
    metrics = '<dl class="pnl-metrics">' + ''.join('<div><dt>' + label + '</dt><dd>' + value + '</dd></div>' for label, value in entries) + '</dl>'
    details = ('<details id="pnl-details-' + key + '"><summary>展开逐期模拟明细（' + str(len(data['events']))
               + ' 期）</summary><div class="pnl-scroll" style="overflow-x:auto" tabindex="0" aria-label="逐期模拟盈亏，可横向滚动">'
               '<table><thead><tr>' + ''.join('<th>' + label + '</th>' for label in
                   ('目标期', '第几手', '固定模拟组数', '每组金额 / 元', '本期总投入 / 元', '结果', '返奖 / 元', '净盈亏 / 元', '累计 / 元'))
               + '</tr></thead><tbody>' + ''.join(_row(event) for event in data['events']) + '</tbody></table></div></details>')
    empty = '<div class="pnl-empty">本统计日暂无已结算模拟样本。</div>' if not totals['settled_count'] else ''
    blocked = ('<div class="pnl-unavailable">存在待核对期次，盈亏仅为已核验部分，后续暂停计算。</div>'
               if data['blocked_count'] else '')
    return ('<article class="pnl-card" data-pnl-model="' + key + '"><h4>' + title + '</h4><div class="pnl-net'
            + net_class + '">' + net_label + ' ¥' + _money(net, True) + '</div>' + blocked + empty + metrics
            + '<div class="pnl-exclusions">待核验 ' + str(data['pending_count']) + ' 期（待入账投入 ¥' + _money(pending)
            + '）；已跳过 ' + str(data['skipped_count']) + ' 期；阻塞或冲突 ' + str(data['blocked_count'])
            + ' 期。<br>当前模拟第 ' + str(data['current_step'] + 1) + ' 手，每组金额 ¥' + _money(data['current_stake_cents'])
            + '；5000组总投入 ¥' + _money(5000 * data['current_stake_cents'])
            + '；最近结算期：' + escape(data['latest_settled_period'] or '—') + '。</div>' + details + '</article>')


def _history(summary):
    rows = summary.get('history', [])
    if not isinstance(rows, list) or not rows:
        return ''
    seen, displayed = set(), []
    for row in rows[-366:]:
        if not isinstance(row, dict):
            return ''
        day = row.get('biz_date')
        try:
            valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except (ValueError, TypeError):
            valid = False
        models = row.get('models')
        if (not valid or day >= summary['biz_date'] or day in seen or not isinstance(models, dict)
                or set(models) != {key for key, _ in MODELS} or any(not _totals(models[key]) for key, _ in MODELS)):
            return ''
        seen.add(day)
        for key, title in DISPLAY_MODELS:
            total = models[key]
            displayed.append('<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in
                (day, title, '日终已核验' if row.get('complete') is True else '仅已核验部分', str(total['settled_count']), _money(total['cost_cents']),
                 _money(total['return_cents']), _money(total['net_cents'], True), _money(total['max_drawdown_cents']))) + '</tr>')
    return ('<details id="pnl-history"><summary>展开以往统计日模拟汇总</summary><div class="pnl-scroll" style="overflow-x:auto" tabindex="0">'
            '<table><thead><tr>' + ''.join('<th>' + word + '</th>' for word in
               ('统计日', '框架', '状态', '结算期数', '投入 / 元', '返奖 / 元', '净盈亏 / 元', '最大回撤 / 元'))
            + '</tr></thead><tbody>' + ''.join(displayed) + '</tbody></table></div></details>')


def render_pnl(summary):
    """Never render raw provider content, credentials, proof bodies or explanations."""
    checked = _validated(summary)
    if checked is None:
        return (START + '\n<section id="simulated-pnl">' + STYLE + '<h3>今日模拟盈亏 · 固定5000组</h3>'
                '<div class="pnl-unavailable" role="status">固定5000组模拟账本暂不可用，等待数据核验；暂不显示投入、返奖和盈亏。</div></section>\n' + END)
    data = checked['summary']
    return (START + '\n<section id="simulated-pnl" data-stats-business-date="' + escape(data['biz_date'], quote=True)
            + '">' + STYLE + '<h3>今日模拟盈亏 · 固定5000组</h3><div class="pnl-meta">'
            + escape(data['biz_date']) + ' 05:00起（北京时间）；更新：' + checked['updated']
            + '。<br>每组金额：¥1.50 → ¥3.00 → ¥6.00 → ¥12.00；第一手5000组总投入¥7,500.00，命中含本金总返¥14,850.00，净赢¥7,350.00。'
            + '<br>按真实事前封存与开奖回算；模拟账本，非实际下注记录。</div><div class="pnl-grid">'
            + ''.join(_card(checked['models'][key], key, title) for key, title in DISPLAY_MODELS) + '</div>'
            + '<div class="pnl-note">每组金额按1.5、3、6、12元依次计算。未中进入下一手，中奖回到每组1.5元；连续四手未中也回到每组1.5元。'
            '本期模拟总投入＝5000组×当手每组金额；命中总返奖＝9900×当手每组金额（含本金），未中返奖为0；净盈亏＝返奖−本期总投入。'
            '<br>固定5000组模拟：5000是指定的成本基数，不代表模型实际筛出5000个号码；实际筛选组数不参与成本。'
            '是否命中仍按真实事前封存框架和开奖核对，空号码集跳过。'
            '<br>本框架每日北京时间05:00从第一手重新计算。待核验、缺失或冲突数据不补胜负，未结算成本单列；'
            '已保留开关上线前的记录；之后只计入AI开启期间封存的新期次，暂停前已开始的期次仍正常结算。金额仅为按规则推算，不代表真实资金进出。</div>' + _history(data) + '</section>\n' + END)


def apply_pnl(content, fragment):
    """Publish a separate top-level range while preserving every other named block."""
    before, incoming = _inspect(content), _inspect(fragment)
    if (set(incoming) != {MARKER} or incoming[MARKER][2] != fragment.strip()
            or len(_OWN_ID.findall(fragment)) != 1):
        raise ValueError('Invalid simulated P&L fragment')
    if ANCHOR not in before or before[ANCHOR][3] != 0:
        raise ValueError('Top-level cold observation anchor required')
    own = before.get(MARKER)
    if own is not None:
        if own[3] != 0 or own[0] < before[ANCHOR][1] or len(_OWN_ID.findall(content)) != 1:
            raise ValueError('Invalid simulated P&L placement')
        updated = content[:own[0]] + fragment + content[own[1]:]
    else:
        if _OWN_ID.search(content):
            raise ValueError('Simulated P&L id exists without owned markers')
        position = before[ANCHOR][1]
        updated = content[:position] + '\n' + fragment + '\n' + content[position:]
    after = _inspect(updated)
    if ({name: row[2] for name, row in before.items() if name != MARKER}
            != {name: row[2] for name, row in after.items() if name != MARKER}):
        raise ValueError('Simulated P&L changed another named panel block')
    return updated
