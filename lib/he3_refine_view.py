"""Independent presentation of an extra exclusion within the original six tails.

The eight existing cards/statistics blocks are immutable to this writer. The
core owns model calls and proof validation; this module only renders and moves
its own complete marker outside those blocks.
"""
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
import re

from ai_verified_stats import next_period
from stats_history import TZ, load_snapshot, parse_period, period_slot

ROOT = Path('/root/penta5-runner')
MARKER = 'HE3_REFINE'
START = '<!-- HE3_REFINE_START -->'
END = '<!-- HE3_REFINE_END -->'
STATS_MARKER = 'HE3_REFINE_STATS'
STATS_START = '<!-- HE3_REFINE_STATS_START -->'
STATS_END = '<!-- HE3_REFINE_STATS_END -->'
PROTECTED = ('R1_FRAMEWORK', 'R1_KILLER', 'R1_PRO_COMPARE', 'STAT50_PREDICTION',
             'R1_FRAMEWORK_STATS', 'R1_KILLER_STATS', 'MODEL_COMPARE_STATS',
             'STAT50_COMPARE_STATS')
ANCHOR = 'R1_FRAMEWORK_STATS'


def _reference(value):
    parsed = parse_period(value)
    if not isinstance(value, str) or not parsed or value != parsed[0]:
        raise ValueError('Invalid refinement reference')
    return value


def _digits(value, count):
    if (not isinstance(value, str) or len(value) != count or len(set(value)) != count
            or any(d not in '0123456789' for d in value)):
        raise ValueError('Invalid refinement digit set')
    return value


def _generated(value):
    if isinstance(value, datetime):
        value = value.isoformat() if value.tzinfo is not None and value.utcoffset() is not None else None
    if not isinstance(value, str):
        raise ValueError('Invalid refinement generation time')
    try:
        aware = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise ValueError('Invalid refinement generation time') from None
    if aware.tzinfo is None or aware.utcoffset() is None:
        raise ValueError('Refinement generation time must include its timezone')
    return aware.astimezone(TZ)


STALE_SCRIPT = """<script id="he3-refine-availability-control">
(function(){
  var active=document.getElementById('he3-refine-active');
  var placeholder=document.getElementById('he3-refine-unavailable');
  if(!active||!placeholder)return;
  function check(){
    var current=String((document.getElementById('disp_period')||{}).textContent||'').trim();
    var reference=active.getAttribute('data-reference')||'';
    var deadline=Date.parse(active.getAttribute('data-deadline')||'');
    var ready=/^[0-9]{11}$/.test(current)&&current===reference&&Number.isFinite(deadline)&&Date.now()<deadline;
    active.hidden=!ready;
    placeholder.hidden=ready;
    placeholder.textContent=ready?'':'本项尚无当前期可用结果，或参考已过期；等待本期独立排除结果。';
  }
  check();
  window.addEventListener('load',check);
  window.addEventListener('pageshow',check);
  document.addEventListener('visibilitychange',check);
  setInterval(check,1000);
})();
</script>"""

SECTION_STYLE = ('box-sizing:border-box;max-width:1000px;margin:10px auto 16px;padding:12px 16px;'
                 'border:1px solid #c7d2fe;border-radius:8px;background:#f8faff;'
                 'font-family:sans-serif;color:#334155;font-size:13px;line-height:1.8;'
                 'overflow-wrap:anywhere;min-width:0;')


def card_fragment(reference, base_keep, drop, remaining, reason, generated_at):
    reference = _reference(reference)
    base_keep, remaining = _digits(base_keep, 6), _digits(remaining, 5)
    if type(drop) is int and 0 <= drop <= 9:
        drop = str(drop)
    if not isinstance(drop, str) or len(drop) != 1 or drop not in base_keep:
        raise ValueError('Extra exclusion must be one member of the original six')
    if set(remaining) != set(base_keep) - {drop}:
        raise ValueError('Remaining five do not match the original six minus the exclusion')
    generated = _generated(generated_at)
    deadline = period_slot(next_period(reference))
    if not period_slot(reference) <= generated < deadline:
        raise ValueError('Refinement generation time is outside its reference window')
    if not isinstance(reason, str):
        raise ValueError('Refinement reason must be text')
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('Refinement reason must be valid UTF-8') from None
    return (START + '\n<section aria-label="三数合尾额外排除参考" style="' + SECTION_STYLE + '">'
            '<b style="color:#3730a3;">三数合尾 · 原六码内额外排除1码</b>'
            '<div id="he3-refine-active" data-reference="' + escape(reference, quote=True)
            + '" data-deadline="' + escape(deadline.isoformat(), quote=True) + '" hidden>'
            '<div style="display:flex;flex-wrap:wrap;gap:6px 20px;">'
            '<span>原6码：<b data-he3-base="1">' + escape(base_keep) + '</b></span>'
            '<span>额外排除1码：<b data-he3-drop="1" style="color:#dc2626;">' + escape(drop) + '</b></span>'
            '<span>剩余5码：<b data-he3-remaining="1" style="color:#2563eb;">' + escape(remaining) + '</b></span>'
            '</div><div style="font-size:12px;color:#64748b;">参考期：' + escape(reference)
            + ' ｜ 目标期：' + escape(next_period(reference))
            + '<br>参考截止：' + escape(deadline.strftime('%Y-%m-%d %H:%M:%S'))
            + '（北京时间） ｜ 计算时间：' + escape(generated.strftime('%Y-%m-%d %H:%M:%S'))
            + '（北京时间）</div><div>参考理由：' + escape(reason) + '</div></div>'
            '<p id="he3-refine-unavailable" role="status" style="margin:4px 0;">'
            '等待核对当前参考期；未核对前不显示号码。</p>'
            '<div style="font-size:12px;color:#64748b;">'
            '本行仅为独立参考，不计入原8项统计及原攻击语；原六码和复制内容保持不变。'
            '</div></section>\n' + STALE_SCRIPT + '\n' + END)


def _placeholder(reference):
    label = reference if isinstance(reference, str) and parse_period(reference) else '--'
    return (START + '\n<section aria-label="三数合尾额外排除参考" style="' + SECTION_STYLE + '">'
            '<b style="color:#3730a3;">三数合尾 · 原六码内额外排除1码</b>'
            '<p id="he3-refine-unavailable" role="status" style="margin:4px 0;">'
            '当前参考期：' + escape(label) + '；等待本期独立排除结果，暂无可用号码。</p>'
            '<div style="font-size:12px;color:#64748b;">'
            '本行仅为独立参考，不计入原8项统计及原攻击语；原六码和复制内容保持不变。'
            '</div></section>\n' + END)


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _stats_summary(summary):
    raw = summary if isinstance(summary, dict) else {}
    error = raw.get('stats_error') is not False
    day = raw.get('biz_date')
    try:
        if not isinstance(day, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
            raise ValueError('Invalid business date')
        day = date.fromisoformat(day).isoformat()
    except (ValueError, TypeError):
        day = '--'
        error = True
    hits, total = _count(raw.get('hits')), _count(raw.get('total'))
    pending, conflicts = _count(raw.get('pending_count')), _count(raw.get('conflict_count'))
    if hits is None or total is None or hits > total or pending is None or conflicts is None:
        error = True
    latest = raw.get('latest_settled_period')
    latest = latest if isinstance(latest, str) and parse_period(latest) and latest == parse_period(latest)[0] else '--'
    return {'biz_date': day, 'stats_error': error,
            'hits': hits if not error else None, 'total': total if not error else None,
            'percent': round(100 * hits / total, 4) if not error and total else None,
            'pending_count': pending, 'conflict_count': conflicts,
            'latest_settled_period': latest}


FIVE_STATS_SCRIPT = """<script id="he3-five-stats-control">
(function(){
  var version='inline-row8-retained-v4';
  function businessDate(){
    var date=new Date(Date.now()+8*60*60*1000);
    if(date.getUTCHours()<5)date.setUTCDate(date.getUTCDate()-1);
    function pad(value){return String(value).padStart(2,'0');}
    return date.getUTCFullYear()+'-'+pad(date.getUTCMonth()+1)+'-'+pad(date.getUTCDate());
  }
  function period(value){
    if(typeof value!=='string'||!/^[0-9]{11}$/.test(value))return null;
    var year=Number(value.slice(0,4)),month=Number(value.slice(4,6)),day=Number(value.slice(6,8)),index=Number(value.slice(8));
    if(year<1||index<1||index>288)return null;
    var date=new Date(0);date.setUTCHours(0,0,0,0);date.setUTCFullYear(year,month-1,day);
    if(date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day)return null;
    var slot=date.getTime()+index*300000-8*60*60*1000,next;
    if(index<288)next=value.slice(0,8)+String(index+1).padStart(3,'0');
    else{date.setUTCDate(date.getUTCDate()+1);next=String(date.getUTCFullYear()).padStart(4,'0')+String(date.getUTCMonth()+1).padStart(2,'0')+String(date.getUTCDate()).padStart(2,'0')+'001';}
    return {slot:slot,next:next};
  }
  function retained(){
    var nodes=document.querySelectorAll('[id="he3-retained-data"]');if(nodes.length!==1)return null;
    var raw=String(nodes[0].textContent||'').trim(),data;
    try{data=JSON.parse(raw);}catch(error){return null;}
    if(!data||typeof data!=='object'||Array.isArray(data)||JSON.stringify(data)!==raw)return null;
    var keys=['version','ref_period','target_period','base_keep','drop','remaining','published_at'];
    if(Object.keys(data).length!==keys.length||!keys.every(function(key){return Object.prototype.hasOwnProperty.call(data,key);})||data.version!==1)return null;
    var reference=period(data.ref_period),target=period(data.target_period),published=Date.parse(data.published_at);
    if(!reference||!target||reference.next!==data.target_period||typeof data.base_keep!=='string'||!/^[0-9]{6}$/.test(data.base_keep)||new Set(data.base_keep).size!==6
      ||typeof data.drop!=='string'||!/^[0-9]$/.test(data.drop)||data.base_keep.indexOf(data.drop)===-1
      ||typeof data.remaining!=='string'||!/^[0-9]{5}$/.test(data.remaining)||new Set(data.remaining).size!==5||data.base_keep.split(data.drop).join('')!==data.remaining
      ||typeof data.published_at!=='string'||!/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]{1,6})?\\+08:00$/.test(data.published_at)
      ||!Number.isFinite(published)||published<reference.slot||published>=target.slot||!Number.isFinite(Date.now())||published>Date.now())return null;
    data.deadline=target.slot;return data;
  }
  function showNumbers(container,data,label,history){
    container.replaceChildren();
    function text(value){container.appendChild(document.createTextNode(value));}
    function number(attribute,value,color){var node=document.createElement('b');node.setAttribute(attribute,'1');node.textContent=value;node.style.color=color;return node;}
    if(history){
      var title=document.createElement('b');title.setAttribute('data-he3-retained-label','1');
      title.style.cssText='display:block;color:#92400e;';
      title.textContent=Date.now()>=data.deadline?'最近封存 · 历史参考（已过期）':'已封存记录 · 等待核对当前期';container.appendChild(title);
      var identity=document.createElement('span');identity.setAttribute('data-he3-retained-target','1');identity.style.display='block';
      identity.textContent='目标期：'+data.target_period+' ｜ 参考期：'+data.ref_period+' ｜ 封存时间：'+data.published_at.slice(0,19).replace('T',' ')+'（北京时间）';container.appendChild(identity);
      text('该次封存原6码：');container.appendChild(number('data-he3-inline-base',data.base_keep,'#475569'));text(' ｜ ');
    }
    text('额外排除1码：');container.appendChild(number('data-he3-inline-drop',data.drop,'#dc2626'));
    text(' ｜ 剩余5码：');container.appendChild(number('data-he3-inline-remaining',data.remaining,'#2563eb'));
    var odd=data.remaining.split('').filter(function(digit){return Number(digit)%2===1;}).length;
    var parity=document.createElement('span');parity.setAttribute('data-he3-inline-parity','1');parity.style.cssText='display:inline-block;margin-left:8px;color:#475569;';
    parity.textContent='（'+odd+'奇'+(5-odd)+'偶）';container.appendChild(parity);
    var baseOdd=data.base_keep.split('').filter(function(digit){return Number(digit)%2===1;}).length;
    if(baseOdd===1||baseOdd===5){
      var note=document.createElement('span');note.setAttribute('data-he3-inline-parity-note','1');note.style.cssText='display:block;color:#64748b;font-size:12px;';
      note.textContent='原6码为'+baseOdd+'奇'+(6-baseOdd)+'偶，仅排除1码无法组成2奇3偶或3奇2偶。';
      if(odd===(baseOdd===1?1:4))note.textContent+='本期已取最接近搭配。';container.appendChild(note);
    }
    if(history){var warning=document.createElement('span');warning.setAttribute('data-he3-retained-notice','1');warning.style.cssText='display:block;font-size:12px;color:#92400e;';
      warning.textContent='本行保留已核验的封存号码，不代表当前第8项的原6码；等待新一期独立结果。';container.appendChild(warning);}
    var rate=document.createElement('span');rate.setAttribute('data-he3-inline-rate','1');rate.style.cssText='display:block;color:#3730a3;';
    rate.textContent=(history?'今日5码汇总：':'剩余5码独立参考（不计入原8项）：')+label;rate.title='独立五码统计；原六码及原8项统计不变。';container.appendChild(rate);
  }
  function projectInline(label){
    var badges=document.querySelectorAll('[data-r1-hit-key="he3_keep"]');
    var previous=document.querySelectorAll('[data-he3-inline]');
    var sections=document.querySelectorAll('section[aria-label="三数合尾额外排除参考"]');
    var row=badges.length===1?badges[0].parentElement:null;
    var grid=row&&row.parentElement,rows=grid?Array.prototype.slice.call(grid.children):[];
    var validHost=row&&row.tagName==='DIV'&&rows.length===8&&rows[7]===row&&rows.every(function(item,index){
      var match=String(item.textContent||'').trim().match(/^([1-8])[.]/);
      return item.tagName==='DIV'&&match&&Number(match[1])===index+1;
    });
    var six=validHost?Array.prototype.filter.call(row.children,function(item){
      return item.tagName==='SPAN'&&!item.hasAttribute('data-r1-hit-key')&&/^[0-9]{6}$/.test(String(item.textContent||'').trim());
    }):[];
    var card=validHost?grid.parentElement:null;
    var heading=card&&card.firstElementChild;
    var match=heading&&String(heading.textContent||'').match(/参考期号[：:]\\s*([0-9]{11})/);
    validHost=validHost&&six.length===1&&match&&new Set(six[0].textContent.trim()).size===6;
    var inline=null;
    Array.prototype.forEach.call(previous,function(item){
      if(validHost&&item.parentElement===row&&!inline)inline=item;
      else item.remove();
    });
    // The full sealed source remains in the document for validation. Present
    // its concise numbers beside item 8, with the full source as a fallback.
    Array.prototype.forEach.call(sections,function(section){section.hidden=!!validHost;});
    var saved=retained(),fallbackNodes=document.querySelectorAll('[id="he3-retained-fallback"]');
    var fallback=fallbackNodes.length===1?fallbackNodes[0]:null;
    if(fallback){fallback.hidden=!!validHost||!saved;if(!fallback.hidden)showNumbers(fallback,saved,label,true);else fallback.replaceChildren();}
    if(!validHost)return;
    if(!inline){inline=document.createElement('div');inline.setAttribute('data-he3-inline','1');row.appendChild(inline);}
    inline.style.cssText='display:block;box-sizing:border-box;max-width:100%;margin-top:7px;padding:7px 9px;border:1px solid #c7d2fe;border-radius:6px;background:#f8faff;font-size:13px;line-height:1.8;white-space:normal;overflow-wrap:anywhere;';
    var activeNodes=document.querySelectorAll('[id="he3-refine-active"]');
    var active=activeNodes.length===1?activeNodes[0]:null;
    var current=String((document.getElementById('disp_period')||{}).textContent||'').trim();
    var reference=active?active.getAttribute('data-reference'):'';
    var deadline=active?Date.parse(active.getAttribute('data-deadline')||''):NaN;
    function value(selector){
      var found=active?active.querySelectorAll(selector):[];
      return found.length===1?String(found[0].textContent||'').trim():'';
    }
    var base=value('[data-he3-base]'),drop=value('[data-he3-drop]'),remaining=value('[data-he3-remaining]');
    var now=Date.now();
    var ref=period(reference),target=ref?period(ref.next):null;
    var ready=ref&&reference===current&&reference===match[1]
      &&Number.isFinite(now)&&target&&deadline===target.slot&&now<deadline
      &&base===six[0].textContent.trim()&&/^[0-9]$/.test(drop)&&base.indexOf(drop)!==-1
      &&/^[0-9]{5}$/.test(remaining)&&new Set(remaining).size===5&&base.split(drop).join('')===remaining;
    var savedNodes=document.querySelectorAll('[id="he3-retained-data"]');
    var emptySaved=savedNodes.length===1&&String(savedNodes[0].textContent||'').trim()==='null';
    if(savedNodes.length&&(savedNodes.length!==1||(!saved&&!emptySaved)))ready=false;
    // The current raw card may publish before the next derived-stat refresh.
    // An older retained record must not suppress a newly valid current card.
    if(saved&&saved.ref_period===reference&&(saved.base_keep!==base||saved.drop!==drop||saved.remaining!==remaining))ready=false;
    inline.replaceChildren();
    if(ready){showNumbers(inline,{base_keep:base,drop:drop,remaining:remaining},label,false);return;}
    if(saved){showNumbers(inline,saved,label,true);return;}
    inline.textContent='额外排除1码 / 剩余5码：暂无可核验封存，等待独立结果';
  }
  function check(){
    var source=document.getElementById('he3-five-stats-data'),data={stats_error:true};
    if(source){
      try{data=JSON.parse(source.textContent);}catch(error){data={stats_error:true};}
      if(!data||typeof data!=='object'||Array.isArray(data))data={stats_error:true};
    }
    var today=data.biz_date===businessDate();
    var valid=data.stats_error===false&&Number.isSafeInteger(data.hits)&&Number.isSafeInteger(data.total)&&data.total>0&&data.hits>=0&&data.hits<=data.total;
    var label=data.stats_error===true?'统计核验失败，暂停显示命中率':!today?'统计周期已切换，等待本日核验':valid?'命中率 '+(data.hits/data.total*100).toFixed(2)+'%（'+data.hits+'/'+data.total+'期）':'暂无已验证数据';
    var notice=document.getElementById('he3-five-stats-rate');
    if(notice)notice.textContent=label;
    projectInline(label);
    var activeNodes=document.querySelectorAll('[id="he3-refine-active"]');
    if(activeNodes.length!==1)return;
    var active=activeNodes[0],remaining=active.querySelectorAll('[data-he3-remaining]');
    var existing=active.querySelectorAll('[data-he3-five-hit]');
    var current=String((document.getElementById('disp_period')||{}).textContent||'').trim();
    var reference=active.getAttribute('data-reference')||'';
    var deadline=Date.parse(active.getAttribute('data-deadline')||'');
    var currentCard=/^[0-9]{11}$/.test(reference)&&reference===current&&Number.isFinite(deadline)&&Date.now()<deadline;
    if(!currentCard||remaining.length!==1){
      Array.prototype.forEach.call(existing,function(node){node.remove();});
      return;
    }
    var badge=existing.length?existing[0]:document.createElement('span');
    Array.prototype.forEach.call(existing,function(node,index){if(index)node.remove();});
    badge.setAttribute('data-he3-five-hit','1');
    badge.setAttribute('data-stats-business-date',data.biz_date);
    badge.style.cssText='display:inline-block;max-width:100%;margin-left:6px;padding:1px 6px;border-radius:4px;background:#e0e7ff;color:#3730a3;white-space:normal;overflow-wrap:anywhere;font-size:12px;';
    badge.textContent=label;
    badge.title='仅核验开奖前已封存的剩余5码；前4球任取3球的4个合尾，任一个落入当期5码即算命中。不计入原8项。';
    remaining[0].parentNode.appendChild(badge);
  }
  function attach(state){
    // document.open can retain the window cache while clearing DOM listeners.
    // Reusing the same listener function is deduplicated by addEventListener.
    window.addEventListener('load',state.check);
    window.addEventListener('pageshow',state.check);
    document.addEventListener('visibilitychange',state.check);
    if(state.timer!=null)clearInterval(state.timer);
    state.timer=setInterval(state.check,1000);
    state.check();
  }
  var prior=window.__PENTA5_HE3_FIVE_STATS__;
  if(prior&&typeof prior.check==='function'&&prior.version===version){attach(prior);return;}
  if(prior){
    if(prior.timer!=null)clearInterval(prior.timer);
    if(typeof prior.check==='function'){
      window.removeEventListener('load',prior.check);window.removeEventListener('pageshow',prior.check);
      document.removeEventListener('visibilitychange',prior.check);
    }
  }
  var state={check:check,timer:null,version:version};
  window.__PENTA5_HE3_FIVE_STATS__=state;
  attach(state);
})();
</script>"""


PAIRED_STATS_SCRIPT = """<script id="he3-paired-day-control">
(function(){
  var version='paired-day-v1';
  function businessDate(){
    var now=Date.now();if(!Number.isFinite(now))return '';
    var date=new Date(now+8*60*60*1000);
    if(date.getUTCHours()<5)date.setUTCDate(date.getUTCDate()-1);
    function pad(value){return String(value).padStart(2,'0');}
    return date.getUTCFullYear()+'-'+pad(date.getUTCMonth()+1)+'-'+pad(date.getUTCDate());
  }
  function check(){
    var roots=document.querySelectorAll('[id="he3-paired-comparison"]');
    Array.prototype.forEach.call(roots,function(root){
      var current=roots.length===1&&root.getAttribute('data-business-date')===businessDate();
      var daily=root.querySelector('[data-he3-paired-daily]');
      var notice=root.querySelector('[data-he3-paired-day-notice]');
      if(daily)daily.hidden=!current;
      if(notice){notice.hidden=!!current;notice.textContent=roots.length===1?
        '统计周期已切换，等待本日配对核验；下方滚动窗口仍为所标期号的历史快照。':
        '统计载入异常，暂停显示今日配对成绩。';}
    });
  }
  function attach(state){
    window.addEventListener('load',state.check);window.addEventListener('pageshow',state.check);
    document.addEventListener('visibilitychange',state.check);
    if(state.timer!=null)clearInterval(state.timer);
    state.timer=setInterval(state.check,1000);state.check();
  }
  var prior=window.__PENTA5_HE3_PAIRED_VIEW__;
  if(prior&&prior.version===version&&typeof prior.check==='function'){attach(prior);return;}
  if(prior){
    if(prior.timer!=null)clearInterval(prior.timer);
    if(typeof prior.check==='function'){
      window.removeEventListener('load',prior.check);window.removeEventListener('pageshow',prior.check);
      document.removeEventListener('visibilitychange',prior.check);
    }
  }
  var state={version:version,check:check,timer:null};window.__PENTA5_HE3_PAIRED_VIEW__=state;attach(state);
})();
</script>"""


def _paired_window(raw, maximum, anchor):
    if not isinstance(raw, dict):
        raise ValueError('Invalid paired window')
    names = ('planned_count', 'observed_count', 'total', 'hits6', 'hits5', 'lost_hits', 'published_count',
             'unpublished_count', 'pending_count', 'conflict_count', 'max_win_streak5',
             'max_loss_streak5', 'current_win_streak5', 'current_loss_streak5')
    result = {name: _count(raw.get(name)) for name in names}
    if any(value is None for value in result.values()):
        raise ValueError('Invalid paired count')
    n, total, six, five = (result[name] for name in ('planned_count', 'total', 'hits6', 'hits5'))
    if (not five <= six <= total <= result['published_count'] <= n <= maximum
            or result['observed_count'] > n
            or result['lost_hits'] != six - five
            or total + result['pending_count'] != result['published_count']
            or result['published_count'] + result['unpublished_count'] + result['conflict_count'] != n):
        raise ValueError('Inconsistent paired counts')
    for name in ('max_win_streak5', 'max_loss_streak5', 'current_win_streak5', 'current_loss_streak5'):
        if result[name] > total:
            raise ValueError('Invalid paired streak')
    if (result['current_win_streak5'] > result['max_win_streak5']
            or result['current_loss_streak5'] > result['max_loss_streak5']
            or result['current_win_streak5'] and result['current_loss_streak5']
            or type(raw.get('latest_verified')) is not bool):
        raise ValueError('Inconsistent paired streak')
    if not raw['latest_verified'] and (result['current_win_streak5'] or result['current_loss_streak5']):
        raise ValueError('Unverified latest slot cannot carry current streak')
    if raw['latest_verified'] and not total:
        raise ValueError('Verified latest slot requires a sample')
    first, last = raw.get('first_period'), raw.get('last_period')
    for value in (first, last):
        if value is not None and (not isinstance(value, str) or not parse_period(value) or parse_period(value)[0] != value):
            raise ValueError('Invalid paired period')
    if n and (first is None or last is None or first > last or anchor is None or last > anchor):
        raise ValueError('Invalid paired period range')
    if n and (period_slot(last) - period_slot(first)).total_seconds() != (n - 1) * 300:
        raise ValueError('Paired range does not match its continuous slots')
    if n and maximum in (50, 200) and (n != maximum or last != anchor):
        raise ValueError('Rolling window must end at its stated anchor')
    if not n and (first is not None or last is not None):
        raise ValueError('Empty paired window cannot have a range')
    versions = raw.get('versions')
    allowed = ('he3-refine-v1', 'he3-refine-v2-balanced', 'he3-refine-v3-panel-context')
    if not isinstance(versions, dict) or set(versions) - set(allowed):
        raise ValueError('Invalid paired versions')
    versions = {key: _count(versions.get(key, 0)) for key in allowed}
    if any(value is None for value in versions.values()) or sum(versions.values()) != total:
        raise ValueError('Inconsistent paired version samples')
    result.update({'first_period': first, 'last_period': last, 'latest_verified': raw['latest_verified'],
                   'versions': versions, 'percent6': six / total * 100 if total else None,
                   'percent5': five / total * 100 if total else None,
                   'loss_of_six_hits_percent': result['lost_hits'] / six * 100 if six else None})
    return result


def _paired_summary(summary, data):
    if data['stats_error'] or not isinstance(summary, dict):
        return None
    raw = summary.get('comparison')
    if (not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1
            or raw.get('revision') != 'he3-paired-v1' or raw.get('biz_date') != data['biz_date']):
        return None
    anchor = raw.get('anchor_period')
    if anchor is not None and (not isinstance(anchor, str) or not parse_period(anchor) or parse_period(anchor)[0] != anchor):
        return None
    try:
        windows = {name: _paired_window(raw.get(name), maximum, anchor)
                   for name, maximum in (('daily', 288), ('rolling50', 50), ('rolling200', 200))}
    except (ValueError, TypeError):
        return None
    return {'biz_date': data['biz_date'], 'anchor_period': anchor, **windows}


def _paired_window_html(window, title, daily=False):
    attributes = ' data-he3-paired-daily="1" hidden' if daily else ''
    content = ('<div' + attributes + ' style="flex:1 1 240px;min-width:0;max-width:100%;box-sizing:border-box;'
               'padding:9px 11px;border:1px solid #dbe3ef;border-radius:6px;background:white;">'
               '<b>' + escape(title) + '</b>')
    if window['planned_count']:
        content += '<div style="font-size:12px;color:#64748b;">' + escape(window['first_period']) + '—' + escape(window['last_period']) + '</div>'
    else:
        content += '<div>暂无已开奖窗口</div>'
    total = window['total']
    if total:
        content += ('<div>原6码命中：<b>' + f'{window["percent6"]:.2f}%（{window["hits6"]}/{total}期）' + '</b></div>'
                    '<div>剩余5码命中：<b>' + f'{window["percent5"]:.2f}%（{window["hits5"]}/{total}期）' + '</b></div>')
    else:
        content += '<div>原6码／剩余5码：暂无已核验样本</div>'
    content += '<div>删码导致失手：<b>' + str(window['lost_hits']) + '期</b>'
    if window['loss_of_six_hits_percent'] is not None:
        content += f'（占原6码命中 {window["loss_of_six_hits_percent"]:.2f}%）'
    content += '</div><div style="font-size:12px;color:#475569;">'
    content += ('已核验配对：' + str(total) + '期 ｜ 可核验发布：' + str(window['published_count']) + '/' + str(window['planned_count']) + '期'
                '<br>可见真实开奖：' + str(window['observed_count']) + '期'
                '<br>无有效封存：' + str(window['unpublished_count']) + '期 ｜ 待核验：' + str(window['pending_count'])
                + '期 ｜ 冲突：' + str(window['conflict_count']) + '期</div>')
    if total:
        content += ('<div>5码最高连中：' + str(window['max_win_streak5']) + '手 ｜ 最高连败：' + str(window['max_loss_streak5'])
                    + '手<br>当前连中：' + str(window['current_win_streak5']) + '手 ｜ 当前连败：' + str(window['current_loss_streak5']) + '手</div>')
    else:
        content += '<div>5码连续成绩：暂无已核验样本</div>'
    if window['planned_count'] and not window['latest_verified']:
        content += '<div style="font-size:12px;color:#64748b;">最新期未核验，连续段中断。</div>'
    content += ('<div style="font-size:12px;color:#64748b;">样本版本：旧规则 ' + str(window['versions']['he3-refine-v1'])
                + '期 ｜ 奇偶约束规则 ' + str(window['versions']['he3-refine-v2-balanced'])
                + '期 ｜ 面板上下文规则 ' + str(window['versions']['he3-refine-v3-panel-context']) + '期</div></div>')
    return content


def _paired_html(summary, data):
    comparison = _paired_summary(summary, data)
    intro = ('<details id="he3-paired-comparison" data-business-date="' + escape(data['biz_date'], quote=True)
             + '" style="margin-top:10px;padding-top:8px;border-top:1px solid #c7d2fe;">'
             '<summary style="cursor:pointer;color:#3730a3;"><b>原6码与5码同期开奖对照 · 稳定性统计</b></summary>')
    if comparison is None:
        return intro + '<p role="status">暂无已核验对照；等待配对统计刷新。</p></details>'
    return (intro + '<div style="font-size:12px;color:#64748b;margin:6px 0;">'
            '仅比较同一目标期、同一原6码与其删1后的5码；原6码命中而5码失手才计为删码损失。'
            '<br>窗口按连续目标期计算；无有效封存、待核验及冲突不计输赢，并中断连续段。'
            '近50／200期窗口不随每日05:00清零；样本可能包含所列历史版本。'
            '<br>滚动窗口截至已开奖期：' + escape(comparison['anchor_period'] or '--') + '</div>'
            '<p data-he3-paired-day-notice="1" role="status" style="color:#64748b;">等待核对今日统计日期。</p>'
            '<div style="display:flex;flex-wrap:wrap;gap:10px;">'
            + _paired_window_html(comparison['daily'], '今日05:00起（北京时间）', daily=True)
            + _paired_window_html(comparison['rolling50'], '近50个连续目标期')
            + _paired_window_html(comparison['rolling200'], '近200个连续目标期')
            + '</div></details>')


def _retained_summary(raw):
    """Validate only derived display metadata; the selector proves the seal."""
    if not isinstance(raw, dict) or set(raw) != {
            'version', 'ref_period', 'target_period', 'base_keep', 'drop', 'remaining', 'published_at'}:
        return None
    try:
        if type(raw['version']) is not int or raw['version'] != 1:
            return None
        reference = _reference(raw['ref_period'])
        target = _reference(raw['target_period'])
        if target != next_period(reference):
            return None
        base, remaining = _digits(raw['base_keep'], 6), _digits(raw['remaining'], 5)
        drop = str(raw['drop']) if type(raw['drop']) is int and 0 <= raw['drop'] <= 9 else raw['drop']
        if not isinstance(drop, str) or len(drop) != 1 or drop not in base or base.replace(drop, '') != remaining:
            return None
        published = _generated(raw['published_at'])
        if not period_slot(reference) <= published < period_slot(target):
            return None
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    return {'version': 1, 'ref_period': reference, 'target_period': target,
            'base_keep': base, 'drop': drop, 'remaining': remaining, 'published_at': published.isoformat()}


def _retained_from_seal(seal):
    if not isinstance(seal, dict):
        return None
    response = seal.get('response')
    response = response if isinstance(response, dict) else {}
    return _retained_summary({'version': 1, 'ref_period': seal.get('ref_period'),
        'target_period': seal.get('target_period'), 'base_keep': seal.get('original_he3_keep'),
        'drop': response.get('he3_drop'), 'remaining': response.get('he3_keep5'),
        'published_at': seal.get('published_at')})


def _latest_retained(root, now=None):
    """Read the newest independently valid publication without generating one."""
    from ai_prediction_guard import read_stats
    from he3_refine import _validate_saved
    from stats_history import beijing_now

    root = Path(root)
    now = now if isinstance(now, datetime) else beijing_now()
    try:
        ledger = read_stats(root / 'data/he3_refine_seals.json')
        seals = ledger.get('prediction_seals', {})
        if not isinstance(seals, dict):
            return None
        candidates = [reference for reference, seal in seals.items()
                      if isinstance(reference, str) and parse_period(reference)
                      and parse_period(reference)[0] == reference and isinstance(seal, dict)
                      and seal.get('publication_state') == 'published']
        if not candidates:
            return None
        # One durable parent snapshot for this selection; no per-candidate I/O.
        parents = read_stats(root / 'data/r1_framework_seals.json')
        for reference in sorted(candidates, reverse=True)[:50]:
            try:
                checked = _validate_saved(seals[reference], reference, now, root=root,
                                          published=True, parent_seals=parents)
                retained = _retained_from_seal(checked)
                if retained is not None:
                    return retained
            except (ValueError, TypeError, KeyError, OSError, OverflowError):
                continue
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        return None
    return None


def render_he3_stats_block(summary, *, retained=None):
    data = _stats_summary(summary)
    if data['stats_error']:
        rate = '统计核验失败，暂停显示命中率'
    elif data['total']:
        rate = f'命中率 {100 * data["hits"] / data["total"]:.2f}%（{data["hits"]}/{data["total"]}期）'
    else:
        rate = '暂无已验证数据'
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        encoded = encoded.replace(old, new)
    saved = json.dumps(_retained_summary(retained), ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        saved = saved.replace(old, new)
    pending = str(data['pending_count']) if data['pending_count'] is not None else '—'
    conflicts = str(data['conflict_count']) if data['conflict_count'] is not None else '—'
    return (STATS_START + '\n<section id="he3-five-stats-notice" aria-label="剩余五码今日命中统计" style="'
            + SECTION_STYLE + '"><b>剩余5码 · 今日已验证战绩</b> ｜ '
            + escape(data['biz_date']) + ' 05:00起（北京时间）'
            '<br><b id="he3-five-stats-rate" style="color:#3730a3;">' + escape(rate) + '</b>'
            '<br>只使用前4球，任取3球形成4个合尾，任一合尾落入当期剩余5码即命中。'
            '按实际开奖时间归日；每日北京时间05:00重新统计，历史证据保留。'
            '<br>最近核验期：' + escape(data['latest_settled_period'])
            + ' ｜ 待核验：' + pending + '期 ｜ 冲突：' + conflicts + '期'
            '<br>仅计入开奖前已封存、开奖后已核验的独立5码结果；不计入原8项及原攻击语。'
            '<div id="he3-retained-fallback" hidden style="margin:8px 0;padding:8px;border:1px solid #fcd34d;border-radius:6px;background:#fffbeb;"></div>'
            + _paired_html(summary, data)
            + '</section>\n<script type="application/json" id="he3-five-stats-data">'
            + encoded + '</script>\n<script type="application/json" id="he3-retained-data">'
            + saved + '</script>\n' + FIVE_STATS_SCRIPT + '\n' + PAIRED_STATS_SCRIPT + '\n' + STATS_END)


def _inspect(html):
    if not isinstance(html, str):
        raise TypeError('Panel HTML must be text')
    found = {}
    for marker in (*PROTECTED, MARKER, STATS_MARKER):
        start, end = '<!-- ' + marker + '_START -->', '<!-- ' + marker + '_END -->'
        counts = html.count(start), html.count(end)
        if counts == (0, 0):
            found[marker] = None
            continue
        if counts != (1, 1):
            raise ValueError('Missing or duplicated panel markers')
        left, right = html.index(start), html.index(end) + len(end)
        if left >= right - len(end):
            raise ValueError('Reversed panel markers')
        found[marker] = (left, right, html[left:right])
    intervals = sorted((value[0], value[1]) for value in found.values() if value is not None)
    if any(left < previous_right for (_, previous_right), (left, _) in zip(intervals, intervals[1:])):
        raise ValueError('Overlapping protected or refinement panel markers')
    return found


def _protected(found):
    return {marker: found[marker][2] if found[marker] is not None else None for marker in PROTECTED}


def _relocate(html, found):
    own, anchor = found[MARKER], found[ANCHOR]
    if own is None:
        raise ValueError('Refinement transform did not produce its owned block')
    if anchor is None or found['R1_FRAMEWORK'] is None:
        raise ValueError('Framework card and statistics anchor are required')
    if own[0] >= anchor[1] and not html[anchor[1]:own[0]].strip():
        return html
    cleaned = html[:own[0]] + html[own[1]:]
    position = _inspect(cleaned)[ANCHOR][1]
    suffix = cleaned[position:]
    separator = '' if suffix.startswith('\n') else '\n'
    return cleaned[:position] + '\n' + own[2] + separator + suffix


def _stats_bytes(found):
    stats = found[STATS_MARKER]
    return stats[2] if stats is not None else None


def _relocate_stats(html, found):
    stats, own = found[STATS_MARKER], found[MARKER]
    if stats is None:
        return html
    if own is None:
        raise ValueError('Refinement statistics require their source reference card')
    if stats[0] >= own[1] and not html[own[1]:stats[0]].strip():
        return html
    cleaned = html[:stats[0]] + html[stats[1]:]
    position = _inspect(cleaned)[MARKER][1]
    suffix = cleaned[position:]
    separator = '' if suffix.startswith('\n') else '\n'
    return cleaned[:position] + '\n' + stats[2] + separator + suffix


def compose_refine_transform(html, transform):
    """Wrap generic publication while protecting every original card/stat block."""
    before = _inspect(html)
    if not callable(transform):
        raise TypeError('Refinement transform must be callable')
    transformed = transform(html)
    checked = _inspect(transformed)
    if _protected(before) != _protected(checked):
        raise ValueError('Refinement publication changed a protected panel block')
    if _stats_bytes(before) != _stats_bytes(checked):
        raise ValueError('Refinement publication changed its derived statistics block')
    updated = _relocate(transformed, checked)
    updated = _relocate_stats(updated, _inspect(updated))
    after = _inspect(updated)
    if _protected(before) != _protected(after):
        raise ValueError('Refinement placement changed a protected panel block')
    if _stats_bytes(before) != _stats_bytes(after):
        raise ValueError('Refinement placement changed its derived statistics block')
    return updated


def apply_refine_block(html, fragment):
    """Replace only our complete fragment, then locate it after framework stats."""
    if not isinstance(fragment, str):
        raise TypeError('Refinement fragment must be text')
    fragment = fragment.strip()
    block = _inspect(fragment)[MARKER]
    if block is None or block[0] != 0 or block[1] != len(fragment):
        raise ValueError('Refinement fragment must contain only its complete owned block')

    def transform(content):
        existing = _inspect(content)[MARKER]
        if existing is not None:
            return content[:existing[0]] + fragment + content[existing[1]:]
        if content.count('</body>') != 1:
            raise ValueError('Panel body closing marker is missing or ambiguous')
        return content.replace('</body>', fragment + '\n</body>', 1)

    return compose_refine_transform(html, transform)


def apply_refine_stats_block(html, fragment):
    """Update derived statistics while preserving all eight blocks and the seal."""
    if not isinstance(fragment, str):
        raise TypeError('Refinement statistics fragment must be text')
    fragment = fragment.strip()
    checked_fragment = _inspect(fragment)[STATS_MARKER]
    if checked_fragment is None or checked_fragment[0] != 0 or checked_fragment[1] != len(fragment):
        raise ValueError('Statistics fragment must contain only its complete owned block')
    before = _inspect(html)
    if before[MARKER] is None:
        raise ValueError('Refinement statistics require their source reference card')
    stats = before[STATS_MARKER]
    if stats is None:
        if html.count('</body>') != 1:
            raise ValueError('Panel body closing marker is missing or ambiguous')
        updated = html.replace('</body>', fragment + '\n</body>', 1)
    else:
        updated = html[:stats[0]] + fragment + html[stats[1]:]
    updated = _relocate_stats(updated, _inspect(updated))
    after = _inspect(updated)
    if _protected(before) != _protected(after) or before[MARKER][2] != after[MARKER][2]:
        raise ValueError('Refinement statistics changed a protected card or statistics block')
    return updated


def refresh_he3_refine_panel(snapshot=None, root=ROOT):
    # Delay core imports: the core can use card_fragment without an import cycle.
    from he3_refine import select_current
    from panel_io import update_panel

    root = Path(root)
    if snapshot is None:
        snapshot = load_snapshot(state_path=root / 'data/state.json',
                                 cache_path=root / 'data/stats_draw_history.json', persist=False)
    try:
        from he3_refine_stats import refresh_he3_stats
        summary = refresh_he3_stats(snapshot, root)
    except Exception:
        # Never turn a statistics failure into a fabricated zero rate or block
        # an otherwise independently validated reference card.
        summary = {'stats_error': True}
    result = {}

    def transform(html):
        # Statistics deliberately use the caller's frozen draw snapshot. Display
        # selection must instead see publications that completed during that
        # calculation or while this writer waited for the shared panel lock.
        live = load_snapshot(state_path=root / 'data/state.json',
                             cache_path=root / 'data/stats_draw_history.json', persist=False)
        reference = str((live.get('latest') or {}).get('period') or '')
        seal = select_current(root, live)
        available = (isinstance(seal, dict) and seal.get('ref_period') == reference
                     and seal.get('publication_state') == 'published')
        result.update(reference=reference, available=available)
        # A publisher commits its exact card under this same lock before
        # acknowledging published in the seal ledger. Preserve that current
        # card byte-for-byte in this short window; never synthesize its
        # publication metadata from rendered digits or replace it with a
        # placeholder/older result. A later refresh verifies the real ack.
        existing = _inspect(html)[MARKER]
        matches = re.findall(r'id="he3-refine-active" data-reference="([0-9]{11})"'
                             r' data-deadline="([^"]+)"', existing[2] if existing else '')
        if len(matches) == 1:
            old_reference, old_deadline = matches[0]
            try:
                moment = _generated(live['now'])
                deadline = period_slot(next_period(_reference(old_reference)))
                current = (period_slot(old_reference) <= moment < deadline
                           and _generated(old_deadline) == deadline)
            except (ValueError, TypeError, KeyError, OverflowError):
                current = False
            if current and (not available or period_slot(old_reference) > period_slot(reference)):
                result['available'] = False
                result['preserved_current_panel'] = True
                return html
        fragment = seal['card_html'] if available else _placeholder(reference)
        retained = _retained_from_seal(seal) if available else _latest_retained(root, live.get('now'))
        stats_fragment = render_he3_stats_block(summary, retained=retained)
        return apply_refine_stats_block(apply_refine_block(html, fragment), stats_fragment)

    update_panel(transform, path=root / 'panel.html')
    return result
