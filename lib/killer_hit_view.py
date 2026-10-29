"""Overlay today's verified killer rates without altering immutable card HTML."""
from datetime import date
from html import escape
import json
import re

START = '<!-- R1_KILLER_STATS_START -->'
END = '<!-- R1_KILLER_STATS_END -->'
CARD_START = '<!-- R1_KILLER_START -->'
CARD_END = '<!-- R1_KILLER_END -->'
METRIC_KEYS = ('k1', 'k2', 'k3', 'k4', 'he_all', 'he_q3', 'he_h3', 'pat1', 'pat2')


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9007199254740991 else None


def _summary(summary):
    summary = summary if isinstance(summary, dict) else {}
    source = summary.get('metrics') if isinstance(summary.get('metrics'), dict) else {}
    metrics = {}
    for key in METRIC_KEYS:
        metric = source.get(key) if isinstance(source.get(key), dict) else {}
        hits, total = _count(metric.get('hits')), _count(metric.get('total'))
        if hits is None or total is None or hits > total:
            hits, total = 0, 0
        metrics[key] = {'hits': hits, 'total': total,
                        'percent': round(hits * 100 / total, 4) if total else None}
    try:
        day = date.fromisoformat(str(summary.get('biz_date') or '')).isoformat()
    except ValueError:
        day = '--'
    latest = str(summary.get('latest_settled_period') or '')
    latest = latest if re.fullmatch(r'[0-9]{11}', latest) else '--'
    pending = _count(summary.get('pending_count'))
    conflicts = _count(summary.get('conflict_count'))
    return {'biz_date': day, 'metrics': metrics, 'latest_settled_period': latest,
            'pending_count': pending if pending is not None else 0,
            'conflict_count': conflicts if conflicts is not None else 0,
            'stats_error': summary.get('stats_error') is True}


SCRIPT = r"""<script id="r1-killer-hit-script">
(function(){
  var script=document.currentScript||document.getElementById('r1-killer-hit-script');
  var payload=document.getElementById('r1-killer-hit-data');
  if(!script||!payload)return;
  var data;
  try{data=JSON.parse(payload.textContent||'{}');}catch(error){return;}
  var node=script.previousSibling,end=null,start=null;
  while(node){
    if(node.nodeType===8&&String(node.nodeValue||'').trim()==='R1_KILLER_END'){end=node;break;}
    node=node.previousSibling;
  }
  if(!end)return;
  var cards=[];
  node=end.previousSibling;
  while(node){
    if(node.nodeType===8&&String(node.nodeValue||'').trim()==='R1_KILLER_START'){start=node;break;}
    if(node.nodeType===1&&String(node.tagName).toUpperCase()==='DIV')cards.push(node);
    node=node.previousSibling;
  }
  if(!start||cards.length!==1)return;
  var card=cards[0];
  card.style.overflowWrap='anywhere';
  var keys=['k1','k2','k3','k4','he_all','he_q3','he_h3','pat1','pat2'];
  var spans=Array.prototype.slice.call(card.querySelectorAll('span[data-r1-killer-hit-key]'));
  if(spans.length===9){
    spans=keys.map(function(key){return spans.filter(function(span){return span.getAttribute('data-r1-killer-hit-key')===key;})[0];});
    if(spans.some(function(span){return !span;}))return;
  }else{
    spans=Array.prototype.filter.call(card.querySelectorAll('span'),function(span){
      var text=String(span.textContent||'').trim();
      var rate=/^[(（]\s*[0-9]+\s*[/]\s*[0-9]+\s+[0-9]+(?:[.][0-9]+)?%\s*[)）]$/.test(text);
      return span.style.fontSize==='12px'&&(rate||text==='(待首期开奖结算)');
    });
    if(spans.length!==9)return;
  }
  var currentDay=new Date(Date.now()+3*3600000).toISOString().slice(0,10);
  var sameDay=data.biz_date===currentDay;
  keys.forEach(function(key,index){
    var span=spans[index],metric=data.metrics&&data.metrics[key];
    span.setAttribute('data-r1-killer-hit-key',key);
    span.setAttribute('data-stats-business-date',String(data.biz_date||''));
    span.style.cssText+=';display:inline-block;max-width:100%;white-space:normal;overflow-wrap:anywhere;vertical-align:middle;';
    var valid=sameDay&&metric&&Number.isSafeInteger(metric.hits)&&Number.isSafeInteger(metric.total)&&metric.total>0&&metric.hits>=0&&metric.hits<=metric.total;
    span.textContent=data.stats_error===true?'统计核验失败，暂停显示命中率':!sameDay?'北京时间05:00已清零，等待本周期数据':valid?'排除成功率 '+(metric.hits/metric.total*100).toFixed(2)+'%（'+metric.hits+'/'+metric.total+'期）':'暂无今日已验证数据';
    span.title=data.stats_error===true?'统计核验失败，暂停显示命中率':'北京时间05:00起，仅统计开奖前发布且开奖后核验的前4球；不含历史未核验数据。';
  });
})();
</script>"""


def render_killer_stats_block(summary):
    data = _summary(summary)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, replacement in [('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                              ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        encoded = encoded.replace(old, replacement)
    notice = ('<div id="r1_killer_stats_notice" data-stats-business-date="' + escape(data['biz_date'], quote=True)
              + '" style="max-width:1000px;margin:8px auto 16px;padding:8px 12px;box-sizing:border-box;'
              'border:1px solid #bfdbfe;border-radius:6px;background:#f8fbff;color:#475569;font:12px/1.7 sans-serif;overflow-wrap:anywhere;">'
              '<b>智能排除9项本版今日已验证战绩</b> ｜ ' + escape(data['biz_date']) + ' 05:00起（北京时间）'
              '<br>只使用前4球，按实际开奖时间归入当日；旧策略证据保留，不并入本版。0样本不显示命中百分比。'
              '<br>最近核验期：' + escape(data['latest_settled_period'])
              + ' ｜ 待核验：' + str(data['pending_count']) + '期'
              + ('<br><b style="color:#b45309;">有' + str(data['conflict_count']) + '期开奖号码存在冲突，未计入命中率，等待复核。</b>' if data['conflict_count'] else '')
              + '</div>')
    if data['stats_error']:
        notice = ('<div id="r1_killer_stats_notice" data-stats-business-date="' + escape(data['biz_date'], quote=True)
                  + '" style="max-width:1000px;margin:8px auto 16px;padding:8px 12px;box-sizing:border-box;'
                    'border:1px solid #fca5a5;border-radius:6px;background:#fff7f7;color:#991b1b;'
                    'font:12px/1.7 sans-serif;overflow-wrap:anywhere;">'
                    '<b>统计核验失败，暂停显示命中率</b><br>请等待统计修复；封存预测保持不变。</div>')
    return (START + '\n' + notice + '\n'
            '<script type="application/json" id="r1-killer-hit-data">' + encoded + '</script>\n'
            + SCRIPT + '\n' + END)


def apply_killer_stats_block(html, summary):
    if not isinstance(html, str):
        raise TypeError('Panel HTML must be text')
    if html.count(CARD_START) != 1 or html.count(CARD_END) != 1:
        return html
    left, right = html.index(CARD_START), html.index(CARD_END) + len(CARD_END)
    if right <= left:
        return html
    pattern = re.compile(r'(?:\r?\n)?' + re.escape(START) + r'.*?' + re.escape(END), re.S)
    if any(match.start() < right and match.end() > left for match in pattern.finditer(html)):
        return html
    updated = pattern.sub('', html)
    end = updated.index(CARD_END) + len(CARD_END)
    return updated[:end] + '\n' + render_killer_stats_block(summary) + updated[end:]

