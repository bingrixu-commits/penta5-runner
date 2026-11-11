#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 1. 语法 ==="
python3 -m py_compile lib/ai_scope_view.py && echo OK

echo
echo "=== 2. 重新渲染面板区块 ==="
python3 - <<'PY'
import sys
sys.path.insert(0, 'lib')
from panel_io import update_panel
import ai_scope_view
update_panel(lambda page: ai_scope_view.apply_control(page))
html = open('panel.html', encoding='utf-8').read()
import re
m = re.search(r'<!-- AI_SCOPE_START -->(.*?)<!-- AI_SCOPE_END -->', html, re.S)
block = m.group(1) if m else ''
print('区块存在:', bool(block))
print('独立 key 行数:', block.count('独立 key'))
print('共享 key 行数:', block.count('共享 key'))
PY

echo
echo "=== 3. 分页页面上也能看到（取最新 panel.html）==="
set -a; . ./.env; set +a
curl -s -u "${PANEL_USER:-}:${PANEL_PASSWORD:-}" http://127.0.0.1:3010/f/ | grep -c "独立 key"

echo
echo "=== 4. 提交 ==="
git add lib/ai_scope_view.py
git -c user.name=ops-bot -c user.email=ops@local commit -m "feat(scope-ui): 密钥列改为接口实时刷新（新增 3 把 key 后不再显示旧状态）" 2>&1 | tail -2
git push origin main 2>&1 | tail -1
