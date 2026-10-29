"""Derived per-position rates for genuine stat50-v1 candidate publications."""
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import json
import re

from stats_history import TZ, beijing_now, business_start, parse_period

ROOT = Path('/root/penta5-runner')
REVISION = 'stat50-position-stats-v1'
MARKER = 'STAT50_POSITION_STATS'
START, END = '<!-- STAT50_POSITION_STATS_START -->', '<!-- STAT50_POSITION_STATS_END -->'
ANCHOR = 'STAT50_PREDICTION'
PROTECTED = ('R1_FRAMEWORK', 'R1_KILLER', 'R1_PRO_COMPARE', 'STAT50_PREDICTION',
             'R1_FRAMEWORK_STATS', 'R1_KILLER_STATS', 'MODEL_COMPARE_STATS',
             'STAT50_COMPARE_STATS', 'HE3_REFINE', 'HE3_REFINE_STATS', 'PRO_POSITION_STATS',
             'PENTA5_STATS_PANEL')
MODELS = (('frequency', '频率候选'), ('markov', '转移候选'))
POSITIONS = (('k1', '千位'), ('k2', '百位'), ('k3', '十位'), ('k4', '个位'))


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(TZ) if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _period(value):
    parsed = parse_period(value) if isinstance(value, str) else None
    return value if parsed and value == parsed[0] else None


def _summary(summary):
    """Normalize counts; supplied percentages never provide evidence."""
    raw = summary if isinstance(summary, dict) else {}
    day = _day(raw.get('biz_date'))
    beginning, updated = _stamp(raw.get('business_start')), _stamp(raw.get('updated_at'))
    valid = (type(raw.get('version')) is int and raw.get('version') == 1
             and raw.get('experiment_id') == 'stat50-v1' and raw.get('revision') == REVISION
             and raw.get('stats_error') is False and day is not None
             and beginning is not None and beginning.strftime('%Y-%m-%d') == day
             and beginning.time().isoformat() == '05:00:00' and updated is not None
             and beginning <= updated < beginning + timedelta(days=1))
    source = raw.get('models') if isinstance(raw.get('models'), dict) else {}
    valid = valid and set(source) == {name for name, _ in MODELS}
    models, totals = {}, []
    for model, _ in MODELS:
        group = source.get(model) if isinstance(source.get(model), dict) else {}
        metrics = group.get('metrics') if isinstance(group.get('metrics'), dict) else {}
        valid = valid and set(metrics) == {key for key, _ in POSITIONS}
        models[model] = {'metrics': {}}
        for key, _ in POSITIONS:
            item = metrics.get(key) if isinstance(metrics.get(key), dict) else {}
            hits, total = _count(item.get('hits')), _count(item.get('total'))
            correct = hits is not None and total is not None and hits <= total
            valid = valid and correct
            totals.append(total)
            models[model]['metrics'][key] = {'hits': hits, 'total': total}
    valid = valid and all(total is not None and total == totals[0] for total in totals)
    total = totals[0] if totals and totals[0] is not None else None
    counters = {key: _count(raw.get(key)) for key in ('pending_count', 'conflict_count', 'rejected_count')}
    valid = valid and all(value is not None for value in counters.values())
    latest = _period(raw.get('latest_settled_period'))
    valid = valid and ((total == 0 and raw.get('latest_settled_period') is None)
                       or (total is not None and total > 0 and latest is not None))
    if not valid:
        models = {model: {'metrics': {key: {'hits': None, 'total': None} for key, _ in POSITIONS}}
                  for model, _ in MODELS}
    return {'version': 1, 'experiment_id': 'stat50-v1', 'revision': REVISION,
            'biz_date': day or '--', 'business_start': beginning.isoformat() if beginning else None,
            'updated_at': updated.strftime('%Y-%m-%d %H:%M:%S') if updated else '--',
            'models': models, 'latest_settled_period': latest or '--',
            'stats_error': not bool(valid), **counters}


def _rate(metric, error):
    if error:
        return '统计核验失败，暂停显示'
    return ('排除成功率 %.2f%%（%d/%d期）' %
            (100 * metric['hits'] / metric['total'], metric['hits'], metric['total'])
            if metric['total'] else '暂无已核验数据')


DAY_SCRIPT = """<script id="stat50-position-day-control">
(function(){
  var version='stat50-position-day-v1';
  function businessDate(){var now=Date.now();return Number.isFinite(now)?new Date(now+3*3600000).toISOString().slice(0,10):'';}
  function count(value){return Number.isSafeInteger(value)&&value>=0;}
  function check(){
    var roots=document.querySelectorAll('[id="stat50-position-stats"]');
    if(roots.length!==1)return;
    var root=roots[0],source=document.getElementById('stat50-position-data'),data;
    try{data=source?JSON.parse(source.textContent):null;}catch(error){data=null;}
    var valid=!!data&&typeof data==='object'&&!Array.isArray(data)&&data.stats_error===false
      &&data.version===1&&data.experiment_id==='stat50-v1'&&data.revision==='stat50-position-stats-v1';
    var today=valid&&data.biz_date===businessDate(),total=null;
    var models=['frequency','markov'],keys=['k1','k2','k3','k4'];
    models.forEach(function(model){keys.forEach(function(key){
      var metric=data&&data.models&&data.models[model]&&data.models[model].metrics&&data.models[model].metrics[key];
      if(!metric||!count(metric.hits)||!count(metric.total)||metric.hits>metric.total){valid=false;return;}
      if(total===null)total=metric.total;else if(total!==metric.total)valid=false;
    });});
    ['pending_count','conflict_count','rejected_count'].forEach(function(key){if(!data||!count(data[key]))valid=false;});
    models.forEach(function(model){keys.forEach(function(key){
      var nodes=root.querySelectorAll('[data-stat50-position-rate="'+model+':'+key+'"]');
      if(nodes.length!==1)return;
      var metric=data&&data.models&&data.models[model]&&data.models[model].metrics&&data.models[model].metrics[key];
      nodes[0].textContent=!valid?'统计核验失败，暂停显示':!today?'北京时间05:00已清零，等待本周期数据':metric.total?'排除成功率 '+(100*metric.hits/metric.total).toFixed(2)+'%（'+metric.hits+'/'+metric.total+'期）':'暂无已核验数据';
    });});
    var expiry=root.querySelector('[data-stat50-position-expired]');
    if(expiry){expiry.hidden=!valid||today;expiry.textContent='统计周期已切换，等待本日核验；上周期百分比已停止显示。';}
    var counters=root.querySelector('[data-stat50-position-counters]');
    if(counters)counters.textContent=!valid?'样本计数暂不可用':!today?'待核验、最近核验期及异常计数等待本周期更新':'最近核验期：'+data.latest_settled_period+' ｜ 待核验：'+data.pending_count+'期 ｜ 冲突：'+data.conflict_count+'期 ｜ 拒绝计入：'+data.rejected_count+'期';
    var warning=root.querySelector('[data-stat50-position-warning]');
    if(warning)warning.hidden=!valid||!today||!(data.conflict_count||data.rejected_count);
  }
  function attach(state){window.addEventListener('load',state.check);window.addEventListener('pageshow',state.check);document.addEventListener('visibilitychange',state.check);if(state.timer!=null)clearInterval(state.timer);state.timer=setInterval(state.check,1000);state.check();}
  var prior=window.__PENTA5_STAT50_POSITION_STATS__;
  if(prior&&prior.version===version&&typeof prior.check==='function'){attach(prior);return;}
  if(prior){if(prior.timer!=null)clearInterval(prior.timer);if(typeof prior.check==='function'){window.removeEventListener('load',prior.check);window.removeEventListener('pageshow',prior.check);document.removeEventListener('visibilitychange',prior.check);}}
  var state={version:version,check:check,timer:null};window.__PENTA5_STAT50_POSITION_STATS__=state;attach(state);
})();
</script>"""


def render_stat50_position_block(summary):
    data = _summary(summary)
    groups = []
    for model, label in MODELS:
        cells = []
        for key, position in POSITIONS:
            metric = data['models'][model]['metrics'][key]
            cells.append('<div data-stat50-position-cell="' + model + ':' + key + '" '
                         'style="min-width:0;overflow-wrap:anywhere;padding:7px 9px;border:1px solid #dbeafe;'
                         'border-radius:5px;background:#fff;"><b>' + position + '：</b><br>'
                         '<span data-stat50-position-rate="' + model + ':' + key + '" style="color:#1d4ed8;">'
                         + escape(_rate(metric, data['stats_error'])) + '</span></div>')
        groups.append('<section data-stat50-position-model="' + model + '" style="min-width:0;margin-top:10px;">'
                      '<h4 style="margin:0 0 5px;font-size:14px;color:#1e3a8a;">' + label + '</h4>'
                      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,205px),1fr));'
                      'gap:7px;min-width:0;">' + ''.join(cells) + '</div></section>')
    count = lambda key: str(data[key]) if data[key] is not None else '—'
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, replacement in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                             ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        encoded = encoded.replace(old, replacement)
    return (START + '\n<section id="stat50-position-stats" data-stat50-position-day="'
            + escape(data['biz_date'], quote=True) + '" style="width:calc(100% - 24px);max-width:1000px;'
            'box-sizing:border-box;margin:8px auto 16px;padding:12px 14px;border:1px solid #93c5fd;'
            'border-radius:8px;background:#f5f9ff;color:#334155;font:13px/1.8 sans-serif;'
            'overflow-wrap:anywhere;min-width:0;">'
            '<h3 style="margin:0 0 4px;font-size:16px;color:#1e40af;">近50期统计候选 · 今日已核验排除成功率</h3>'
            '<div>' + escape(data['biz_date']) + ' 05:00起（北京时间） · 两组各四位独立核验</div>'
            '<div data-stat50-position-expired role="status" hidden style="color:#b45309;"></div>'
            + ''.join(groups) + '<div data-stat50-position-counters style="margin-top:10px;color:#475569;">'
            '最近核验期：' + escape(data['latest_settled_period']) + ' ｜ 待核验：' + count('pending_count')
            + '期 ｜ 冲突：' + count('conflict_count') + '期 ｜ 拒绝计入：' + count('rejected_count') + '期</div>'
            '<div data-stat50-position-warning role="status" style="color:#b45309;"'
            + ('' if not data['stats_error'] and (data['conflict_count'] or data['rejected_count']) else ' hidden')
            + '>存在冲突或被拒绝的证据，已排除，不计入成功或失败；等待核对。</div>'
            '<div style="margin-top:5px;color:#64748b;">只使用前4球；对应实际数字不等于事前排除码才算成功。'
            '按实际开奖时间归日；北京时间每日05:00重新统计，历史证据保留。'
            '<br>本栏使用统计候选自身的已核验样本；与原三方共同样本对照的分母不同，不混用成绩。'
            '<br>统计更新时间：' + escape(data['updated_at']) + '（北京时间）</div></section>\n'
            '<script type="application/json" id="stat50-position-data">' + encoded + '</script>\n'
            + DAY_SCRIPT + '\n' + END)


def _inspect(html):
    if not isinstance(html, str):
        raise TypeError('Panel HTML must be text')
    found = {}
    for marker in (*PROTECTED, MARKER):
        start, end = '<!-- ' + marker + '_START -->', '<!-- ' + marker + '_END -->'
        counts = html.count(start), html.count(end)
        if counts == (0, 0):
            found[marker] = None
            continue
        if counts != (1, 1):
            raise ValueError('Missing or duplicate stat50 position panel markers')
        left, right = html.index(start), html.index(end) + len(end)
        if left >= right - len(end):
            raise ValueError('Reversed stat50 position panel markers')
        found[marker] = (left, right, html[left:right])
    intervals = sorted((value[0], value[1]) for value in found.values() if value is not None)
    if any(left < previous_end for (_, previous_end), (left, _) in zip(intervals, intervals[1:])):
        raise ValueError('Overlapping sealed, derived or stat50 position panel blocks')
    return found


def _protected(found):
    return {marker: found[marker][2] if found[marker] is not None else None for marker in PROTECTED}


def apply_stat50_position_block(html, summary):
    """Replace only this derived block and place it directly below its source."""
    before = _inspect(html)
    if before[ANCHOR] is None:
        raise ValueError('Stat50 prediction card is required for its derived rates')
    own = before[MARKER]
    content = html
    if own is not None:
        left = own[0]
        if content[:left].endswith('\r\n'):
            left -= 2
        elif content[:left].endswith('\n'):
            left -= 1
        content = content[:left] + content[own[1]:]
    position = _inspect(content)[ANCHOR][1]
    suffix = content[position:]
    updated = content[:position] + '\n' + render_stat50_position_block(summary)
    updated += ('' if suffix.startswith('\n') else '\n') + suffix
    if _protected(before) != _protected(_inspect(updated)):
        raise ValueError('Stat50 position rates changed another sealed or derived block')
    return updated


def _failure_summary(snapshot):
    now = snapshot.get('now') if isinstance(snapshot, dict) else None
    now = now if isinstance(now, datetime) and now.tzinfo is not None else beijing_now()
    start = business_start(now)
    return {'version': 1, 'experiment_id': 'stat50-v1', 'revision': REVISION,
            'biz_date': start.strftime('%Y-%m-%d'), 'business_start': start.isoformat(),
            'updated_at': now.isoformat(), 'models': {}, 'latest_settled_period': None,
            'pending_count': 0, 'conflict_count': 0, 'rejected_count': 0, 'stats_error': True}


def refresh_stat50_position_panel(snapshot=None, root=ROOT):
    from panel_io import update_panel
    from stat50_position_stats import refresh_stat50_position_stats
    root = Path(root)
    try:
        summary = refresh_stat50_position_stats(snapshot=snapshot, root=root)
        if not isinstance(summary, dict):
            summary = _failure_summary(snapshot)
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        summary = _failure_summary(snapshot)
    update_panel(lambda html: apply_stat50_position_block(html, summary), path=root / 'panel.html')
    return summary
