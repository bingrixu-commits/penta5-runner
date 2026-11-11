#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"

echo "=== 服务端到底返回什么 ==="
for u in / /index.html /panel.html /panel /f/ /home; do
  echo "--- $u ---"
  curl -s -I -u "$A" "http://127.0.0.1:3010$u" | head -6
done

echo
echo "=== 经 nginx 3001 ==="
for u in / /index.html; do
  echo -n "  $u -> "; curl -s -o /dev/null -w "%{http_code} loc=%{redirect_url}\n" -u "$A" "http://127.0.0.1:3001$u"
done

echo
echo "=== 服务端返回的 / 是否有缓存头 ==="
curl -s -D - -o /dev/null -u "$A" http://127.0.0.1:3001/ | grep -iE "cache|location|HTTP/"

echo
echo "=== 是否有别的地方还在发长版 ==="
grep -n "sendPanelHtml(" penta5_backend_runner.js | head -6
grep -n '"/panel.html"\|/index.html' penta5_backend_runner.js | head -6
