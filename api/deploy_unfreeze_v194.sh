#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "unfreeze_ultra_light_v194\|DISPLAY_TODAY_LIMIT = 12\|remainUltraLight15\|TXT_POSITION_TASK_YIELD_MS\|txtPositionRemainSnapshotFromRows\|排队中" /root/penta5-runner/panel.html | head -20
pm2 restart penta5-runner --update-env
sleep 3
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/pc.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -oE "unfreeze_ultra_light_v194|DISPLAY_TODAY_LIMIT = 12|remainUltraLight15" | sort | uniq -c
curl -s -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:3001/health
pm2 show penta5-runner | awk '/status|uptime|restarts/{print}'
