#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a

echo "=== 静态页是否可直接访问 ==="
for p in stats.html accuracy.html panel.html; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "${PANEL_USER:-}:${PANEL_PASSWORD:-}" "http://127.0.0.1:3010/$p")
  echo "  /$p -> $code"
done

echo
echo "=== 面板里的区块标记 ==="
grep -o "<!-- [A-Z0-9_]*_START -->" panel.html | sort -u | head -40

echo
echo "=== 是否有页面路由表 ==="
grep -n "\.html" penta5_backend_runner.js | head -15

echo
echo "=== 是否已有按框架分页的痕迹 ==="
ls -1 *.html | head -20
