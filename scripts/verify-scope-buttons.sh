#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 面板区块里每个框架的开关按钮 ==="
python3 - <<'PY'
import re
html = open('panel.html', encoding='utf-8').read()
m = re.search(r'<!-- AI_SCOPE_START -->(.*?)<!-- AI_SCOPE_END -->', html, re.S)
if not m:
    print('区块缺失'); raise SystemExit
block = m.group(1)
rows = re.findall(r'<code>([a-z0-9]+)</code>.*?data-action="(\w+)">(\S+?)</button>', block, re.S)
print(f'按钮数量: {len(rows)}')
for scope, action, label in rows:
    print(f'  {scope:10s} 当前按钮: {label}')
PY

echo
echo "=== 全局开关与框架开关的关系（代码）==="
grep -n "ai-api-stopped\|ai-scope-stopped" lib/ai_call_control.py | head -4
sed -n '/def require_ai_enabled/,/return result/p' lib/ai_call_control.py | head -20
