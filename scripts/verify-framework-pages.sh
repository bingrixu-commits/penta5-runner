#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"

echo "=== 经 nginx(3001) 访问分页 ==="
for u in /f/ /f/kill15 /f/framework; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "$A" "http://127.0.0.1:3001$u")
  echo "  3001$u -> $code"
done

echo
echo "=== 首页导航里的框架列表 ==="
curl -s -u "$A" http://127.0.0.1:3001/f/ | grep -o 'href="/f/[a-z0-9]*"' | sort -u | tr '\n' ' '
echo

echo
echo "=== 提交 ==="
git add framework_pages.js penta5_backend_runner.js scripts/deploy-framework-pages.sh
git -c user.name=ops-bot -c user.email=ops@local commit -m "feat(panel): 每框架独立页面 /f/<scope>（含总览与导航，常取最新面板数据）" 2>&1 | tail -2
git push origin main 2>&1 | tail -2
