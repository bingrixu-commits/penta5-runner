#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 1. 挂载分页路由 ==="
python3 - <<'PY'
from pathlib import Path
p = Path('/root/penta5-runner/penta5_backend_runner.js')
text = p.read_text(encoding='utf-8')
if 'createFrameworkPageHandler' in text:
    print('已挂载，跳过'); raise SystemExit

a0 = 'const { createAiScopeHandler } = require("./ai_scope_routes");'
if a0 not in text:
    raise SystemExit('anchor0 missing')
text = text.replace(a0, a0 + '\nconst { createFrameworkPageHandler } = require("./framework_pages");', 1)

a1 = 'const handleAiScopeRequest = createAiScopeHandler({ root: __dirname, authRoleForRequest });'
if a1 not in text:
    raise SystemExit('anchor1 missing')
text = text.replace(a1, a1 + '\nconst handleFrameworkPage = createFrameworkPageHandler({ root: __dirname, authRoleForRequest });', 1)

a2 = 'if (await handleAiScopeRequest(req, res)) return;'
if a2 not in text:
    raise SystemExit('anchor2 missing')
text = text.replace(a2, a2 + '\n  if (handleFrameworkPage(req, res)) return;', 1)

p.write_text(text, encoding='utf-8')
print('已挂载')
PY

echo
echo "=== 2. 语法 ==="
node --check framework_pages.js && echo "pages OK"
node --check penta5_backend_runner.js && echo "runner OK"

echo
echo "=== 3. 重启 ==="
pm2 restart penta5-runner --update-env 2>&1 | tail -2
sleep 6

echo
echo "=== 4. 自测 ==="
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"
for u in /f/ /f/kill15 /f/kill1 /f/nosuch; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "$A" "http://127.0.0.1:3010$u")
  echo "  $u -> $code"
done
echo "--- 未鉴权 ---"
curl -s -o /dev/null -w "  /f/kill15 无凭据 -> %{http_code}\n" http://127.0.0.1:3010/f/kill15

echo
echo "=== 5. 页面内容抽样 ==="
curl -s -u "$A" "http://127.0.0.1:3010/f/kill15" | python3 -c "
import sys,re
h=sys.stdin.read()
print('大小:',len(h))
print('含框架标题:', 'kill15' in h)
print('含区块标记:', 'KILL_FIFTEEN_START' in h)
m=re.search(r'<title>(.*?)</title>',h)
print('title:', m.group(1) if m else '-')
links=len(re.findall(r'href=\"/f/',h))
print('导航链接数:', links)
"
