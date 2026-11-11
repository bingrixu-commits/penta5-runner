#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 1. 改路由：/ 跳 /f/，/panel 保留长版 ==="
python3 - <<'PY'
from pathlib import Path
p = Path('/root/penta5-runner/penta5_backend_runner.js')
text = p.read_text(encoding='utf-8')

if 'Location: "/f/"' in text:
    print('已改造，跳过'); raise SystemExit

# 未登录：访问 / 时登录后回跳 /f/
a1 = '''    if (req.method === "GET" && (pathname === "/" || pathname === "/panel")) {
      sendPasswordLoginHtml(req, res, "", "/panel");'''
b1 = '''    if (req.method === "GET" && (pathname === "/" || pathname === "/panel")) {
      sendPasswordLoginHtml(req, res, "", pathname === "/" ? "/f/" : "/panel");'''
if a1 not in text:
    raise SystemExit('anchor1 missing')
text = text.replace(a1, b1, 1)

# 已登录：/ 302 到 /f/，/panel 仍给长版
a2 = '''    if (req.method === "GET" && (pathname === "/" || pathname === "/panel")) {
      sendPanelHtml(req, res);
      return;
    }'''
b2 = '''    if (req.method === "GET" && pathname === "/") {
      res.writeHead(302, {"Location": "/f/", "Cache-Control": "no-store"});
      res.end();
      return;
    }
    if (req.method === "GET" && pathname === "/panel") {
      sendPanelHtml(req, res);
      return;
    }'''
if a2 not in text:
    raise SystemExit('anchor2 missing')
text = text.replace(a2, b2, 1)

p.write_text(text, encoding='utf-8')
print('路由已改造')
PY

echo
echo "=== 2. 未登录访问 /f/ 时跳登录页 ==="
python3 - <<'PY'
from pathlib import Path
p = Path('/root/penta5-runner/framework_pages.js')
text = p.read_text(encoding='utf-8')
old = """    if (role !== 'admin' && role !== 'viewer') {
      res.writeHead(401, {'Content-Type': 'text/plain; charset=utf-8',
        'WWW-Authenticate': 'Basic realm="penta5"'});
      res.end('authentication required');
      return true;
    }"""
new = """    if (role !== 'admin' && role !== 'viewer') {
      // 与站点一致：未登录先去登录页，登录后回跳
      res.writeHead(302, {'Location': '/', 'Cache-Control': 'no-store'});
      res.end();
      return true;
    }"""
if old not in text:
    raise SystemExit('anchor missing')
p.write_text(text.replace(old, new, 1), encoding='utf-8')
print('未登录改为跳转')
PY

echo
echo "=== 3. 语法与重启 ==="
node --check penta5_backend_runner.js && node --check framework_pages.js && echo "syntax OK"
pm2 restart penta5-runner --update-env 2>&1 | tail -1
sleep 6

echo
echo "=== 4. 自测 ==="
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"
echo -n "  / 已登录      -> "; curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" -u "$A" http://127.0.0.1:3010/
echo -n "  /panel 已登录 -> "; curl -s -o /dev/null -w "%{http_code} %{size_download}B\n" -u "$A" http://127.0.0.1:3010/panel
echo -n "  /f/ 已登录    -> "; curl -s -o /dev/null -w "%{http_code}\n" -u "$A" http://127.0.0.1:3010/f/
echo -n "  /f/kill15     -> "; curl -s -o /dev/null -w "%{http_code}\n" -u "$A" http://127.0.0.1:3010/f/kill15
echo -n "  / 未登录      -> "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3010/
echo -n "  /f/ 未登录    -> "; curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://127.0.0.1:3010/f/
