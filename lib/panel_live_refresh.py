"""Derived, same-origin panel refresh. Never changes sealed source fragments."""
import re
import json
from framework_hit_view import SCRIPT as FRAMEWORK_RATE_SCRIPT
from killer_hit_view import SCRIPT as KILLER_RATE_SCRIPT
from he3_refine_view import STALE_SCRIPT as HE3_AVAILABILITY_SCRIPT
from attack_editor_view import install_attack_editor


REFRESH_SCRIPT = r'''<script id="penta5-refresh-control">
(function(){
  'use strict';
  var version='quiet-panel-v30-add-kill12',old=window.__PENTA5_LIVE_PANEL__;
  if(old&&old.version===version){window.fetchState=old.refresh;return;}
  if(old&&old.timer)clearInterval(old.timer);
  if(old&&old.availabilityTimer)clearInterval(old.availabilityTimer);
  var he3AvailabilitySource=__LOCAL_HE3_AVAILABILITY_SOURCE__;
  var names=['R1_KILLER','R1_KILLER_STATS','R1_FRAMEWORK','R1_FRAMEWORK_STATS','HE3_REFINE','HE3_REFINE_STATS','STAT50_COMPARE_STATS','PENTA5_STATS_PANEL','COLD_OBSERVATION','AI_API_CONTROL','RUNTIME_OBSERVABILITY','AI_JOB_STATUS','R1_PRO_COMPARE','STAT50_PREDICTION','STAT50_POSITION_STATS','SELECTED_FRAMEWORK','SELECTED_FRAMEWORK_STATS','SELECTED_FRAMEWORK_ATTACK','KILL_THREE','KILL_ONE','KILL_TEN','KILL_ELEVEN','KILL_TWELVE','COLD_HIT_STATS','POSITION_COLD','REAL_HIT','STRATEGY_BOARD_DERIVED','TAIL_STABILITY_SHADOW','THREE_AVOID2','MODEL_CALL_REC','POSITION_KILL_SHADOW','TAIL_HOLD_MAX_SHADOW','MODEL_USAGE','JOINT_FORM_SHADOW','HITRATE_BASE','HITRATE_STABILITY','HITRATE_ATTRIBUTION'];
  var headers=['disp_period','disp_numbers','disp_hewei','disp_time'],cache={},busy=false,started=false;
  function unique(doc,id){var nodes=doc.querySelectorAll('[id="'+id+'"]');return nodes.length===1?nodes[0]:null;}
  function ranges(doc){
    var found={},walk=doc.createTreeWalker(doc.body,128),node;
    while((node=walk.nextNode())){var m=/^\s*([A-Z0-9_]+)_(START|END)\s*$/.exec(node.data);if(m&&names.indexOf(m[1])>=0){var k=m[1]+'_'+m[2];if(found[k])throw Error('duplicate-marker');found[k]=node;}}
    var result={};names.forEach(function(name){var a=found[name+'_START'],b=found[name+'_END'];if(!a&&!b)return;if(!a||!b||a.parentNode!==b.parentNode)throw Error('unbalanced-marker');var nodes=[],n=a.nextSibling;while(n&&n!==b){nodes.push(n);n=n.nextSibling;}if(n!==b)throw Error('reversed-marker');result[name]={start:a,end:b,nodes:nodes};});return result;
  }
  function html(range){return range.nodes.map(function(n){return n.nodeType===1?n.outerHTML:n.nodeType===8?'<!--'+n.data+'-->':n.textContent;}).join('');}
  function executable(node){return node.nodeType===1&&node.tagName==='SCRIPT'&&node.getAttribute('type')!=='application/json';}
  function scriptMap(doc){
    var result={},anonymous=0,availabilityCount=0,hooks={'r1-killer-hit-script':'__PENTA5_REFRESH_R1_KILLER__','r1-framework-hit-script':'__PENTA5_REFRESH_R1_FRAMEWORK__'};
    doc.querySelectorAll('script').forEach(function(s){if(!executable(s))return;var k=s.id?'id:'+s.id:'anonymous:'+anonymous++,text=s.textContent,hook=hooks[s.id];if(result[k]!=null)throw Error('duplicate-script');
      // This local canonical card includes its availability script only when
      // a result exists. Accept that one exact known script's normal presence
      // transition, while never executing fetched JavaScript.
      if(s.id==='he3-refine-availability-control'){
        if(++availabilityCount>1)throw Error('duplicate-script');
        if(text.trim()!==he3AvailabilitySource.trim())throw Error('script-version');
        return;
      }
      // A stats writer can briefly publish its original static IIFE before the
      // installer runs. This wrapper only registers that same local code.
      if(hook){var prefix='\nwindow.'+hook+'=function(){\n',suffix='\n};\nwindow.'+hook+'();\n';if(text.startsWith(prefix)&&text.endsWith(suffix))text=text.slice(prefix.length,-suffix.length);}
      result[k]=text;
    });return result;
  }
  function derived(node){return node.nodeType===1&&(['1','true'].indexOf(node.getAttribute('data-penta5-live-derived'))>=0||node.matches('[data-r1-framework-overall],[data-r1-current-streak-interrupted],[data-r1-killer-hit-key],[data-r1-hit-key],[data-r1-framework-hit-key],[data-ai-target-period]'));}
  var keys=['data-selected6-row','data-selected6-number','data-selected6-rate-key'];
  function key(node){if(node.nodeType!==1)return '';if(node.id)return 'id:'+node.id;for(var i=0;i<keys.length;i++){var v=node.getAttribute(keys[i]);if(v!=null)return keys[i]+':'+v;}return '';}
  function compatible(a,b){return a&&a.nodeType===b.nodeType&&(a.nodeType!==1||(a.tagName===b.tagName&&key(a)===key(b)));}
  function morph(node,fresh){
    if(node.nodeType===3||node.nodeType===8){if(node.data!==fresh.data)node.data=fresh.data;return;}
    if(node.nodeType!==1||executable(node))return;
    var isDetails=node.tagName==='DETAILS',open=node.open,isControl=/^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName),focused=node===document.activeElement;
    var privateAiControl=node.tagName==='INPUT'&&['ai-control-user','ai-control-password'].indexOf(node.id)>=0,privateValue=privateAiControl?node.value:null;
    Array.from(node.attributes).forEach(function(a){if(!fresh.hasAttribute(a.name)&&!(isDetails&&a.name==='open'))node.removeAttribute(a.name);});
    Array.from(fresh.attributes).forEach(function(a){if(isDetails&&a.name==='open')return;if(node.getAttribute(a.name)!==a.value)node.setAttribute(a.name,a.value);});
    if(node.tagName==='TEXTAREA'){
      if(node.defaultValue!==fresh.defaultValue)node.defaultValue=fresh.defaultValue;
      // New sealed text must replace an old attack atomically, even while selected.
      if(node.value!==fresh.value)node.value=fresh.value;
    }else if(node.tagName==='INPUT'){if(privateAiControl){if(node.value!==privateValue)node.value=privateValue;}else if(!focused&&node.value!==fresh.value)node.value=fresh.value;}
    else children(node,Array.from(fresh.childNodes),null,null);
    if(isDetails)node.open=open;
    if(isControl&&node.tagName==='SELECT'&&!focused)node.value=fresh.value;
  }
  function children(parent,freshNodes,start,end){
    var cursor=start?start.nextSibling:parent.firstChild;
    freshNodes.forEach(function(fresh){
      while(cursor&&cursor!==end&&derived(cursor)&&key(cursor)!==key(fresh))cursor=cursor.nextSibling;
      var match=compatible(cursor,fresh)?cursor:null;
      if(!match&&key(fresh)){for(var n=cursor;n&&n!==end;n=n.nextSibling){if(compatible(n,fresh)){match=n;break;}}}
      // A derived summary is intentionally moved from its server stats block
      // into the visible card by its controller. Reuse it instead of cloning.
      if(!match&&fresh.nodeType===1&&fresh.id){var global=unique(document,fresh.id);if(global&&derived(global)&&compatible(global,fresh))match=global;}
      if(!match){if(executable(fresh))return;match=document.importNode(fresh,true);parent.insertBefore(match,cursor||end);}
      else {if(match!==cursor)parent.insertBefore(match,cursor||end);morph(match,fresh);}
      cursor=match.nextSibling;
    });
    while(cursor&&cursor!==end){var next=cursor.nextSibling;if(!derived(cursor))parent.removeChild(cursor);cursor=next;}
  }
  function anchor(){
    var x=Math.min(innerWidth-1,Math.max(1,innerWidth/2)),nodes=document.elementsFromPoint(x,Math.min(innerHeight-1,80));
    for(var i=0;i<nodes.length;i++){var node=nodes[i];if(node!==document.body&&node!==document.documentElement&&node.getBoundingClientRect().height<innerHeight*2)return {node:node,top:node.getBoundingClientRect().top};}return null;
  }
  function controls(){
    checkHe3Availability();
    ['__PENTA5_REFRESH_R1_KILLER__','__PENTA5_REFRESH_R1_FRAMEWORK__','__PENTA5_CHECK_STATS_DAY__'].forEach(function(k){if(typeof window[k]==='function')window[k]();});
    ['__PENTA5_AI_COPY_CONTROL__','__PENTA5_AI_TARGET_PERIOD__','__PENTA5_HE3_FIVE_STATS__','__PENTA5_HE3_PAIRED_VIEW__','__PENTA5_PRO_FOUR__','__PENTA5_STAT50_POSITION_STATS__','__PENTA5_SELECTED_ATTACK__','__PENTA5_AI_API_CONTROL__','__PENTA5_ATTACK_EDITOR__','__PENTA5_RUNTIME_OBSERVABILITY__'].forEach(function(k){if(window[k]&&typeof window[k].check==='function')window[k].check();});
    document.dispatchEvent(new CustomEvent('penta5:panel-updated',{bubbles:true}));
  }
  function checkHe3Availability(){
    var active=unique(document,'he3-refine-active'),placeholder=unique(document,'he3-refine-unavailable');
    if(!placeholder)return;
    if(!active){if(placeholder.hidden)placeholder.hidden=false;return;}
    var current=String((unique(document,'disp_period')||{}).textContent||'').trim(),deadline=Date.parse(active.getAttribute('data-deadline')||''),now=Date.now();
    var ready=/^[0-9]{11}$/.test(current)&&current===active.getAttribute('data-reference')&&Number.isFinite(now)&&Number.isFinite(deadline)&&now<deadline;
    if(active.hidden!==!ready)active.hidden=!ready;
    if(placeholder.hidden!==ready)placeholder.hidden=ready;
    var message=ready?'':'本项尚无当前期可用结果，或参考已过期；等待本期独立排除结果。';
    if(placeholder.textContent!==message)placeholder.textContent=message;
    // The old availability script is part of the immutable seal. A separate
    // derived notice states the earlier platform close without changing it.
    var note=unique(document,'he3-platform-close-notice');
    if(!note){note=document.createElement('div');note.id='he3-platform-close-notice';note.setAttribute('data-penta5-live-derived','1');note.style.cssText='font-size:12px;line-height:1.7;min-height:1.7em;color:#92400e;';active.insertAdjacentElement('beforebegin',note);}
    var text=ready&&now>=deadline-70000?'已关盘，仅供核对；原封存号码保留。':ready?'平台在开奖前70秒关盘。':'历史参考，仅供核对；等待本期独立结果。';
    if(note.textContent!==text)note.textContent=text;
  }
  function checkAvailability(){
    checkHe3Availability();
    // Snapshot age must advance even when no new response or event arrives.
    var runtime=window.__PENTA5_RUNTIME_OBSERVABILITY__;
    if(runtime&&typeof runtime.check==='function'){try{runtime.check();}catch(_){}}
  }
  function received(){
    var raw=unique(document,'runtime-observability-data'),data=null;
    try{data=raw?JSON.parse(raw.textContent):null;}catch(error){return;}
    if(!data||data.version!==1||!data.models||typeof data.models!=='object')return;
    var selectors={killer:'#ai_job_status .ai-job-row[data-ai-kind="killer"]',framework:'#ai_job_status .ai-job-row[data-ai-kind="framework"]',selected7:'#selected6-card',he3_refine:'#he3-refine-active'};
    var attributes={killer:'data-ai-ref',framework:'data-ai-ref',selected7:'data-selected6-reference',he3_refine:'data-reference'};
    var targets={killer:'data-ai-target',framework:'data-ai-target',selected7:'data-selected6-target'};
    var current=String((unique(document,'disp_period')||{}).textContent||'').trim(),models={};
    function next(reference){
      if(!/^\d{11}$/.test(reference))return '';
      var serial=Number(reference.slice(8)),date=new Date(Date.UTC(Number(reference.slice(0,4)),Number(reference.slice(4,6))-1,Number(reference.slice(6,8))));
      if(serial<1||serial>288||!Number.isFinite(date.getTime())||date.toISOString().slice(0,10).replace(/-/g,'')!==reference.slice(0,8))return '';
      if(serial<288)return reference.slice(0,8)+String(serial+1).padStart(3,'0');
      date.setUTCDate(date.getUTCDate()+1);return date.toISOString().slice(0,10).replace(/-/g,'')+'001';
    }
    var expected=next(current);
    Object.keys(selectors).forEach(function(kind){
      var nodes=document.querySelectorAll(selectors[kind]),row=data.models[kind],event=row&&row.availability&&row.availability.event_id;
      if(nodes.length!==1||!row||!expected||row.reference_period!==current||nodes[0].getAttribute(attributes[kind])!==current||row.target_period!==expected||(targets[kind]&&nodes[0].getAttribute(targets[kind])!==expected)||!/^[a-f0-9]{64}$/.test(event||''))return;
      models[kind]={reference_period:row.reference_period,target_period:row.target_period,event_id:event};
    });
    var now=Date.now();if(!Number.isFinite(now))return;
    window.__PENTA5_CLIENT_DELIVERY__={version:1,received_at:new Date(now).toISOString(),models:models};
    document.dispatchEvent(new CustomEvent('penta5:panel-received',{detail:window.__PENTA5_CLIENT_DELIVERY__}));
  }
  function notice(text){
    var n=document.getElementById('penta5-live-notice');if(!n){if(!text)return;n=document.createElement('div');n.id='penta5-live-notice';n.setAttribute('role','status');n.style.cssText='position:fixed;right:10px;bottom:10px;max-width:90vw;padding:5px 8px;background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;border-radius:5px;font-size:12px;z-index:9999;pointer-events:none;';document.body.appendChild(n);}if(n.hidden!==!text)n.hidden=!text;if(n.textContent!==text)n.textContent=text;
  }
  function apply(source){
    var doc=new DOMParser().parseFromString(source,'text/html'),nowRanges=ranges(document),nextRanges=ranges(doc);
    if(!unique(doc,'disp_period')||!unique(doc,'disp_numbers')||!Object.keys(nextRanges).length)throw Error('invalid-panel');
    var current=String((unique(document,'disp_period')||{}).textContent||'').trim(),incoming=unique(doc,'disp_period').textContent.trim();
    if(!/^\d{11}$/.test(incoming)||(/^\d{11}$/.test(current)&&incoming<current))throw Error('older-panel');
    Object.keys(nowRanges).forEach(function(name){if(!nextRanges[name])throw Error('missing-section');});
    var scripts=scriptMap(document),freshScripts=scriptMap(doc);
    Array.from(new Set(Object.keys(scripts).concat(Object.keys(freshScripts)))).forEach(function(id){if(id!=='id:penta5-refresh-control'&&scripts[id]!==freshScripts[id])throw Error('script-version');});
    // Plan every section first. Invalid JSON or incomplete markup applies nothing.
    var plan=[];Object.keys(nextRanges).forEach(function(name){if(!nowRanges[name])throw Error('new-section');var raw=html(nextRanges[name]);if(cache[name]!==raw)plan.push({name:name,current:nowRanges[name],fresh:nextRanges[name],raw:raw});});
    doc.querySelectorAll('script[type="application/json"]').forEach(function(n){JSON.parse(n.textContent);});
    var a=anchor(),oldY=scrollY,active=document.activeElement,selection=null;
    if(active&&typeof active.selectionStart==='number')selection={start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection};
    var scrollers=Array.from(document.querySelectorAll('textarea,[style*="overflow"],details')).map(function(n){return {node:n,x:n.scrollLeft,y:n.scrollTop,open:n.open};});
    // One synchronous transaction: no controller/timer can see mixed card/JSON data.
    if(plan.some(function(p){return p.name==='HE3_REFINE';})){
      var priorActive=unique(document,'he3-refine-active'),priorPlaceholder=unique(document,'he3-refine-unavailable'),nextActive=unique(doc,'he3-refine-active');
      // The original sealed card's local script captures its initial nodes.
      // Detach that placeholder before retiring its active card, so the old
      // timer cannot reveal a warning on a subsequent, unrelated live card.
      if(priorActive&&priorPlaceholder&&(!nextActive||priorActive.getAttribute('data-reference')!==nextActive.getAttribute('data-reference'))){
        priorPlaceholder.replaceWith(priorPlaceholder.cloneNode(true));
      }
    }
    headers.forEach(function(id){var to=unique(document,id),from=unique(doc,id);if(to&&from)morph(to,from);});
    plan.forEach(function(p){children(p.current.start.parentNode,p.fresh.nodes,p.current.start,p.current.end);cache[p.name]=p.raw;});
    // Editable displays reset only after the whole response passes validation.
    // An unchanged successful response also restores the latest generated text.
    var editor=window.__PENTA5_ATTACK_EDITOR__,resetEditors=!!editor&&typeof editor.reset==='function';
    if(resetEditors)editor.reset(doc);
    if(plan.length||incoming!==current||resetEditors)controls();
    received();
    scrollers.forEach(function(s){if(s.node.isConnected){s.node.scrollLeft=s.x;s.node.scrollTop=s.y;if(s.node.tagName==='DETAILS')s.node.open=s.open;}});
    if(active&&active.isConnected&&active!==document.body){if(document.activeElement!==active)active.focus({preventScroll:true});if(selection&&typeof active.setSelectionRange==='function'){try{active.setSelectionRange(selection.start,selection.end,selection.direction);}catch(_){}}}
    var delta=a&&a.node.isConnected?a.node.getBoundingClientRect().top-a.top:0;if(Math.abs(delta)>0.5)scrollTo({top:oldY+delta,left:scrollX,behavior:'instant'});
    notice('');return plan.length;
  }
  async function refresh(){
    if(busy)return false;busy=true;var abort=new AbortController(),timer=setTimeout(function(){abort.abort();},12000);
    try{var url=new URL(location.href);url.hash='';url.searchParams.set('_panel_refresh',String(Date.now()));var response=await fetch(url.href,{credentials:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:'text/html'},signal:abort.signal});if(!response.ok||response.status!==200)throw Error('http');if(response.url&&new URL(response.url).origin!==location.origin)throw Error('origin');var type=response.headers.get('content-type')||'';if(type&&!/text\/html/i.test(type))throw Error('content-type');var text=await response.text();apply(text);return true;}
    catch(error){checkAvailability();notice(error&&error.message==='script-version'?'页面版本已更新，请方便时手动刷新一次。':'数据更新暂未完成，保留当前号码，正在自动重试。');return false;}
    finally{clearTimeout(timer);busy=false;}
  }
  /* LOCAL_RATE_HOOKS */
  function start(){if(started)return;started=true;document.querySelectorAll('meta[http-equiv]').forEach(function(m){if(String(m.httpEquiv).toLowerCase()==='refresh')m.remove();});try{var r=ranges(document);Object.keys(r).forEach(function(k){cache[k]=html(r[k]);});}catch(_){}window.__PENTA5_LIVE_PANEL__.timer=setInterval(function(){if(!document.hidden)refresh();},8000);window.__PENTA5_LIVE_PANEL__.availabilityTimer=setInterval(checkAvailability,1000);checkAvailability();}
  window.__PENTA5_LIVE_PANEL__={version:version,refresh:refresh,apply:apply,timer:null,availabilityTimer:null};window.fetchState=refresh;
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
  document.addEventListener('visibilitychange',function(){if(!document.hidden)refresh();});window.addEventListener('pageshow',function(e){if(e.persisted)refresh();});
})();
</script>'''


def _local_rate_hook(script, hook):
    """Embed existing local projector code; fetched executable scripts stay inert."""
    code = re.fullmatch(r'<script[^>]*>([\s\S]*)</script>', script.strip()).group(1)
    code = code.replace('document.currentScript||', '')
    return 'window.' + hook + '=function(){\n' + code + '\n};'


REFRESH_SCRIPT = REFRESH_SCRIPT.replace('/* LOCAL_RATE_HOOKS */',
    _local_rate_hook(KILLER_RATE_SCRIPT, '__PENTA5_REFRESH_R1_KILLER__') + '\n' +
    _local_rate_hook(FRAMEWORK_RATE_SCRIPT, '__PENTA5_REFRESH_R1_FRAMEWORK__'))
REFRESH_SCRIPT = REFRESH_SCRIPT.replace('__LOCAL_HE3_AVAILABILITY_SOURCE__',
    json.dumps(re.fullmatch(r'<script[^>]*>([\s\S]*)</script>', HE3_AVAILABILITY_SCRIPT.strip()).group(1),
               ensure_ascii=True))


OBSERVATION_STYLE = '''<style id="penta5-observation-visibility">
#penta5-hidden-stat50-prediction,
#stat50_compare_stats,
#stat50-position-stats,
#selected6-single-observation,
#model_compare_stats,
#he3-paired-comparison { display:none !important; }
</style>'''

def hide_observation_sections(content):
    """Hide controls in presentation while preserving canonical sealed bytes."""
    style_pattern = r'<style\b[^>]*\bid=[\'\"]penta5-observation-visibility[\'\"][^>]*>[\s\S]*?</style>'
    if re.search(style_pattern, content, re.I):
        content = re.sub(style_pattern, lambda _: OBSERVATION_STYLE, content, count=1, flags=re.I)
    elif re.search(r'</head\s*>', content, re.I):
        content = re.sub(r'</head\s*>', lambda m: OBSERVATION_STYLE + '\n' + m.group(0), content, count=1, flags=re.I)
    else:
        content = OBSERVATION_STYLE + '\n' + content
    # This statistical-control card has no stable element id. Its marker range
    # is itself immutable evidence, so hide a wrapper outside the exact range.
    start, end = '<!-- STAT50_PREDICTION_START -->', '<!-- STAT50_PREDICTION_END -->'
    wrapper_id = 'penta5-hidden-stat50-prediction'
    if (content.count(start) == content.count(end) == 1
            and not re.search(r'\bid=[\'\"]' + wrapper_id + r'[\'\"]', content)):
        left, right = content.index(start), content.index(end) + len(end)
        if left < right:
            content = (content[:left] + '<div id="' + wrapper_id + '" hidden>\n'
                       + content[left:right] + '\n</div>' + content[right:])
    return content


def install_quiet_refresh(content):
    """Patch presentation only, including pre-existing auto-refresh installations."""
    content = re.sub(r'<meta\b(?=[^>]*\bhttp-equiv\s*=\s*[\'\"]?refresh\b)[^>]*>', '', content, flags=re.I)
    pattern = r'<script\b[^>]*\bid=[\'\"]penta5-refresh-control[\'\"][^>]*>[\s\S]*?</script>'
    if re.search(pattern, content, re.I):
        content = re.sub(pattern, lambda _: REFRESH_SCRIPT, content, count=1, flags=re.I)
    else:
        content = content.replace('</body>', REFRESH_SCRIPT + '\n</body>')
    return install_attack_editor(hide_observation_sections(content))
