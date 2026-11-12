'use strict';

// 按框架分页：/f/<scope> 从 panel.html 抽出该框架区块，套导航单独成页。
// 每页只带「本框架自己的」启停开关，不再列出其它框架。
const fs = require('fs');
const path = require('path');

const FRAMEWORKS = [
  {scope: 'kill1', marker: 'KILL_ONE', title: 'kill1 双定位杀码'},
  {scope: 'kill3', marker: 'KILL_THREE', title: 'kill3 定位杀码'},
  {scope: 'kill10', marker: 'KILL_TEN', title: 'kill10 定位杀码'},
  {scope: 'kill11', marker: 'KILL_ELEVEN', title: 'kill11 三定位杀码'},
  {scope: 'kill12', marker: 'KILL_TWELVE', title: 'kill12 定位杀码'},
  {scope: 'kill13', marker: 'KILL_THIRTEEN', title: 'kill13 定位杀码'},
  {scope: 'kill14', marker: 'KILL_FOURTEEN', title: 'kill14 四位置排除'},
  {scope: 'kill15', marker: 'KILL_FIFTEEN', title: 'kill15 分位杀号'},
  {scope: 'cold15', marker: 'COLD_FIFTEEN', title: 'cold15 冷号'},
  {scope: 'cold30', marker: 'COLD_THIRTY', title: 'cold30 冷号'},
  {scope: 'he3', marker: 'HE3_REFINE', title: 'he3 精修'},
  {scope: 'killer', marker: 'R1_KILLER', title: 'killer 主杀号'},
  {scope: 'framework', marker: 'R1_FRAMEWORK', title: 'framework 框架'},
];

const PAGE_HEAD = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%TITLE% · Penta5</title>
<style>
body{margin:0;background:#f1f5f9;font:14px/1.6 system-ui,sans-serif;color:#0f172a}
.fw-nav{position:sticky;top:0;z-index:9;display:flex;gap:4px;flex-wrap:wrap;align-items:center;
 padding:8px 12px;background:#0f172a;color:#e2e8f0}
.fw-nav a{color:#cbd5e1;text-decoration:none;padding:3px 8px;border-radius:6px;font-size:13px}
.fw-nav a:hover{background:#1e293b;color:#fff}
.fw-nav a.active{background:#2563eb;color:#fff}
.fw-nav .brand{font-weight:700;color:#fff;margin-right:6px}
.fw-wrap{padding:6px 0 24px}
.fw-switch{max-width:1240px;margin:10px auto;padding:10px 14px;border:1px solid #cbd5e1;
 border-radius:10px;background:#fff;display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:13px}
.fw-switch .st{font-weight:700}
.fw-switch .on{color:#15803d}.fw-switch .off{color:#b91c1c}
.fw-switch button{padding:3px 12px;border-radius:6px;border:1px solid #94a3b8;background:#fff;cursor:pointer}
.fw-switch button.stop{border-color:#fca5a5;color:#b91c1c}
.fw-switch button.start{border-color:#86efac;color:#15803d}
.fw-switch input{padding:2px 6px;border:1px solid #cbd5e1;border-radius:5px;font-size:12px;width:110px}
.fw-switch .msg{color:#92400e;font-size:12px}
.fw-switch .ai-global{font-size:12px;color:#475569}
.fw-switch .ai-global.off{color:#b91c1c;font-weight:700}
.fw-missing{margin:24px auto;max-width:640px;padding:16px;border:1px solid #fca5a5;
 border-radius:10px;background:#fef2f2;color:#991b1b}
</style></head><body>`;

function nav(active) {
  const links = FRAMEWORKS.map(f =>
    `<a href="/f/${f.scope}"${f.scope === active ? ' class="active"' : ''}>${f.title.split(' ')[0]}</a>`
  ).join('');
  return `<nav class="fw-nav"><a class="brand" href="/f/"${active === '' ? ' class="active"' : ''}>总览</a>${links}</nav>`;
}

function extract(html, marker) {
  const start = `<!-- ${marker}_START -->`;
  const end = `<!-- ${marker}_END -->`;
  const i = html.indexOf(start);
  if (i < 0) return '';
  const j = html.indexOf(end, i);
  if (j < 0) return '';
  return html.slice(i, j + end.length);
}

// 单框架开关：状态用会话读取（只读），启动/停止需要管理员账号密码
function switchBlock(fw) {
  return `<section class="fw-switch" data-scope="${fw.scope}">
<strong>${fw.title}</strong>
<span class="st" id="fw-state">读取中…</span>
<button id="fw-toggle" disabled>…</button>
<span class="ai-global" id="fw-global">全局 AI：读取中…</span>
<details style="margin-left:auto"><summary style="cursor:pointer;color:#475569">管理员操作</summary>
<span style="margin-left:8px">账号 <input id="fw-user" autocomplete="username"></span>
<span style="margin-left:6px">密码 <input id="fw-pass" type="password" autocomplete="current-password"></span>
</details>
<span class="msg" id="fw-msg"></span>
</section>
<script>
(function(){
  var SCOPE='${fw.scope}';
  function n(id){return document.getElementById(id);}
  function say(t){n('fw-msg').textContent=t||'';}
  function creds(){return {u:(n('fw-user').value||'').trim(),p:n('fw-pass').value||''};}
  function auth(c){return 'Basic '+btoa(unescape(encodeURIComponent(c.u+':'+c.p)));}
  function paint(on){
    var s=n('fw-state'),b=n('fw-toggle');
    s.textContent=on?'运行中':'已停用';s.className='st '+(on?'on':'off');
    b.textContent=on?'停止':'启动';b.className=on?'stop':'start';
    b.dataset.action=on?'stop':'start';b.disabled=false;
  }
  function readGlobal(){
    fetch('/api/ai-control',{credentials:'same-origin',cache:'no-store'})
      .then(function(r){return r.json();})
      .then(function(d){
        var el=n('fw-global');if(!el)return;
        if(!d.ok){el.textContent='全局 AI：读取失败';el.className='ai-global';return;}
        var s=d.state||{};var on=s.enabled===true;
        el.textContent='全局 AI：'+(on?'运行中':'已停止')+'（第'+(s.generation==null?'?':s.generation)+'代）';
        el.className='ai-global'+(on?'':' off');
        if(!on){say('全局 AI 已停止：本框架不会发布新一期，页面停留在最近一期；请先在主面板开启全局 AI。');}
      }).catch(function(){
        var el=n('fw-global');if(el){el.textContent='全局 AI：网络错误';el.className='ai-global off';}
      });
  }
  function read(){
    fetch('/api/ai-scope',{credentials:'same-origin',cache:'no-store'})
      .then(function(r){return r.json();})
      .then(function(d){
        if(!d.ok){say('读取失败：'+(d.error||''));return;}
        var row=(d.scopes||[]).filter(function(x){return x.scope===SCOPE;})[0];
        if(!row){say('该框架未注册开关');return;}
        paint(row.enabled);
      }).catch(function(){say('读取失败：网络错误');});
  }
  function change(action){
    var c=creds();
    if(!c.u||!c.p){say('请先展开「管理员操作」填账号密码再切换');return;}
    fetch('/api/ai-scope',{method:'POST',credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json',Authorization:auth(c)},
      body:JSON.stringify({action:action,scope:SCOPE})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(!d.ok){say('操作失败：'+(d.error||''));return;}
        paint(d.enabled);say(action==='stop'?'已停止，下一周期不再拉起进程':'已启动');
      }).catch(function(){say('操作失败：网络错误');});
  }
  n('fw-toggle').addEventListener('click',function(){change(this.dataset.action||'stop');});
  read();
  readGlobal();
  setInterval(read,60000);
  setInterval(readGlobal,60000);
})();
</script>`;
}

function indexPage(html) {
  const items = FRAMEWORKS.map(f => {
    const has = html.includes(`<!-- ${f.marker}_START -->`);
    return `<tr><td><a href="/f/${f.scope}">${f.title}</a></td>` +
           `<td><code>${f.scope}</code></td><td>${has ? '已渲染' : '无区块'}</td></tr>`;
  }).join('');
  return `${PAGE_HEAD.replace('%TITLE%', '框架总览')}${nav('')}<div class="fw-wrap">
<section style="max-width:1000px;margin:12px auto;padding:14px;background:#fff;border:1px solid #e2e8f0;border-radius:12px">
<h3 style="margin:0 0 8px">框架总览（每框架一页）</h3>
<table style="width:100%;border-collapse:collapse;font-size:13px">
<thead><tr><th style="text-align:left">框架</th><th style="text-align:left">作用域</th><th style="text-align:left">状态</th></tr></thead>
<tbody>${items}</tbody></table></section></div></body></html>`;
}

function frameworkPage(html, fw) {
  const block = extract(html, fw.marker);
  const body = block || `<div class="fw-missing">该框架暂时没有面板区块（${fw.marker} 标记不存在）。</div>`;
  return `${PAGE_HEAD.replace('%TITLE%', fw.title)}${nav(fw.scope)}` +
         `<div class="fw-wrap">${switchBlock(fw)}${body}</div>`;
}

function createFrameworkPageHandler(options) {
  const root = path.resolve(options.root);
  const authRoleForRequest = options.authRoleForRequest;

  return function handleFrameworkPage(req, res) {
    if (req.method !== 'GET') return false;
    let url;
    try { url = new URL(req.url || '/', 'http://localhost'); } catch (_) { return false; }
    const m = /^\/f(?:\/([a-z0-9]{2,16}))?\/?$/.exec(url.pathname);
    if (!m) return false;
    const role = authRoleForRequest(req);
    if (role !== 'admin' && role !== 'viewer') {
      res.writeHead(302, {'Location': '/', 'Cache-Control': 'no-store'});
      res.end();
      return true;
    }
    let html;
    try {
      html = fs.readFileSync(path.join(root, 'panel.html'), 'utf8');
    } catch (_) {
      res.writeHead(503, {'Content-Type': 'text/plain; charset=utf-8'});
      res.end('panel unavailable');
      return true;
    }
    const scope = m[1];
    let page;
    if (!scope) {
      page = indexPage(html);
    } else {
      const fw = FRAMEWORKS.find(item => item.scope === scope);
      if (!fw) { res.writeHead(404, {'Content-Type': 'text/plain; charset=utf-8'}); res.end('unknown framework'); return true; }
      page = frameworkPage(html, fw);
    }
    res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'SAMEORIGIN'});
    res.end(page);
    return true;
  };
}

module.exports = {createFrameworkPageHandler, FRAMEWORKS};
