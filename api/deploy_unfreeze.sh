#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "three_remain_light_v192\|threeRemainLight60\|TXT_POSITION_DISPLAY_RECENT_LIMIT\|页面可继续滚动\|paintTop:true" /root/penta5-runner/panel.html | head -20
pm2 restart penta5-runner --update-env
sleep 3
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/pc.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -o "three_remain_light_v192\|页面可继续滚动\|DISPLAY_RECENT_LIMIT = 60" | sort | uniq -c
curl -s -b "$COOKIE" -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:3001/health
