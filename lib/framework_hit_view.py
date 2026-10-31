"""Render verified framework rates beside, never inside, immutable served cards."""
from datetime import date
from html import escape
import json
import re

START = '<!-- R1_FRAMEWORK_STATS_START -->'
END = '<!-- R1_FRAMEWORK_STATS_END -->'
CARD_START = '<!-- R1_FRAMEWORK_START -->'
CARD_END = '<!-- R1_FRAMEWORK_END -->'
METRIC_KEYS = ('q_kill1', 'b_kill1', 'he_h3_kill', 'he_q3_kill',
               'pattern_ds', 'pattern_dx', 'he2_keep', 'he3_keep')


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9007199254740991 else None


def _period(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{11}', value):
        return None
    try:
        day = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None
    return value if 1 <= int(value[8:]) <= 288 and day.isoformat().replace('-', '') == value[:8] else None


def _summary(summary):
    summary = summary if isinstance(summary, dict) else {}
    raw_metrics = summary.get('metrics')
    raw_metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
    metrics = {}
    for key in METRIC_KEYS:
        raw = raw_metrics.get(key)
        raw = raw if isinstance(raw, dict) else {}
        hits, total = _count(raw.get('hits')), _count(raw.get('total'))
        if hits is None or total is None or hits > total:
            hits, total = 0, 0
        metrics[key] = {'hits': hits, 'total': total,
                        'percent': round(100 * hits / total, 4) if total else None}
    raw_overall = summary.get('overall')
    raw_overall = raw_overall if isinstance(raw_overall, dict) else {}
    values = {key: _count(raw_overall.get(key)) for key in
              ('hits', 'total', 'max_win_streak', 'max_loss_streak',
               'current_win_streak', 'current_loss_streak')}
    hits, total = values['hits'], values['total']
    wins, losses = values['max_win_streak'], values['max_loss_streak']
    current_wins, current_losses = values['current_win_streak'], values['current_loss_streak']
    interrupted = raw_overall.get('current_streak_interrupted', False)
    as_of = raw_overall.get('current_streak_as_of')
    last_verified = _period(summary.get('latest_settled_period'))
    valid_interruption = (type(interrupted) is bool and (
        interrupted is False and as_of is None or
        interrupted is True and _period(as_of) is not None and last_verified is not None
        and as_of > last_verified and current_wins == 0 and current_losses == 0))
    valid = (all(value is not None for value in values.values())
             and hits <= total and wins <= hits and losses <= total - hits
             and (wins > 0) == (hits > 0) and (losses > 0) == (total > hits)
             and current_wins <= wins and current_losses <= losses
             and not (current_wins and current_losses)
             and valid_interruption
             and ((current_wins + current_losses > 0) == (total > 0) or interrupted is True)
             and all(metric['total'] == total for metric in metrics.values()))
    overall = {**values, 'available': valid and total > 0,
               'current_streak_interrupted': interrupted,
               'current_streak_as_of': as_of} if valid else {
        'hits': 0, 'total': 0, 'max_win_streak': 0, 'max_loss_streak': 0,
        'current_win_streak': 0, 'current_loss_streak': 0, 'available': False,
        'current_streak_interrupted': False, 'current_streak_as_of': None}
    raw_day = str(summary.get('biz_date') or '')
    try:
        day = date.fromisoformat(raw_day).isoformat()
    except ValueError:
        day = '--'
    latest = str(summary.get('latest_settled_period') or '')
    latest = latest if re.fullmatch(r'[0-9]{11}', latest) else '--'
    pending = _count(summary.get('pending_count'))
    conflicts = _count(summary.get('conflict_count'))
    return {'biz_date': day, 'metrics': metrics, 'overall': overall, 'latest_settled_period': latest,
            'pending_count': pending if pending is not None else 0,
            'conflict_count': conflicts if conflicts is not None else 0,
            'stats_error': summary.get('stats_error') is True}


SCRIPT = """<script id="r1-framework-hit-script">
(function(){
  var script=document.currentScript||document.getElementById('r1-framework-hit-script');
  var payload=document.getElementById('r1-framework-hit-data');
  if(!script||!payload)return;
  var data;
  try{data=JSON.parse(payload.textContent||'{}');}catch(error){return;}
  var node=script.previousSibling,end=null,start=null,card=null;
  while(node){
    if(node.nodeType===8&&String(node.nodeValue||'').trim()==='R1_FRAMEWORK_END'){end=node;break;}
    node=node.previousSibling;
  }
  if(!end)return;
  node=end.previousSibling;
  var candidates=[];
  while(node){
    if(node.nodeType===8&&String(node.nodeValue||'').trim()==='R1_FRAMEWORK_START'){start=node;break;}
    if(node.nodeType===1&&String(node.tagName).toUpperCase()==='DIV')candidates.push(node);
    node=node.previousSibling;
  }
  if(!start||candidates.length!==1)return;
  card=candidates[0];
  card.style.overflowWrap='anywhere';
  var grids=Array.prototype.filter.call(card.querySelectorAll('div'),function(element){
    var rows=Array.prototype.slice.call(element.children||[]);
    return rows.length===8&&rows.every(function(row,index){
      var prefix=String(row.textContent||'').trim().match(/^([1-8])[.]/);
      return String(row.tagName).toUpperCase()==='DIV'&&prefix&&Number(prefix[1])===index+1;
    });
  });
  if(grids.length!==1)return;
  var keys=['q_kill1','b_kill1','he_h3_kill','he_q3_kill','pattern_ds','pattern_dx','he2_keep','he3_keep'];
  keys.forEach(function(key,index){
    var row=grids[0].children[index],badges=row.querySelectorAll('[data-r1-hit-key]');
    var badge=null;
    Array.prototype.forEach.call(badges,function(item){
      if(item.getAttribute('data-r1-hit-key')===key&&!badge)badge=item;
      else if(item.parentNode)item.parentNode.removeChild(item);
    });
    if(!badge){
      badge=document.createElement('span');
      badge.setAttribute('data-r1-hit-key',key);
      badge.style.cssText='display:inline-block;max-width:100%;white-space:normal;overflow-wrap:anywhere;margin:4px 0 0 6px;padding:2px 6px;border-radius:4px;background:#eff6ff;color:#1e40af;font-size:12px;line-height:1.6;vertical-align:middle;';
      row.appendChild(badge);
    }
    var metric=data.metrics&&data.metrics[key],label=index<6?'排除成功率':(key==='he2_keep'?'4码命中率':'6码命中率');
    badge.setAttribute('data-stats-business-date',data.biz_date);
    var valid=metric&&Number.isSafeInteger(metric.hits)&&Number.isSafeInteger(metric.total)&&metric.total>0&&metric.hits>=0&&metric.hits<=metric.total;
    badge.textContent=data.stats_error===true?'统计核验失败，暂停显示命中率':valid?label+' '+(metric.hits/metric.total*100).toFixed(2)+'%（'+metric.hits+'/'+metric.total+'期）':'暂无已验证数据';
    badge.title=data.stats_error===true?'统计核验失败，暂停显示命中率':(index===2||index===3?'此合尾项统计排除成功率：实际合尾不等于排除码。':index===6?'二数合保留4码随机基线98.08%；任一两位合尾落入保留集即命中。':index===7?'三数合保留6码随机基线97.44%；任一三位合尾落入保留集即命中。':'')+'仅计入开奖前发布、开奖后核验的前4球记录。';
  });
  var overall=card.querySelector('[data-r1-framework-overall]');
  if(!overall){
    overall=document.createElement('div');
    overall.setAttribute('data-r1-framework-overall','1');
    overall.style.cssText='margin:12px 0;padding:10px 12px;border:1px solid #bfdbfe;border-radius:6px;background:#eff6ff;color:#1e40af;font-size:13px;line-height:1.8;overflow-wrap:anywhere;';
    grids[0].parentNode.insertBefore(overall,grids[0].nextSibling);
  }
  overall.setAttribute('data-stats-business-date',data.biz_date);
  var group=data.overall;
  overall.textContent=data.stats_error===true?'整组战绩：统计核验失败，暂停显示':
    group&&group.available===true?'整组8项全部通过：'+(group.hits/group.total*100).toFixed(2)+'%（'+group.hits+'/'+group.total+'期） ｜ 当前连中：'+group.current_win_streak+'手 ｜ 当前连败：'+group.current_loss_streak+'手 ｜ 最高连中：'+group.max_win_streak+'手 ｜ 最高连败：'+group.max_loss_streak+'手':
    '整组8项全部通过：暂无已验证数据 ｜ 当前连中：— ｜ 当前连败：— ｜ 最高连中：— ｜ 最高连败：—';
  if(data.stats_error!==true&&group&&group.available===true&&group.current_streak_interrupted===true){
    var interrupted=document.createElement('div');interrupted.setAttribute('data-r1-current-streak-interrupted','1');
    interrupted.style.color='#b45309';
    interrupted.textContent='最新已开奖期'+group.current_streak_as_of+'尚无有效核验，当前连续段已中断。';
    overall.appendChild(interrupted);
  }
  overall.title='北京时间每日05:00起；同一期8项全部通过算连中，任一项未通过算连败；未开奖不计，已开奖缺期或冲突期中断当前连续段，不计作失手。';
})();
</script>"""


def render_stats_block(summary):
    """Return an independent metadata block plus safe, repeatable DOM badges."""
    data = _summary(summary)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, replacement in [('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                              ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        encoded = encoded.replace(old, replacement)
    notice = (
        '<div id="r1_framework_stats_notice" data-stats-business-date="' + escape(data['biz_date'], quote=True) + '" style="max-width:1000px;margin:8px auto 16px;padding:8px 12px;'
        'box-sizing:border-box;border:1px solid #bfdbfe;border-radius:6px;background:#f8fbff;color:#475569;'
        'font-family:sans-serif;font-size:12px;line-height:1.7;overflow-wrap:anywhere;">'
        '<b>框架8项本版已验证战绩</b> ｜ ' + escape(data['biz_date']) + ' 05:00起（北京时间）'
        '<br>仅统计本版开奖前已发布、开奖后完成核验的记录；旧策略证据保留，不并入本版。只使用前4球。'
        '前6项显示排除成功率，后二项显示保留集命中率（二数合保留4码、三数合保留6码）。'
        '整组战绩以同一期8项全部通过为准；缺期或冲突期中断连续。'
        '当前连续段按最新已开奖期核验；未开奖不计，已开奖缺期、冲突或未核验会中断，不计作失手。'
        '<br>最近核验期：' + escape(data['latest_settled_period'])
        + ' ｜ 待核验：' + str(data['pending_count']) + '期'
        + ('<br><b style="color:#b45309;">最新已开奖期' + escape(data['overall']['current_streak_as_of'])
           + '尚无有效核验，当前连续段已中断。</b>'
           if data['overall']['available'] and data['overall']['current_streak_interrupted'] else '')
        + ('<br><b style="color:#b45309;">有' + str(data['conflict_count']) + '期开奖号码存在冲突，未计入命中率，等待复核。</b>' if data['conflict_count'] else '')
        + '</div>')
    if data['stats_error']:
        notice = ('<div id="r1_framework_stats_notice" data-stats-business-date="' + escape(data['biz_date'], quote=True)
                  + '" style="max-width:1000px;margin:8px auto 16px;padding:8px 12px;box-sizing:border-box;'
                    'border:1px solid #fca5a5;border-radius:6px;background:#fff7f7;color:#991b1b;'
                    'font:12px/1.7 sans-serif;overflow-wrap:anywhere;">'
                    '<b>统计核验失败，暂停显示命中率</b><br>请等待统计修复；封存预测保持不变。</div>')
    return (START + '\n' + notice + '\n'
            '<script type="application/json" id="r1-framework-hit-data">' + encoded + '</script>\n'
            + SCRIPT + '\n' + END)


def apply_stats_block(html, summary):
    """Insert after the sealed card; refuse missing/ambiguous/overlapping markers."""
    if not isinstance(html, str):
        raise TypeError('Panel HTML must be text')
    if html.count(CARD_START) != 1 or html.count(CARD_END) != 1:
        return html
    card_start, card_end = html.index(CARD_START), html.index(CARD_END) + len(CARD_END)
    if card_end <= card_start:
        return html
    old_pattern = re.compile(r'(?:\r?\n)?' + re.escape(START) + r'.*?' + re.escape(END), re.S)
    for old in old_pattern.finditer(html):
        if old.start() < card_end and old.end() > card_start:
            return html
    updated = old_pattern.sub('', html)
    end = updated.index(CARD_END) + len(CARD_END)
    return updated[:end] + '\n' + render_stats_block(summary) + updated[end:]

