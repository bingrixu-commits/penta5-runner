"""Render verified framework rates beside, never inside, immutable served cards."""
from datetime import date
from html import escape
from pathlib import Path
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
    raw_rolling = summary.get('rolling')
    raw_rolling = raw_rolling if isinstance(raw_rolling, dict) else {}
    raw_rolling_metrics = raw_rolling.get('metrics')
    raw_rolling_metrics = raw_rolling_metrics if isinstance(raw_rolling_metrics, dict) else {}
    rolling_metrics = {}
    rolling_valid = True
    for key in METRIC_KEYS:
        raw = raw_rolling_metrics.get(key)
        raw = raw if isinstance(raw, dict) else {}
        hits, total = _count(raw.get('hits')), _count(raw.get('total'))
        if hits is None or total is None or hits > total:
            hits, total = 0, 0
            rolling_valid = False
        rolling_metrics[key] = {'hits': hits, 'total': total,
                                'percent': round(100 * hits / total, 4) if total else None}
    raw_rolling_overall = raw_rolling.get('overall')
    raw_rolling_overall = raw_rolling_overall if isinstance(raw_rolling_overall, dict) else {}
    rolling_hits, rolling_total = _count(raw_rolling_overall.get('hits')), _count(raw_rolling_overall.get('total'))
    rolling_window, rolling_target = _count(raw_rolling.get('window')), _count(raw_rolling.get('window_target'))
    if (rolling_hits is None or rolling_total is None or rolling_hits > rolling_total
            or rolling_window is None or rolling_target is None
            or not 0 < rolling_target <= 10000 or rolling_window != rolling_total
            or rolling_total > rolling_target
            or any(metric['total'] != rolling_total for metric in rolling_metrics.values())):
        rolling_valid = False
    rolling = {'available': bool(rolling_valid and rolling_total
                              and raw_rolling.get('available') is True),
               'window': rolling_window if rolling_window is not None else 0,
               'window_target': rolling_target if rolling_target is not None else 0,
               'metrics': rolling_metrics if rolling_valid else {},
               'overall': {'hits': rolling_hits if rolling_valid else 0,
                           'total': rolling_total if rolling_valid else 0,
                           'percent': round(100 * rolling_hits / rolling_total, 4)
                                      if rolling_valid and rolling_total else None}}
    raw_day = str(summary.get('biz_date') or '')
    try:
        day = date.fromisoformat(raw_day).isoformat()
    except ValueError:
        day = '--'
    latest = str(summary.get('latest_settled_period') or '')
    latest = latest if re.fullmatch(r'[0-9]{11}', latest) else '--'
    pending = _count(summary.get('pending_count'))
    conflicts = _count(summary.get('conflict_count'))
    return {'biz_date': day, 'metrics': metrics, 'overall': overall, 'rolling': rolling,
            'latest_settled_period': latest,
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
  var rollAvailable=data.rolling&&data.rolling.available===true&&Number.isSafeInteger(data.rolling.window)&&data.rolling.window>0;
  var rollingPrefix=rollAvailable?('滚动'+data.rolling.window+'期：'+(data.rolling.overall.hits/data.rolling.overall.total*100).toFixed(2)+'%（'+data.rolling.overall.hits+'/'+data.rolling.overall.total+'期） ｜ '):'';
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
    var rollMetric=rollAvailable&&data.rolling.metrics?data.rolling.metrics[key]:null;
    var rollValid=rollMetric&&Number.isSafeInteger(rollMetric.hits)&&Number.isSafeInteger(rollMetric.total)&&rollMetric.total>0&&rollMetric.hits>=0&&rollMetric.hits<=rollMetric.total;
    var rollText=rollAvailable?(rollValid?'｜滚动'+data.rolling.window+'期 '+(rollMetric.hits/rollMetric.total*100).toFixed(2)+'%（'+rollMetric.hits+'/'+rollMetric.total+'期）':'｜滚动口径暂无'):'';
    badge.textContent=data.stats_error===true?'统计核验失败，暂停显示命中率':valid?label+' 本日 '+(metric.hits/metric.total*100).toFixed(2)+'%（'+metric.hits+'/'+metric.total+'期）'+rollText:'暂无已验证数据';
    badge.title=data.stats_error===true?'统计核验失败，暂停显示命中率':(index===2||index===3?'此合尾项统计排除成功率：实际合尾不等于排除码，随机基线90%。':index<2?'定位杀码随机基线90%（实际数字不等于杀码即成功）。':index===4||index===5?'四位反向形态随机基线93.75%（实际形态不等于排除形态即成功）。':index===6?'二数合保留4码随机基线98.08%；任一两位合尾落入保留集即命中。':index===7?'三数合保留6码随机基线97.44%；任一三位合尾落入保留集即命中。':'')+'仅计入开奖前发布、开奖后核验的前4球记录。';
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
    group&&group.available===true?'整组8项全部通过：'+rollingPrefix+'本日 '+(group.hits/group.total*100).toFixed(2)+'%（'+group.hits+'/'+group.total+'期） ｜ 当前连中：'+group.current_win_streak+'手 ｜ 当前连败：'+group.current_loss_streak+'手 ｜ 最高连中：'+group.max_win_streak+'手 ｜ 最高连败：'+group.max_loss_streak+'手':
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


def _cumulative_line():
    """只读累计口径行（数据源 data/hitrate_stability.json）；缺失/异常返回空串。"""
    try:
        report = json.loads(Path('/root/penta5-runner/data/hitrate_stability.json')
                            .read_text(encoding='utf-8'))
        rows = report.get('rows') if isinstance(report, dict) else None
        if not isinstance(rows, list):
            return ''
        row = next((item for item in rows
                    if isinstance(item, dict) and item.get('key') == 'framework'), None)
        if not row:
            return ''
        n = row.get('n')
        hits = row.get('hits')
        rate = row.get('rate')
        expected = row.get('expected')
        if not isinstance(n, int) or n < 1 or not isinstance(hits, int) or rate is None:
            return ''
        ci = row.get('ci95')
        ci_text = ('，95%%CI %.1f%%–%.1f%%' % (ci[0] * 100.0, ci[1] * 100.0)
                   if isinstance(ci, (list, tuple)) and len(ci) >= 2 else '')
        parts = ['<b>累计口径</b>（自账本首期，跨天不清零）',
                 '整组 <b>%.2f%%</b>（%d/%d 期%s）' % (float(rate) * 100.0, hits, n, ci_text)]
        if expected is not None:
            parts.append('精确基线 %.2f%%' % (float(expected) * 100.0))
        delta = row.get('delta')
        p = row.get('p')
        if delta is not None:
            parts.append('偏差 %+.2fpp' % (float(delta) * 100.0))
        if p is not None:
            parts.append('二项 p=%.3f' % float(p))
        drawdown = row.get('drawdown') if isinstance(row.get('drawdown'), dict) else {}
        random_ref = drawdown.get('random') if isinstance(drawdown.get('random'), dict) else {}
        if drawdown.get('n') == n and drawdown.get('current_dd_pp') is not None:
            text = ('自峰值回撤 %.1fpp（峰值 %.1f%% 在 n=%s）'
                    % (float(drawdown['current_dd_pp']),
                       float(drawdown.get('peak_rate') or 0) * 100.0,
                       drawdown.get('peak_n')))
            if random_ref.get('median_pp') is not None:
                text += ('；同 n 纯随机序列最大回撤中位 %.1fpp / p90 %.1fpp，P≥该回撤 %.0f%%'
                         % (float(random_ref['median_pp']), float(random_ref['p90_pp']),
                            float(random_ref.get('p_ge_observed') or 0) * 100.0))
            parts.append(text)
        item = row.get('item_stats') if isinstance(row.get('item_stats'), dict) else {}
        if item.get('items') and item.get('avg_items') is not None:
            parts.append('平均命中 %.2f/%d 项（项级 %.1f%%）'
                         % (float(item['avg_items']), int(item['items']),
                            float(item.get('item_rate') or 0) * 100.0))
        return '<br>' + ' · '.join(parts)
    except Exception:
        return ''


def render_stats_block(summary):
    """Return an independent metadata block plus safe, repeatable DOM badges."""
    data = _summary(summary)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, replacement in [('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                              ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        encoded = encoded.replace(old, replacement)
    cumulative_line = _cumulative_line()
    rolling_line = (('<br>滚动口径（最近' + str(data['rolling']['window']) + '个已验证期）：整组8项全过 '
                    + '%.2f%%' % data['rolling']['overall']['percent']
                    + '（' + str(data['rolling']['overall']['hits']) + '/' + str(data['rolling']['overall']['total']) + '期）')
                   if data['rolling']['available'] else '')
    notice = (
        '<div id="r1_framework_stats_notice" data-stats-business-date="' + escape(data['biz_date'], quote=True) + '" style="max-width:1000px;margin:8px auto 16px;padding:8px 12px;'
        'box-sizing:border-box;border:1px solid #bfdbfe;border-radius:6px;background:#f8fbff;color:#475569;'
        'font-family:sans-serif;font-size:12px;line-height:1.7;overflow-wrap:anywhere;">'
        '<b>框架8项本版已验证战绩</b> ｜ ' + escape(data['biz_date']) + ' 05:00起（北京时间）'
        '<br>仅统计本版开奖前已发布、开奖后完成核验的记录；旧策略证据保留，不并入本版。只使用前4球。'
        '前6项显示排除成功率，后二项显示保留集命中率（二数合保留4码、三数合保留6码）。'
        '前6项随机基线均为90%（10选1杀1码，单尾项同样90%）；单日n<30波动大，请以滚动口径与累计样本判断。'
        '合尾两项启用稳定化：上期两项均命中时沿用上期尾码，最多自动沿用3期；封存元数据 tail_hold3 留档，命中口径不变。'
        '整组战绩以同一期8项全部通过为准；缺期或冲突期中断连续。'
        '当前连续段按最新已开奖期核验；未开奖不计，已开奖缺期、冲突或未核验会中断，不计作失手。'
        '<br>最近核验期：' + escape(data['latest_settled_period'])
        + ' ｜ 待核验：' + str(data['pending_count']) + '期'
        + ('<br><b style="color:#b45309;">最新已开奖期' + escape(data['overall']['current_streak_as_of'])
           + '尚无有效核验，当前连续段已中断。</b>'
           if data['overall']['available'] and data['overall']['current_streak_interrupted'] else '')
        + ('<br><b style="color:#b45309;">有' + str(data['conflict_count']) + '期开奖号码存在冲突，未计入命中率，等待复核。</b>' if data['conflict_count'] else '')
        + rolling_line + cumulative_line
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

