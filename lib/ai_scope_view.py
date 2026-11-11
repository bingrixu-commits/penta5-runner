"""按框架的 AI 启停开关面板区块。

复用 AI 手动开关区块里的管理员账号密码输入框（#ai-control-user / #ai-control-password），
调用 /api/ai-scope 接口；接口只接受预置作用域内的 start/stop。
"""
import json
from html import escape

START = '<!-- AI_SCOPE_START -->'
END = '<!-- AI_SCOPE_END -->'

STYLE = """
#ai-scope-control{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #cbd5e1;border-radius:12px;background:#f8fafc;color:#334155;font:14px/1.6 sans-serif}
#ai-scope-control h3{margin:0 0 8px;font-size:17px;color:#0f172a}
#ai-scope-control .scope-note{margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#e2e8f0;color:#334155;font-size:13px}
#ai-scope-control table{width:100%;border-collapse:collapse;font-size:13px}
#ai-scope-control th,#ai-scope-control td{padding:5px 8px;border-bottom:1px solid #e2e8f0;text-align:left}
#ai-scope-control .on{color:#15803d;font-weight:700}
#ai-scope-control .off{color:#b91c1c;font-weight:700}
#ai-scope-control button{padding:3px 10px;border-radius:6px;border:1px solid #94a3b8;background:#fff;cursor:pointer}
#ai-scope-control button.stop{border-color:#fca5a5;color:#b91c1c}
#ai-scope-control button.start{border-color:#86efac;color:#15803d}
#ai-scope-control #ai-scope-message{min-height:22px;margin-top:8px;color:#92400e;font-size:13px}
"""

SCRIPT = r"""
(function(){
  var msg=null;
  function node(id){return document.getElementById(id);}
  function say(t){if(!msg)msg=node('ai-scope-message');if(msg)msg.textContent=t;}
  function creds(){
    var u=node('ai-control-user'),p=node('ai-control-password');
    return {user:(u&&u.value||'').trim(),password:(p&&p.value||'')};
  }
  function authHeader(c){return 'Basic '+btoa(unescape(encodeURIComponent(c.user+':'+c.password)));}
  function paint(rows){
    rows.forEach(function(r){
      var cell=node('ai-scope-state-'+r.scope);
      var btn=node('ai-scope-btn-'+r.scope);
      if(cell){cell.textContent=r.enabled?'运行中':'已停用';cell.className=r.enabled?'on':'off';}
      var keycell=node('ai-scope-key-'+r.scope);
      if(keycell){keycell.textContent=r.own_key?'独立 key':'共享 key';
        keycell.style.color=r.own_key?'#15803d':'#92400e';}
      if(btn){btn.textContent=r.enabled?'停止':'启动';btn.className=r.enabled?'stop':'start';
        btn.setAttribute('data-action',r.enabled?'stop':'start');
        btn.disabled=!r.can_control&&false;}
    });
  }
  function getState(){
    var c=creds();
    if(!c.user||!c.password){say('请先在上方「AI API · 手动启动/停止」里填写管理员账号密码。');return;}
    fetch('/api/ai-scope',{credentials:'same-origin',cache:'no-store',headers:{Authorization:authHeader(c)}})
      .then(function(r){return r.json().then(function(d){return {s:r.status,d:d};});})
      .then(function(x){
        if(x.s!==200||!x.d.ok){say('读取失败：'+(x.d&&x.d.error||x.s));return;}
        paint(x.d.scopes);say('已刷新（'+(x.d.can_control?'管理员':'只读')+'）');
      }).catch(function(){say('读取失败：网络错误');});
  }
  function change(scope,action){
    var c=creds();
    if(!c.user||!c.password){say('请先在上方填写管理员账号密码。');return;}
    fetch('/api/ai-scope',{method:'POST',credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json',Authorization:authHeader(c)},
      body:JSON.stringify({action:action,scope:scope})})
      .then(function(r){return r.json().then(function(d){return {s:r.status,d:d};});})
      .then(function(x){
        if(x.s!==200||!x.d.ok){say('操作失败：'+(x.d&&x.d.error||x.s));return;}
        paint(x.d.scopes);
        say(scope+(action==='stop'?' 已停止（下一周期不再拉起进程）':' 已启动'));
      }).catch(function(){say('操作失败：网络错误');});
  }
  document.addEventListener('click',function(ev){
    var t=ev.target;
    if(!t||!t.id)return;
    if(t.id==='ai-scope-read'){getState();return;}
    if(t.id.indexOf('ai-scope-btn-')===0){
      var scope=t.id.slice('ai-scope-btn-'.length);
      change(scope,t.getAttribute('data-action')||'stop');
    }
  });
  setTimeout(getState,800);
})();
"""


def _rows():
    try:
        import ai_scope
        return ai_scope.describe()
    except Exception:
        return []


def render_control():
    rows = _rows()
    body = []
    for row in rows:
        state = '运行中' if row['enabled'] else '已停用'
        cls = 'on' if row['enabled'] else 'off'
        action = 'stop' if row['enabled'] else 'start'
        label = '停止' if row['enabled'] else '启动'
        btn_cls = 'stop' if row['enabled'] else 'start'
        body.append(
            '<tr><td>%s</td><td><code>%s</code></td>'
            '<td><span id="ai-scope-key-%s">%s</span></td><td>%s</td>'
            '<td><button id="ai-scope-btn-%s" class="%s" data-action="%s">%s</button></td></tr>'
            % (escape(row['label']), escape(row['scope']), escape(row['scope']),
               '独立 key' if row['own_key'] else '共享 key',
               '<span id="ai-scope-state-%s" class="%s">%s</span>' % (escape(row['scope']), cls, state),
               escape(row['scope']), btn_cls, action, label))
    table = ('<table><thead><tr><th>框架</th><th>作用域</th><th>密钥</th><th>状态</th><th>操作</th></tr></thead>'
             '<tbody>%s</tbody></table>') % ''.join(body)
    return (
        '<section id="ai-scope-control">'
        '<style>%s</style>'
        '<h3>🧩 按框架启停（独立开关）</h3>'
        '<div class="scope-note">停止后 cron 不再拉起该框架的进程（连 python 都不启动），'
        '不影响其它框架；启动后下一个 5 分钟周期自动恢复。开关操作需要上方管理员账号密码。</div>'
        '%s'
        '<div style="margin-top:8px"><button id="ai-scope-read">刷新状态</button></div>'
        '<div id="ai-scope-message"></div>'
        '<script>%s</script>'
        '</section>'
    ) % (STYLE, table, SCRIPT)


def apply_control(content, fragment=None):
    block = START + (fragment or render_control()) + END
    if START in content and END in content:
        head, rest = content.split(START, 1)
        _old, tail = rest.split(END, 1)
        return head + block + tail
    if '</body>' in content:
        return content.replace('</body>', block + '</body>', 1)
    return content + block
