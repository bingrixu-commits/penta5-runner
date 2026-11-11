#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a
BASE=http://127.0.0.1:3010/api/ai-scope
AUTH="${PANEL_USER:-}:${PANEL_PASSWORD:-}"

echo "=== POST stop kill12 ==="
curl -s -u "$AUTH" -X POST -H 'Content-Type: application/json' \
  -d '{"action":"stop","scope":"kill12"}' "$BASE" | head -c 220
echo

echo "=== 状态核对 ==="
python3 scripts/ai_scope_control.py status --scope kill12 2>&1 | grep -E '"enabled"|"scope"' | head -3

echo "=== 闸门是否生效 ==="
bash scripts/with_scope.sh kill12 python3 -c "print('LEAKED')" ; echo "输出为空即正确"

echo
echo "=== POST start kill12 ==="
curl -s -u "$AUTH" -X POST -H 'Content-Type: application/json' \
  -d '{"action":"start","scope":"kill12"}' "$BASE" | head -c 120
echo
python3 scripts/ai_scope_control.py status --scope kill12 2>&1 | grep '"enabled"' | head -1

echo
echo "=== 非法 scope 是否被拒 ==="
curl -s -o /dev/null -w "bad scope -> %{http_code}\n" -u "$AUTH" -X POST \
  -H 'Content-Type: application/json' -d '{"action":"stop","scope":"../etc"}' "$BASE"

echo
echo "=== 面板里的区块 ==="
grep -c "AI_SCOPE_START" panel.html
python3 - <<'PY'
import re
html = open('panel.html', encoding='utf-8').read()
m = re.search(r'<!-- AI_SCOPE_START -->(.*?)<!-- AI_SCOPE_END -->', html, re.S)
body = re.sub(r'<script>.*?</script>', '', m.group(1), flags=re.S) if m else ''
text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', body)).strip() if body else ''
print(text[:260])
PY
