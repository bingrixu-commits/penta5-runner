#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 1. 挂载路由 ==="
python3 - <<'PY'
from pathlib import Path
p = Path('/root/penta5-runner/penta5_backend_runner.js')
text = p.read_text(encoding='utf-8')

if 'createAiScopeHandler' in text:
    print('已挂载，跳过')
    raise SystemExit

anchor1 = 'const handleAiControlRequest = createAiControlHandler({ root: __dirname, authRoleForRequest });'
if anchor1 not in text:
    raise SystemExit('anchor1 missing')
text = text.replace(
    anchor1,
    anchor1 + '\nconst handleAiScopeRequest = createAiScopeHandler({ root: __dirname, authRoleForRequest });',
    1)

anchor2 = 'if (await handleAiControlRequest(req, res)) return;'
if anchor2 not in text:
    raise SystemExit('anchor2 missing')
text = text.replace(anchor2, anchor2 + '\n  if (await handleAiScopeRequest(req, res)) return;', 1)

anchor0 = 'const { createAiControlHandler } = require("./ai_control_routes");'
text = text.replace(anchor0, anchor0 + '\nconst { createAiScopeHandler } = require("./ai_scope_routes");', 1)

p.write_text(text, encoding='utf-8')
print('已挂载')
PY

echo
echo "=== 2. 语法检查 ==="
node --check ai_scope_routes.js && echo "route OK"
node --check penta5_backend_runner.js && echo "runner OK"

echo
echo "=== 3. 写入面板区块 ==="
python3 - <<'PY'
import sys
sys.path.insert(0, 'lib')
from panel_io import update_panel
import ai_scope_view

update_panel(lambda page: ai_scope_view.apply_control(page))
content = open('panel.html', encoding='utf-8').read()
print('区块存在:', ai_scope_view.START in content)
PY

echo
echo "=== 4. 重启主 runner（加载新路由）==="
pm2 restart penta5-runner --update-env 2>&1 | tail -3
sleep 6
pm2 list 2>/dev/null | grep penta5-runner

echo
echo "=== 5. 接口自测 ==="
set -a; . ./.env; set +a
curl -s -o /dev/null -w "GET 无凭据 -> %{http_code}\n" http://127.0.0.1:3010/api/ai-scope
curl -s -u "${PANEL_USER:-}:${PANEL_PASSWORD:-}" http://127.0.0.1:3010/api/ai-scope | head -c 300
echo
