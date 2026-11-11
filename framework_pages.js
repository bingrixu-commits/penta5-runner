'use strict';

// 按框架分页：/f/<scope> 从 panel.html 中抽出该框架区块，套上导航单独成页。
// 数据永远取自最新的 panel.html，不需要生成静态副本。
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
.fw-nav{position:sticky;top:0;z-index:9;display:flex;gap:6px;flex-wrap:wrap;align-items:center;
 padding:10px 12px;background:#0f172a;color:#e2e8f0}
.fw-nav a{color:#cbd5e1;text-decoration:none;padding:4px 9px;border-radius:6px;font-size:13px}
.fw-nav a:hover{background:#1e293b;color:#fff}
.fw-nav a.active{background:#2563eb;color:#fff}
.fw-nav .brand{font-weight:700;color:#fff;margin-right:8px}
.fw-wrap{padding:8px 0 24px}
.fw-missing{margin:24px auto;max-width:640px;padding:16px;border:1px solid #fca5a5;
 border-radius:10px;background:#fef2f2;color:#991b1b}
</style></head><body>`;

function nav(active) {
  const links = FRAMEWORKS.map(f =>
    `<a href="/f/${f.scope}"${f.scope === active ? ' class="active"' : ''}>${f.title.split(' ')[0]}</a>`
  ).join('');
  return `<nav class="fw-nav"><a class="brand" href="/">Penta5</a>` +
         `<a href="/f/"${active === '' ? ' class="active"' : ''}>框架总览</a>${links}` +
         `<a href="/" title="完整面板">完整面板</a></nav>`;
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

function indexPage(html) {
  const items = FRAMEWORKS.map(f => {
    const has = html.includes(`<!-- ${f.marker}_START -->`);
    return `<tr><td><a href="/f/${f.scope}">${f.title}</a></td>` +
           `<td><code>${f.scope}</code></td><td>${has ? '已渲染' : '无区块'}</td></tr>`;
  }).join('');
  const control = extract(html, 'AI_SCOPE');
  return `${PAGE_HEAD.replace('%TITLE%', '框架总览')}${nav('')}<div class="fw-wrap">
<section style="max-width:1000px;margin:12px auto;padding:14px;background:#fff;border:1px solid #e2e8f0;border-radius:12px">
<h3 style="margin:0 0 8px">框架总览（每个框架单独一页）</h3>
<table style="width:100%;border-collapse:collapse;font-size:13px">
<thead><tr><th style="text-align:left">框架</th><th style="text-align:left">作用域</th><th style="text-align:left">状态</th></tr></thead>
<tbody>${items}</tbody></table></section>${control}</div></body></html>`;
}

function frameworkPage(html, fw) {
  const block = extract(html, fw.marker);
  const body = block || `<div class="fw-missing">该框架暂时没有面板区块（${fw.marker} 标记不存在）。</div>`;
  const control = extract(html, 'AI_SCOPE');
  return `${PAGE_HEAD.replace('%TITLE%', fw.title)}${nav(fw.scope)}` +
         `<div class="fw-wrap">${body}${control}</div>`;
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
      // 与站点一致：未登录先去登录页，登录后回跳
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
