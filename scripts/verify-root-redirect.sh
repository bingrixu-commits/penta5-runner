#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"

echo "=== 经 nginx(3001) ==="
echo -n "  /          -> "; curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" -u "$A" http://127.0.0.1:3001/
echo -n "  /f/        -> "; curl -s -o /dev/null -w "%{http_code}\n" -u "$A" http://127.0.0.1:3001/f/
echo -n "  /f/kill15  -> "; curl -s -o /dev/null -w "%{http_code}\n" -u "$A" http://127.0.0.1:3001/f/kill15
echo -n "  /panel     -> "; curl -s -o /dev/null -w "%{http_code} %{size_download}B\n" -u "$A" http://127.0.0.1:3001/panel

echo
echo "=== 总览页包含的框架数 ==="
curl -s -u "$A" http://127.0.0.1:3001/f/ | grep -o 'href="/f/[a-z0-9]*"' | sort -u | wc -l

echo
echo "=== 提交 ==="
git add penta5_backend_runner.js framework_pages.js
git -c user.name=ops-bot -c user.email=ops@local commit -m "feat(panel): 根路径默认进框架总览 /f/，长版面板保留在 /panel" 2>&1 | tail -2
git push origin main 2>&1 | tail -1
