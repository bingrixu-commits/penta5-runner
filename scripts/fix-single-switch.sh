#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 1. 语法 ==="
node --check framework_pages.js && echo OK

echo
echo "=== 2. 重启 ==="
pm2 restart penta5-runner --update-env 2>&1 | tail -1
sleep 6

echo
echo "=== 3. 每页只含自己那一个开关 ==="
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"
for s in kill15 kill1 framework; do
  html=$(curl -s -u "$A" "http://127.0.0.1:3010/f/$s")
  others=$(echo "$html" | grep -o 'data-scope="[a-z0-9]*"' | sort -u | wc -l)
  scope=$(echo "$html" | grep -o 'data-scope="[a-z0-9]*"' | sort -u | tr '\n' ' ')
  ai_scope_block=$(echo "$html" | grep -c 'AI_SCOPE_START')
  echo "  /f/$s -> 开关块 $others 个 [$scope] 全量开关块:$ai_scope_block 大小:$(echo -n "$html" | wc -c)B"
done

echo
echo "=== 4. 总览页仍保留完整列表 ==="
curl -s -u "$A" http://127.0.0.1:3010/f/ | grep -o 'href="/f/[a-z0-9]*"' | sort -u | wc -l

echo
echo "=== 5. 提交 ==="
git add framework_pages.js
git -c user.name=ops-bot -c user.email=ops@local commit -m "fix(panel): 每框架页面只保留自身开关块，移除全量开关列表" 2>&1 | tail -2
git push origin main 2>&1 | tail -1
