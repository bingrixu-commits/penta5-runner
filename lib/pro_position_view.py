"""Independent Pro four-position cards and verified daily rate presentation."""
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
import re

from ai_verified_stats import next_period
from stats_history import TZ, beijing_now, business_start, load_snapshot, parse_period, period_slot

ROOT = Path('/root/penta5-runner')
MARKER = 'R1_PRO_COMPARE'
START, END = '<!-- R1_PRO_COMPARE_START -->', '<!-- R1_PRO_COMPARE_END -->'
STATS_MARKER = 'PRO_POSITION_STATS'
STATS_START, STATS_END = '<!-- PRO_POSITION_STATS_START -->', '<!-- PRO_POSITION_STATS_END -->'
LEGACY_MARKER = 'MODEL_COMPARE_STATS'
PROTECTED = ('R1_FRAMEWORK', 'R1_KILLER', 'STAT50_PREDICTION', 'HE3_REFINE',
             'R1_FRAMEWORK_STATS', 'R1_KILLER_STATS', 'STAT50_COMPARE_STATS', 'HE3_REFINE_STATS')
POSITIONS = (('k1', '千位', 1), ('k2', '百位', 2), ('k3', '十位', 3), ('k4', '个位', 4))
STYLE = ('width:calc(100% - 24px);max-width:1000px;margin:14px auto;padding:14px 16px;'
         'box-sizing:border-box;min-width:0;overflow-wrap:anywhere;border:1px solid #94a3b8;'
         'border-radius:8px;background:#fff;color:#334155;font:13px/1.8 sans-serif;')


def _reference(value):
    parsed = parse_period(value)
    if not isinstance(value, str) or not parsed or value != parsed[0]:
        raise ValueError('Invalid Pro four-position reference')
    return value


def _response(value):
    if not isinstance(value, dict) or not isinstance(value.get('reason'), str):
        raise ValueError('Invalid Pro four-position response')
    for key, _, _ in POSITIONS:
        item = value.get(key)
        if not isinstance(item, list) or len(item) != 1 or type(item[0]) is not int or not 0 <= item[0] <= 9:
            raise ValueError('Each Pro position must contain exactly one integer digit')
    return value


def _generated(value):
    if isinstance(value, datetime):
        value = value.isoformat() if value.tzinfo is not None and value.utcoffset() is not None else None
    if not isinstance(value, str):
        raise ValueError('Invalid Pro generation time')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise ValueError('Invalid Pro generation time') from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Pro generation time requires a timezone')
    return parsed.astimezone(TZ)


def card_fragment(reference, response, generated_at):
    """Canonical immutable card: four single exclusions; no attack or copy UI."""
    reference, response, generated = _reference(reference), _response(response), _generated(generated_at)
    deadline = period_slot(next_period(reference))
    if not period_slot(reference) <= generated < deadline:
        raise ValueError('Pro generation is outside the publication window')
    rows = []
    for key, label, index in POSITIONS:
        rows.append('<div data-pro4-row="' + key + '" style="padding:9px 10px;border:1px solid #e2e8f0;'
                    'border-radius:6px;min-width:0;overflow-wrap:anywhere;background:#f8fafc;">'
                    '<span>' + label + '（第' + str(index) + '位）排除：</span>'
                    '<b data-pro4-kill="' + key + '" style="display:inline-block;padding:0 9px;'
                    'color:#b91c1c;border-radius:10px;background:#fee2e2;font-size:17px;">'
                    + str(response[key][0]) + '</b></div>')
    return (START + '\n<section aria-label="Pro四位独立排除" style="' + STYLE + '">'
            '<b style="font-size:16px;color:#334155;">DeepSeek V4 Pro · 四位独立排除</b>'
            '<div id="pro4-active" data-reference="' + escape(reference, quote=True)
            + '" data-deadline="' + escape(deadline.isoformat(), quote=True) + '" hidden>'
            '<div style="font-size:12px;color:#64748b;">参考期：' + escape(reference)
            + ' ｜ 目标期：' + escape(next_period(reference)) + '<br>计算时间：'
            + escape(generated.strftime('%Y-%m-%d %H:%M:%S')) + '（北京时间）'
            + ' ｜ 参考截止：' + escape(deadline.strftime('%Y-%m-%d %H:%M:%S')) + '（北京时间）</div>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));'
            'gap:10px;margin-top:9px;min-width:0;">' + ''.join(rows) + '</div></div>'
            '<p id="pro4-unavailable" role="status" style="margin:6px 0;color:#64748b;">'
            '等待核对当前参考期；未核对前不显示排除数字。</p>'
            '<div style="font-size:12px;color:#64748b;">只使用前4球，每位独立排除1码；'
            '这是独立Pro任务，不改动原Flash框架及其攻击语。</div></section>\n' + END)


def _placeholder(reference):
    label = reference if isinstance(reference, str) and parse_period(reference) else '--'
    return (START + '\n<section aria-label="Pro四位独立排除" style="' + STYLE + '">'
            '<b style="font-size:16px;">DeepSeek V4 Pro · 四位独立排除</b>'
            '<p id="pro4-unavailable" role="status">当前参考期：' + escape(label)
            + '；本期四位独立结果尚未发布，等待本期更新。</p>'
            '<div style="font-size:12px;color:#64748b;">仅展示新四位任务的事前封存结果，'
            '不沿用旧两位任务的数字或成绩。</div></section>\n' + END)


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _summary(summary):
    raw = summary if isinstance(summary, dict) else {}
    error = raw.get('stats_error') is not False
    day = raw.get('biz_date')
    try:
        if not isinstance(day, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
            raise ValueError('Invalid Pro business day')
        day = date.fromisoformat(day).isoformat()
    except (ValueError, TypeError):
        day, error = '--', True
    source = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
    metrics = {}
    for key, _, _ in POSITIONS:
        item = source.get(key) if isinstance(source.get(key), dict) else {}
        hits, total = _count(item.get('hits')), _count(item.get('total'))
        if hits is None or total is None or hits > total:
            error = True
        metrics[key] = {'hits': hits, 'total': total}
    overall = raw.get('overall') if isinstance(raw.get('overall'), dict) else {}
    all_hits, all_total = _count(overall.get('hits')), _count(overall.get('total'))
    if (all_hits is None or all_total is None or all_hits > all_total
            or any(item['total'] != all_total for item in metrics.values())
            or any(item['hits'] is not None and all_hits is not None and all_hits > item['hits'] for item in metrics.values())):
        error = True
    maxima = {key: _count(overall.get(key)) for key in ('max_win_streak','max_loss_streak')}
    if all_hits is not None and all_total is not None and all_hits <= all_total:
        wins, losses = maxima['max_win_streak'], maxima['max_loss_streak']
        if (wins is None or losses is None or wins > all_hits or losses > all_total-all_hits
                or bool(wins) != bool(all_hits) or bool(losses) != bool(all_total-all_hits)):
            error = True
    else:
        error = True
    counters = {key: _count(raw.get(key)) for key in ('pending_count','overdue_count','conflict_count')}
    if any(value is None for value in counters.values()):
        error = True
    latest = raw.get('latest_settled_period')
    latest = latest if isinstance(latest, str) and parse_period(latest) and latest == parse_period(latest)[0] else '--'
    if error:
        metrics = {key: {'hits': None, 'total': None} for key, _, _ in POSITIONS}
        all_hits = all_total = None
        maxima = {key: None for key in maxima}
    return {'biz_date': day, 'metrics': metrics, 'overall': {'hits': all_hits, 'total': all_total, **maxima},
            'latest_settled_period': latest, 'stats_error': error, **counters}


PRO_SCRIPT = """<script id="pro4-stats-control">
(function(){
  var version='pro-four-rates-streaks-v3-platform-close';
  function businessDate(){var d=new Date(Date.now()+8*3600000);if(d.getUTCHours()<5)d.setUTCDate(d.getUTCDate()-1);return d.getUTCFullYear()+'-'+String(d.getUTCMonth()+1).padStart(2,'0')+'-'+String(d.getUTCDate()).padStart(2,'0');}
  function check(){
    var source=document.getElementById('pro4-stats-data'),data={stats_error:true};
    if(source){try{data=JSON.parse(source.textContent);}catch(error){data={stats_error:true};}}
    if(!data||typeof data!=='object'||Array.isArray(data))data={stats_error:true};
    var keys=['k1','k2','k3','k4'],today=data.biz_date===businessDate();
    function rate(item,prefix){
      if(data.stats_error!==false)return '统计核验失败，暂停显示';
      if(!today)return '统计周期已切换，等待本日核验';
      if(!item||!Number.isSafeInteger(item.hits)||!Number.isSafeInteger(item.total)||item.hits<0||item.total<0||item.hits>item.total)return '统计核验失败，暂停显示';
      return item.total?prefix+' '+(100*item.hits/item.total).toFixed(2)+'%（'+item.hits+'/'+item.total+'期）':'暂无已验证数据';
    }
    keys.forEach(function(key){var node=document.querySelector('[data-pro4-notice-key="'+key+'"]');if(node)node.textContent=rate((data.metrics||{})[key],'排除成功率');});
    var overall=document.getElementById('pro4-overall-rate');if(overall)overall.textContent=rate(data.overall,'四位全部通过');
    var streaks=document.getElementById('pro4-overall-streaks');
    if(streaks){
      var item=data.overall||{},w=item.max_win_streak,l=item.max_loss_streak;
      var countsValid=Number.isSafeInteger(item.hits)&&Number.isSafeInteger(item.total)&&item.hits>=0&&item.total>=item.hits;
      var maximaValid=Number.isSafeInteger(w)&&Number.isSafeInteger(l)&&w>=0&&l>=0&&countsValid&&w<=item.hits&&l<=item.total-item.hits&&!!w===!!item.hits&&!!l===!!(item.total-item.hits);
      streaks.textContent=data.stats_error!==false?'统计核验失败，暂停显示':!today?'统计周期已切换，等待本日核验':!maximaValid?'统计核验失败，暂停显示':item.total?'最高连中：'+w+'手 ｜ 最高连败：'+l+'手':'最高连中：— ｜ 最高连败：—';
    }
    var nodes=document.querySelectorAll('[id="pro4-active"]'),waiting=document.getElementById('pro4-unavailable');
    var current=String((document.getElementById('disp_period')||{}).textContent||'').trim();
    var active=nodes.length===1?nodes[0]:null;
    var reference=active?active.getAttribute('data-reference')||'':'';
    var deadline=active?Date.parse(active.getAttribute('data-deadline')||''):NaN;
    var numbers=!!active&&/^[0-9]{11}$/.test(reference)&&/^[0-9]{11}$/.test(current)&&reference<=current&&Number.isFinite(deadline)&&keys.every(function(key){var found=active.querySelectorAll('[data-pro4-kill="'+key+'"]');return found.length===1&&/^[0-9]$/.test(found[0].textContent.trim());});
    var valid=numbers&&reference===current&&Number.isFinite(Date.now())&&Date.now()<deadline-70000;
    Array.prototype.forEach.call(nodes,function(node){node.hidden=!numbers;});
    if(waiting){waiting.hidden=!!valid;if(!valid)waiting.textContent=numbers?'已关盘或为历史参考，仅供核对；封存号码保留。':'本期Pro四位独立结果不可用，等待本期更新。';}
    if(!valid){if(active)active.querySelectorAll('[data-pro4-hit-key]').forEach(function(node){node.remove();});return;}
    keys.forEach(function(key){
      var rows=active.querySelectorAll('[data-pro4-row="'+key+'"]');if(rows.length!==1)return;
      var found=rows[0].querySelectorAll('[data-pro4-hit-key]'),badge=null;
      Array.prototype.forEach.call(found,function(node){if(!badge&&node.getAttribute('data-pro4-hit-key')===key)badge=node;else node.remove();});
      if(!badge){badge=document.createElement('span');badge.setAttribute('data-pro4-hit-key',key);rows[0].appendChild(badge);}
      badge.style.cssText='display:block;max-width:100%;margin-top:5px;padding:1px 5px;color:#3730a3;background:#e0e7ff;border-radius:4px;white-space:normal;overflow-wrap:anywhere;font-size:12px;';
      badge.textContent=rate((data.metrics||{})[key],'排除成功率');
      badge.setAttribute('data-stats-business-date',data.biz_date||'--');
    });
  }
  function attach(state){window.addEventListener('load',state.check);window.addEventListener('pageshow',state.check);document.addEventListener('visibilitychange',state.check);if(state.timer!=null)clearInterval(state.timer);state.timer=setInterval(state.check,1000);state.check();}
  var prior=window.__PENTA5_PRO_FOUR__;
  if(prior&&prior.version===version&&typeof prior.check==='function'){attach(prior);return;}
  if(prior){if(prior.timer!=null)clearInterval(prior.timer);if(typeof prior.check==='function'){window.removeEventListener('load',prior.check);window.removeEventListener('pageshow',prior.check);document.removeEventListener('visibilitychange',prior.check);}}
  var state={version:version,check:check,timer:null};window.__PENTA5_PRO_FOUR__=state;attach(state);
})();
</script>"""


def _rate(item, error, prefix):
    if error:
        return '统计核验失败，暂停显示'
    return ('%s %.2f%%（%d/%d期）' % (prefix, item['hits']/item['total']*100, item['hits'], item['total'])
            if item['total'] else '暂无已验证数据')


def render_stats_block(summary):
    data = _summary(summary)
    rows = ''.join('<div><b>' + label + '：</b><span data-pro4-notice-key="' + key + '">'
                   + escape(_rate(data['metrics'][key], data['stats_error'], '排除成功率')) + '</span></div>'
                   for key, label, _ in POSITIONS)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, replacement in (('&','\\u0026'),('<','\\u003c'),('>','\\u003e'),('\u2028','\\u2028'),('\u2029','\\u2029')):
        encoded = encoded.replace(old,replacement)
    count = lambda key: str(data[key]) if data[key] is not None else '—'
    overall = data['overall']
    streaks = ('统计核验失败，暂停显示' if data['stats_error'] else
               '最高连中：%d手 ｜ 最高连败：%d手' % (overall['max_win_streak'],overall['max_loss_streak'])
               if overall['total'] else '最高连中：— ｜ 最高连败：—')
    return (STATS_START + '\n<section id="pro4-stats-notice" style="' + STYLE + 'background:#f8faff;">'
            '<b>Pro四位独立排除 · 今日已验证战绩</b> ｜ ' + escape(data['biz_date']) + ' 05:00起（北京时间）'
            + rows + '<b id="pro4-overall-rate">' + escape(_rate(data['overall'],data['stats_error'],'四位全部通过'))
            + '</b><div id="pro4-overall-streaks">' + escape(streaks) + '</div><div>最近核验期：' + escape(data['latest_settled_period']) + ' ｜ 待核验：' + count('pending_count')
            + '期 ｜ 超期：' + count('overdue_count') + '期 ｜ 冲突：' + count('conflict_count') + '期</div>'
            '<div>只使用前4球；对应实际数字不等于事前排除码才算成功。按实际开奖时间归日，'
            '北京时间每日05:00重新统计，历史证据保留。旧两位Pro任务不混入本成绩；原Flash与攻击语不变。</div>'
            '</section>\n<script type="application/json" id="pro4-stats-data">' + encoded + '</script>\n'
            + PRO_SCRIPT + '\n' + STATS_END)


def _inspect(html):
    if not isinstance(html,str):
        raise TypeError('Panel HTML must be text')
    found = {}
    for marker in (*PROTECTED,MARKER,STATS_MARKER,LEGACY_MARKER):
        start,end='<!-- '+marker+'_START -->','<!-- '+marker+'_END -->'
        counts=html.count(start),html.count(end)
        if counts==(0,0):found[marker]=None;continue
        if counts!=(1,1):raise ValueError('Missing or duplicated Pro panel markers')
        left,right=html.index(start),html.index(end)+len(end)
        if left>=right-len(end):raise ValueError('Reversed Pro panel markers')
        found[marker]=(left,right,html[left:right])
    intervals=sorted((value[0],value[1]) for value in found.values() if value is not None)
    if any(left<last_end for (_,last_end),(left,_) in zip(intervals,intervals[1:])):
        raise ValueError('Overlapping protected Pro panel blocks')
    return found


def _protected(found):
    return {key:found[key][2] if found[key] is not None else None for key in PROTECTED}


def _remove_legacy(html):
    block=_inspect(html)[LEGACY_MARKER]
    return html[:block[0]]+html[block[1]:] if block is not None else html


def _move_stats(html):
    found=_inspect(html);stats,card=found[STATS_MARKER],found[MARKER]
    if stats is None:return html
    if card is None:raise ValueError('Pro statistics require a Pro card')
    if stats[0]>=card[1] and not html[card[1]:stats[0]].strip():return html
    clean=html[:stats[0]]+html[stats[1]:];position=_inspect(clean)[MARKER][1]
    suffix=clean[position:]
    return clean[:position]+'\n'+stats[2]+('' if suffix.startswith('\n') else '\n')+suffix


def compose_pro_transform(html, transform):
    before=_inspect(html)
    if not callable(transform):raise TypeError('Pro transform must be callable')
    changed=transform(html);checked=_inspect(changed)
    if _protected(before)!=_protected(checked):raise ValueError('Pro publication changed protected original blocks')
    prior_stats=before[STATS_MARKER][2] if before[STATS_MARKER] else None
    next_stats=checked[STATS_MARKER][2] if checked[STATS_MARKER] else None
    if prior_stats!=next_stats:raise ValueError('Pro publication changed its derived statistics')
    if checked[MARKER] is None:raise ValueError('Pro transform did not produce an owned card')
    updated=_move_stats(_remove_legacy(changed))
    if _protected(before)!=_protected(_inspect(updated)):raise ValueError('Pro placement changed protected original blocks')
    return updated


def _fragment(fragment, marker):
    if not isinstance(fragment,str):raise TypeError('Pro fragment must be text')
    fragment=fragment.strip();block=_inspect(fragment)[marker]
    if block is None or block[0]!=0 or block[1]!=len(fragment):raise ValueError('Expected one complete owned Pro fragment')
    return fragment


def apply_pro_block(html, fragment):
    fragment=_fragment(fragment,MARKER)
    def transform(content):
        block=_inspect(content)[MARKER]
        if block is not None:return content[:block[0]]+fragment+content[block[1]:]
        if content.count('</body>')!=1:raise ValueError('Missing or ambiguous panel body')
        return content.replace('</body>',fragment+'\n</body>',1)
    return compose_pro_transform(html,transform)


def apply_pro_stats_block(html, fragment):
    fragment=_fragment(fragment,STATS_MARKER);before=_inspect(html)
    if before[MARKER] is None:raise ValueError('Pro statistics require a Pro card')
    block=before[STATS_MARKER]
    if block is not None:updated=html[:block[0]]+fragment+html[block[1]:]
    else:
        if html.count('</body>')!=1:raise ValueError('Missing or ambiguous panel body')
        updated=html.replace('</body>',fragment+'\n</body>',1)
    updated=_move_stats(_remove_legacy(updated));after=_inspect(updated)
    if _protected(before)!=_protected(after) or before[MARKER][2]!=after[MARKER][2]:
        raise ValueError('Pro statistics changed a sealed card or original block')
    return updated


def refresh_pro_panel(snapshot=None, root=ROOT):
    from pro_position_model import select_current
    from panel_io import update_panel
    root=Path(root)
    if snapshot is None:
        snapshot=load_snapshot(state_path=root/'data/state.json',cache_path=root/'data/stats_draw_history.json',persist=False)
    try:
        from pro_position_stats import refresh_pro_stats
        summary=refresh_pro_stats(snapshot=snapshot,root=root)
    except Exception:
        now=snapshot.get('now') if isinstance(snapshot.get('now'),datetime) else beijing_now()
        summary={'biz_date':business_start(now).strftime('%Y-%m-%d'),'stats_error':True,
                 'latest_settled_period':None,'pending_count':0,'overdue_count':0,'conflict_count':0}
    stats=render_stats_block(summary)
    result={}

    def transform(html):
        # A new card may publish while the frozen statistics are being scanned.
        # Read display evidence under that publisher's shared panel lock.
        live=load_snapshot(state_path=root/'data/state.json',cache_path=root/'data/stats_draw_history.json',persist=False)
        reference=str((live.get('latest') or {}).get('period') or '')
        try:seal=select_current(root,live)
        except (OSError,ValueError,TypeError,KeyError):seal=None
        available=isinstance(seal,dict) and seal.get('ref_period')==reference
        if available:
            try:_response(seal.get('response'))
            except ValueError:available=False
        result.update(reference=reference,available=available)
        # A publisher writes its exact card before acknowledging its ledger.
        # Preserve a current card in that short window without inventing an ack.
        existing=_inspect(html)[MARKER]
        matches=re.findall(r'id="pro4-active" data-reference="([0-9]{11})" data-deadline="([^"]+)"',existing[2] if existing else '')
        if len(matches)==1:
            old_reference,old_deadline=matches[0]
            try:
                moment=_generated(live['now'])
                deadline=period_slot(next_period(_reference(old_reference)))
                current=period_slot(old_reference)<=moment<deadline and _generated(old_deadline)==deadline
            except (ValueError,TypeError,KeyError,OverflowError):current=False
            if current and (not available or old_reference>reference):
                result['available']=False
                result['preserved_current_panel']=True
                return apply_pro_stats_block(html,stats)
        fragment=seal['card_html'] if available else _placeholder(reference)
        return apply_pro_stats_block(apply_pro_block(html,fragment),stats)

    update_panel(transform,path=root/'panel.html')
    return {**summary,**result}
