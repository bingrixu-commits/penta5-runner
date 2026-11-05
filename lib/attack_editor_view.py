"""A browser-only editing surface; original seals, fields and copy gates stay intact."""
import re


SCRIPT_ID = 'penta5-attack-editor-control'
SCRIPT = r'''<script id="penta5-attack-editor-control">
/* all-attack-editor-v16-add-kill9 */
(function(){
'use strict';
const version='all-attack-editor-v16-add-kill9',previous=window.__PENTA5_ATTACK_EDITOR__;
if(previous&&previous.version===version){previous.check();return;}
if(previous&&typeof previous.destroy==='function')previous.destroy();
const definitions=[
 {id:'r1_framework_attack_text',copy:'r1_copy_btn',label:'杀码框架攻击语',kind:'framework'},
 {id:'r1_killer_attack_text',copy:'r1_killer_copy_btn',label:'深度排除攻击语',kind:'killer'},
 {id:'selected6-attack-text',copy:'selected6-attack-copy',label:'三定位七项框架攻击语',data:'selected6-attack-data'},
 {id:'six-independent-attack-text',copy:'six-independent-attack-copy',label:'七项独立框架攻击语',data:'six-independent-attack-data'},
 {id:'kill3-attack-text',copy:'kill3-attack-copy',label:'AI三位置+一合尾攻击语',data:'kill3-attack-data'},
 {id:'kill1-attack-text',copy:'kill1-attack-copy',label:'AI双位置+一合尾攻击语',data:'kill1-attack-data'},
 {id:'k9-attack',copy:'k9-attack-copy',label:'AI四形态+二数合+三数合攻击语'}
];
const entries=new Map();
const styleId='penta5-attack-editor-style',sourceAttribute='data-penta5-attack-editor-source';
const styleText='[data-penta5-attack-editor-source="hidden"]{display:none!important;}#six-independent-attack-edit-note{display:none!important;}';
function nodes(doc,id){return Array.from(doc.querySelectorAll('[id="'+id+'"]'));}
function unique(doc,id){const found=nodes(doc,id);return found.length===1?found[0]:null;}
function id(def,prefix){return 'penta5-edit-'+prefix+def.id;}
function sourceText(source){return source.tagName==='TEXTAREA'?source.defaultValue:String(source.textContent||'').trim();}
function pair(doc,def){
 const sources=nodes(doc,def.id),copies=nodes(doc,def.copy);
 if(sources.length!==1||copies.length!==1)return null;
 const source=sources[0],copy=copies[0];
 if((source.tagName!=='TEXTAREA'&&source.tagName!=='DIV')||copy.tagName!=='BUTTON')return null;
 return {source:source,copy:copy};
}
function put(node,text){if(node&&node.textContent!==text)node.textContent=text;}
function hide(node,hidden){if(node&&node.hidden!==hidden)node.hidden=hidden;}
function restoreSource(entry){
 if(!entry||!entry.source)return;
 const source=entry.source;
 if(source.isConnected){
  hide(source,entry.sourceHidden);
  source.removeAttribute(sourceAttribute);
 }
 if(entry.copy&&entry.copy.isConnected)hide(entry.copy,entry.copyHidden);
}
function remove(def,ambiguous){
 const entry=entries.get(def.id);
 if(entry){entry.epoch++;entry.pending=null;restoreSource(entry);entry.wrap.remove();entries.delete(def.id);}
 // A stale derived sibling must never outlive a missing native field.
 nodes(document,id(def,'wrap-')).forEach(node=>{if(node.getAttribute('data-penta5-live-derived')==='1')node.remove();});
 if(ambiguous)nodes(document,def.copy).forEach(node=>hide(node,true));
}
function identity(def,source){
 const current=unique(document,'disp_period');
 let metadata='';
 if(def.data){const data=unique(document,def.data);metadata=data?data.textContent:'missing-data';}
 else{
  const rows=Array.from(document.querySelectorAll('#ai_job_status .ai-job-row[data-ai-kind="'+def.kind+'"]'));
  metadata=rows.length===1?['data-ai-ref','data-ai-target','data-ai-deadline'].map(name=>rows[0].getAttribute(name)||'').join('|'):'missing-job';
 }
 return [def.id,current?current.textContent.trim():'',sourceText(source),metadata].join('\u001f');
}
function make(def,source,copy,retained){
 const wrap=document.createElement('div');wrap.id=id(def,'wrap-');
 wrap.setAttribute('data-penta5-live-derived','1');
 wrap.style.cssText='display:block;box-sizing:border-box;min-width:0;max-width:100%;margin:7px 0;';
 const editor=document.createElement('textarea');editor.id=id(def,'');editor.rows=3;
 editor.setAttribute('aria-label',def.label+'（可手动编辑）');editor.spellcheck=false;
 editor.style.cssText='display:block;width:100%;min-width:0;max-width:100%;box-sizing:border-box;margin:0 0 6px;padding:9px;resize:vertical;border:1px solid #cbd5e1;border-radius:7px;background:#f8fafc;color:#0f172a;font:15px/1.8 sans-serif;overflow-wrap:anywhere;';
 const button=document.createElement('button');button.id=id(def,'copy-');button.type='button';
 button.textContent='复制编辑内容';button.hidden=true;
 button.style.cssText='padding:7px 14px;border:0;border-radius:7px;background:#475569;color:#fff;font:14px/1.5 sans-serif;cursor:pointer;';
 const status=document.createElement('span');status.id=id(def,'status-');status.setAttribute('role','status');
 status.style.cssText='font:12px/1.7 sans-serif;color:#475569;overflow-wrap:anywhere;margin-left:7px;';
 const note=document.createElement('div');note.id=id(def,'note-');
 note.style.cssText='font:12px/1.7 sans-serif;color:#64748b;min-height:1.7em;overflow-wrap:anywhere;';
 wrap.append(editor,button,status,note);
 const seed=retained?retained.seed:sourceText(source);
 editor.defaultValue=seed;editor.value=retained?retained.text:seed;
 source.insertAdjacentElement('afterend',wrap);
 return {def:def,source:source,copy:copy,sourceHidden:source.hidden,copyHidden:copy.hidden,
  wrap:wrap,editor:editor,button:button,status:status,note:note,seed:seed,
  epoch:retained?retained.epoch+1:0,pending:null};
}
function ensure(def){
 const found=pair(document,def);
 if(!found){const ambiguous=nodes(document,def.id).length>1||nodes(document,def.copy).length>1;remove(def,ambiguous);return null;}
 let entry=entries.get(def.id);
 const ownIds=[id(def,'wrap-'),id(def,''),id(def,'copy-'),id(def,'status-'),id(def,'note-')];
 if(entry&&entry.wrap.isConnected){
  const owned=[entry.wrap,entry.editor,entry.button,entry.status,entry.note];
  if(ownIds.some((key,index)=>unique(document,key)!==owned[index])){remove(def,true);return null;}
  if(entry.source!==found.source){
   restoreSource(entry);entry.source=found.source;entry.sourceHidden=found.source.hidden;entry.epoch++;
  }
  if(entry.copy!==found.copy){entry.copy=found.copy;entry.copyHidden=found.copy.hidden;entry.epoch++;}
  if(entry.wrap.previousElementSibling!==entry.source)entry.source.insertAdjacentElement('afterend',entry.wrap);
 }else{
  const retained=entry?{seed:entry.seed,text:entry.editor.value,epoch:entry.epoch}:null;
  if(entry)remove(def,false);
  if(ownIds.some(key=>nodes(document,key).length)){remove(def,true);return null;}
  entry=make(def,found.source,found.copy,retained);entries.set(def.id,entry);
 }
 return entry;
}
function paint(entry){
 const source=entry.source;
 hide(source,true);
 if(source.getAttribute(sourceAttribute)!=='hidden')source.setAttribute(sourceAttribute,'hidden');
 const custom=entry.editor.value!==entry.seed;
 hide(entry.copy,custom?true:entry.copyHidden);hide(entry.button,!custom);
 const disabled=!custom||!entry.editor.value.trim()||!!entry.pending;
 if(entry.button.disabled!==disabled)entry.button.disabled=disabled;
 put(entry.note,custom?'手动编辑内容，复制以输入框为准；刷新后恢复最新攻击语，不改变统计。':
     '可输入修改；刷新后恢复最新攻击语。');
}
function check(){
 const styles=nodes(document,styleId);
 if(styles.length!==1||styles[0].tagName!=='STYLE'||styles[0].textContent!==styleText){
  definitions.forEach(def=>remove(def,true));return;
 }
 definitions.forEach(def=>{
  try{const entry=ensure(def);if(entry)paint(entry);}
  catch(error){remove(def,true);}
 });
}
function reset(freshDoc){
 // Read every fresh source before touching any draft. This is called only
 // after the parent refresh has validated all sections, scripts and JSON.
 let plans;
 try{
  if(!freshDoc||typeof freshDoc.querySelectorAll!=='function')return;
  plans=definitions.map(def=>{
   const fresh=pair(freshDoc,def);
   return {def:def,fresh:fresh,seed:fresh?sourceText(fresh.source):null,
     copyHidden:fresh?fresh.copy.hidden:false,
     ambiguous:nodes(freshDoc,def.id).length>1||nodes(freshDoc,def.copy).length>1};
  });
 }catch(error){return;}
 plans.forEach(plan=>{
  try{
   if(!plan.fresh){remove(plan.def,plan.ambiguous);return;}
   const entry=ensure(plan.def);if(!entry)return;
   entry.epoch++;entry.pending=null;entry.seed=plan.seed;entry.copyHidden=plan.copyHidden;
   if(entry.editor.defaultValue!==plan.seed)entry.editor.defaultValue=plan.seed;
   if(entry.editor.value!==plan.seed)entry.editor.value=plan.seed;
   put(entry.status,'');paint(entry);
  }catch(error){remove(plan.def,true);}
 });
}
function snapshot(def){
 const found=pair(document,def),entry=entries.get(def.id);
 if(!found||!entry||entry.source!==found.source||entry.copy!==found.copy||!entry.source.isConnected||
    !entry.wrap.isConnected||unique(document,id(def,'wrap-'))!==entry.wrap||
    unique(document,id(def,''))!==entry.editor||unique(document,id(def,'copy-'))!==entry.button||
    unique(document,id(def,'status-'))!==entry.status||unique(document,id(def,'note-'))!==entry.note)return null;
 const text=entry.editor.value;
 if(text===entry.seed||!text.trim())return null;
 return {entry:entry,source:entry.source,text:text,epoch:entry.epoch,identity:identity(def,entry.source)};
}
function same(token){
 const current=snapshot(token.entry.def);
 return !!current&&current.entry===token.entry&&current.source===token.source&&current.epoch===token.epoch&&
   current.text===token.text&&current.identity===token.identity;
}
function fallback(token){
 if(!same(token))return false;
 const active=document.activeElement,x=scrollX,y=scrollY;
 const selection=active&&typeof active.selectionStart==='number'?
   {start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection}:null;
 const temporary=document.createElement('textarea');temporary.value=token.text;temporary.readOnly=true;
 temporary.setAttribute('aria-hidden','true');temporary.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none;';
 document.body.appendChild(temporary);let copied=false;
 try{
  temporary.focus({preventScroll:true});temporary.select();
  if(same(token))copied=typeof document.execCommand==='function'&&document.execCommand('copy')===true;
 }catch(error){}finally{
  temporary.remove();
  if(active&&active.isConnected){try{active.focus({preventScroll:true});if(selection&&typeof active.setSelectionRange==='function')
   active.setSelectionRange(selection.start,selection.end,selection.direction);}catch(error){}}
  if(scrollX!==x||scrollY!==y)scrollTo(x,y);
 }
 return copied;
}
async function click(event){
 const button=event.target.closest&&event.target.closest('button');
 const def=button&&definitions.find(item=>button.id===id(item,'copy-'));
 if(!def)return;
 event.preventDefault();event.stopImmediatePropagation();
 check();const token=snapshot(def);
 if(!token||button!==token.entry.button||token.entry.pending)return;
 token.entry.pending=token;put(token.entry.status,'正在复制编辑内容…');paint(token.entry);
 let copied=false;
 try{
  if(navigator.clipboard&&typeof navigator.clipboard.writeText==='function'){
   await navigator.clipboard.writeText(token.text);copied=true;
  }else copied=fallback(token);
 }catch(error){copied=fallback(token);}
 const unchanged=same(token),entry=entries.get(def.id);
 if(entry===token.entry&&(!entry.pending||entry.pending===token)){
  if(entry.pending===token)entry.pending=null;
  put(entry.status,copied?(unchanged?'已复制编辑内容；模型封存与统计未改变。':
    '已复制先前编辑内容；页面或输入已变化，请核对当前内容。'):
    unchanged?'复制未完成，请选中文字手动复制。':'内容已变化，本次未复制；请核对后重试。');
 }
 check();
}
function input(event){
 const def=definitions.find(item=>event.target&&event.target.id===id(item,''));
 if(!def)return;
 const entry=entries.get(def.id);if(!entry||event.target!==entry.editor)return;
 put(entry.status,'');check();
}
function destroy(){
 document.removeEventListener('input',input);document.removeEventListener('click',click);
 document.removeEventListener('penta5:panel-updated',check);window.removeEventListener('pageshow',check);
 definitions.forEach(def=>remove(def,false));
 const style=unique(document,styleId);if(style&&style.tagName==='STYLE'&&style.textContent===styleText)style.remove();
}
document.addEventListener('input',input);document.addEventListener('click',click);
document.addEventListener('penta5:panel-updated',check);window.addEventListener('pageshow',check);
window.__PENTA5_ATTACK_EDITOR__={version:version,check:check,reset:reset,destroy:destroy};
if(!nodes(document,styleId).length){
 const style=document.createElement('style');style.id=styleId;style.textContent=styleText;
 (document.head||document.body).appendChild(style);
}
check();
})();
</script>'''


_NAMED = re.compile(r'<!--\s*([A-Z][A-Z0-9_]*)_(START|END)\s*-->')
_SCRIPT = re.compile(r'<script\b[^>]*\bid\s*=\s*[\'"]penta5-attack-editor-control[\'"][^>]*>[\s\S]*?</script\s*>', re.I)
_ID = re.compile(r'\bid\s*=\s*[\'"]penta5-attack-editor-control[\'"]', re.I)


def _ranges(content):
    found, stack = {}, []
    for match in _NAMED.finditer(content):
        name, side = match.groups()
        if side == 'START':
            if name in found:
                raise ValueError('Duplicate panel marker')
            found[name] = [match.start(), None, None]
            stack.append(name)
        else:
            if not stack or stack[-1] != name:
                raise ValueError('Unbalanced panel marker')
            stack.pop()
            found[name][1:3] = [match.end(), content[found[name][0]:match.end()]]
    if stack:
        raise ValueError('Unbalanced panel marker')
    return found


def install_attack_editor(content):
    """Install one controller outside all named, immutable panel fragments."""
    if not isinstance(content, str):
        raise TypeError('Panel HTML must be text')
    before = _ranges(content)
    matches, ids = list(_SCRIPT.finditer(content)), list(_ID.finditer(content))
    if len(matches) > 1 or len(ids) != len(matches):
        raise ValueError('Duplicate or invalid attack editor controller')
    if matches:
        match = matches[0]
        if any(start <= match.start() < end for start, end, _ in before.values()):
            raise ValueError('Attack editor controller is inside a protected block')
        updated = content[:match.start()] + SCRIPT + content[match.end():]
    else:
        endings = list(re.finditer(r'</body\s*>', content, re.I))
        if len(endings) != 1:
            raise ValueError('Unique panel body ending required')
        position = endings[0].start()
        if any(start <= position < end for start, end, _ in before.values()):
            raise ValueError('Panel body ending is inside a protected block')
        updated = content[:position] + SCRIPT + '\n' + content[position:]
    if {key: row[2] for key, row in before.items()} != {key: row[2] for key, row in _ranges(updated).items()}:
        raise ValueError('Attack editor installer changed a protected block')
    return updated
