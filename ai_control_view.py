"""Manual API controls, independent of immutable model cards and statistics."""
import re

from cold_observation_view import _inspect

MARKER = 'AI_API_CONTROL'
START, END = '<!-- AI_API_CONTROL_START -->', '<!-- AI_API_CONTROL_END -->'
_OWN_ID = re.compile(r'\bid\s*=\s*[\'\"]ai-api-control[\'\"]', re.I)

STYLE = '''<style>
#ai-api-control{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:13px 14px;border:1px solid #a5b4fc;border-radius:11px;background:#f8faff;color:#334155;font:14px/1.7 sans-serif;min-width:0;overflow-wrap:anywhere}
#ai-api-control *{box-sizing:border-box}#ai-api-control h3{font-size:18px;margin:0 0 4px;color:#3730a3}
#ai-api-control .ai-control-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
#ai-api-control button{font:600 15px/1.5 sans-serif;border:1px solid #cbd5e1;border-radius:7px;padding:8px 15px;background:#fff;color:#334155;cursor:pointer}
#ai-api-control #ai-control-start{background:#2563eb;color:#fff;border-color:#2563eb}#ai-api-control #ai-control-stop{background:#b91c1c;color:#fff;border-color:#b91c1c}
#ai-api-control button:disabled{opacity:.45;cursor:default}#ai-api-control input{font:14px/1.5 sans-serif;border:1px solid #cbd5e1;border-radius:5px;padding:7px;max-width:100%;width:240px;min-width:0}
#ai-api-control .ai-control-fields{display:flex;flex-wrap:wrap;gap:8px 12px;margin:7px 0}#ai-api-control label{display:flex;flex-direction:column;gap:3px;max-width:100%}
#ai-api-control .ai-control-state{font-weight:700;color:#0f172a}#ai-api-control .ai-control-note,#ai-control-updated{font-size:13px;color:#64748b}#ai-control-message{min-height:24px;color:#92400e}
#ai-api-control summary{cursor:pointer;color:#475569}#ai-api-control details{margin:6px 0}#ai-api-control [hidden]{display:none!important}
@media(max-width:430px){#ai-api-control{width:calc(100% - 16px);padding:11px}#ai-api-control h3{font-size:17px}#ai-api-control .ai-control-actions button{flex:1;padding:8px 10px}#ai-api-control label,#ai-api-control input{width:100%}}
</style>'''

SCRIPT = r'''<script id="ai-api-control-script">
(function(){
  'use strict';
  var version='manual-api-control-v1',old=window.__PENTA5_AI_API_CONTROL__;
  if(old&&old.version===version)return;
  var state=null,ready=false,busy=false,sessionAdmin=false;
  function node(id){var list=document.querySelectorAll('[id="'+id+'"]');return list.length===1?list[0]:null;}
  function say(text){var target=node('ai-control-message');if(target)target.textContent=text;}
  function valid(value){return value&&value.version===1&&value.mode==='manual'&&typeof value.enabled==='boolean'&&Number.isSafeInteger(value.generation)&&value.generation>=0&&(value.updated_at===null||(typeof value.updated_at==='string'&&value.updated_at.length<=64&&/^[0-9]{4}-[0-9]{2}-[0-9]{2}T/.test(value.updated_at)&&/(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value.updated_at)&&Number.isFinite(Date.parse(value.updated_at))));}
  function paint(){
    var start=node('ai-control-start'),stop=node('ai-control-stop'),read=node('ai-control-read'),status=node('ai-control-state'),updated=node('ai-control-updated'),admin=node('ai-control-admin');
    if(!start||!stop||!read||!status||!updated||!admin)return;
    start.disabled=busy||!ready||state.enabled;stop.disabled=busy||!ready||!state.enabled;read.disabled=busy;
    status.textContent=!ready?'API状态尚未确认':(state.enabled?'AI API：已启动':'AI API：已停止');
    updated.textContent=ready?(state.updated_at?'最近操作：'+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(state.updated_at))+'（北京时间）':'尚未操作，默认停止'):'正在等待服务器状态。';
    admin.hidden=sessionAdmin;
    ['ai-control-user','ai-control-password'].forEach(function(id){var input=node(id);if(input)input.disabled=busy;});
  }
  function consume(data,read){if(!data||data.ok!==true||!valid(data.state)||typeof data.can_control!=='boolean')throw Error('invalid-control-response');state=data.state;ready=true;if(read)sessionAdmin=data.can_control;paint();}
  async function request(options){
    var abort=new AbortController(),timer=setTimeout(function(){abort.abort();},11000);
    try{var response=await fetch('/api/ai-control',Object.assign({credentials:'same-origin',cache:'no-store',redirect:'error',signal:abort.signal},options)),data=null;try{data=await response.json();}catch(_){}return {response:response,data:data};}
    finally{clearTimeout(timer);}
  }
  async function read(){
    if(busy)return false;busy=true;paint();say('正在读取服务器控制状态…');
    try{var result=await request({method:'GET',headers:{Accept:'application/json'}});if(!result.response.ok)throw Error(result.response.status===401?'auth':'read');consume(result.data,true);say(sessionAdmin?'管理员已验证，可手动启动或停止。':'当前为只读登录；启动或停止时需要本页填写管理员账号密码。');return true;}
    catch(_){ready=false;state=null;say('状态读取失败，按钮已锁定。请确认登录后点击“刷新状态”。');return false;}
    finally{busy=false;paint();}
  }
  function authorization(){
    if(sessionAdmin)return null;
    var user=node('ai-control-user'),password=node('ai-control-password'),admin=node('ai-control-admin');
    if(!user||!password||!user.value||!password.value){if(admin)admin.open=true;say('请填写管理员账号和密码；只读账号不能修改开关。');if(user&&!user.value)user.focus({preventScroll:true});else if(password)password.focus({preventScroll:true});return false;}
    if(user.value.length>128||password.value.length>512){say('管理员账号或密码格式无效。');return false;}
    var raw=user.value+':'+password.value,bytes=new TextEncoder().encode(raw),binary='';bytes.forEach(function(b){binary+=String.fromCharCode(b);});
    password.value='';raw='';return 'Basic '+btoa(binary);
  }
  async function change(action){
    if(busy||!ready||!state)return false;
    var auth=authorization();if(auth===false)return false;
    var headers={'Content-Type':'application/json',Accept:'application/json'};if(auth)headers.Authorization=auth;
    var expected=state.generation;busy=true;paint();say(action==='start'?'正在提交启动请求…':'正在提交停止请求…');
    try{
      var result=await request({method:'POST',headers:headers,body:JSON.stringify({action:action,expected_generation:expected})});
      if(result.response.status===409&&result.data&&valid(result.data.state)){state=result.data.state;ready=true;say('状态已被其他页面更改，已刷新；请核对后重新操作。');return false;}
      if(result.response.status===401||result.response.status===403){sessionAdmin=false;var admin=node('ai-control-admin');if(admin){admin.hidden=false;admin.open=true;}say('管理员验证未通过，请重新填写管理员账号密码；当前开关未确认改变。');return false;}
      if(!result.response.ok)throw Error('change');consume(result.data,false);
      say(action==='start'?'已启动。服务器从正常下一轮计算，仅开启期间生成的新期次计入盈亏。':'已停止新API请求。历史盈亏保留，已开始期次继续结算；已发出的请求可能完成。');return true;
    }catch(_){ready=false;state=null;say('操作结果尚未确认，按钮已锁定。请先刷新状态，再决定操作。');return false;}
    finally{auth=null;delete headers.Authorization;var password=node('ai-control-password');if(password)password.value='';busy=false;paint();}
  }
  document.addEventListener('click',function(event){var button=event.target.closest&&event.target.closest('#ai-api-control button');if(!button)return;if(button.id==='ai-control-read')read();else if(button.id==='ai-control-start')change('start');else if(button.id==='ai-control-stop')change('stop');});
  document.addEventListener('visibilitychange',function(){if(!document.hidden)read();});
  window.__PENTA5_AI_API_CONTROL__={version:version,read:read,check:paint};paint();read();
})();
</script>'''


def render_control():
    """Static placeholder; browser reads authenticated public state, never secrets."""
    return (START + '\n<section id="ai-api-control">' + STYLE
            + '<h3>AI API · 手动启动 / 停止</h3><div class="ai-control-state" id="ai-control-state">API状态尚未确认</div>'
            '<div id="ai-control-updated">正在等待服务器状态。</div><div class="ai-control-actions">'
            '<button type="button" id="ai-control-start" disabled>启动 AI</button>'
            '<button type="button" id="ai-control-stop" disabled>停止 AI</button>'
            '<button type="button" id="ai-control-read">刷新状态</button></div>'
            '<details id="ai-control-admin" hidden><summary>管理员操作验证（只读登录需要填写）</summary>'
            '<div class="ai-control-fields"><label>管理员账号<input id="ai-control-user" type="text" autocomplete="username" maxlength="128"></label>'
            '<label>管理员密码<input id="ai-control-password" type="password" autocomplete="current-password" maxlength="512"></label></div>'
            '<div class="ai-control-note">仅用于这次开关请求；不修改登录身份，不在本页持久保存密码。</div></details>'
            '<div id="ai-control-message" role="status" aria-live="polite">正在读取服务器控制状态…</div>'
            '<div class="ai-control-note">默认停止；启动后按正常下一轮运行。停止后持续停止，没有定时恢复。'
            '<br>仅开启期间生成的新期次计入盈亏；停止后保留历史，已开始期次继续结算。'
            '开奖抓取与统计继续，已发出的请求可能完成。</div>'
            + SCRIPT + '</section>\n' + END)


def apply_control(content, fragment=None):
    """Install a top-level control range without changing another named block."""
    fragment = render_control() if fragment is None else fragment
    before, incoming = _inspect(content), _inspect(fragment)
    body = re.search(r'<body\b[^>]*>', content, re.I)
    if body is None:
        raise ValueError('Panel body is required')
    if (set(incoming) != {MARKER} or incoming[MARKER][2] != fragment.strip()
            or len(_OWN_ID.findall(fragment)) != 1):
        raise ValueError('Invalid AI API control fragment')
    own = before.get(MARKER)
    if own is not None:
        if own[3] != 0 or len(_OWN_ID.findall(content)) != 1:
            raise ValueError('Invalid AI API control placement')
        updated = content[:own[0]] + fragment + content[own[1]:]
    else:
        if _OWN_ID.search(content):
            raise ValueError('AI API control id exists without owned markers')
        anchor = before.get('AI_JOB_STATUS')
        if anchor is not None and anchor[3] == 0:
            position = anchor[0]
        else:
            position = body.end()
        updated = content[:position] + '\n' + fragment + '\n' + content[position:]
    after = _inspect(updated)
    if ({name: row[2] for name, row in before.items() if name != MARKER}
            != {name: row[2] for name, row in after.items() if name != MARKER}):
        raise ValueError('AI API control changed another named block')
    return updated
