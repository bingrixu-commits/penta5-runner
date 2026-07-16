#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "one_backup_idle_v195\|singleBackupRefHtml\|txtPositionIdleShellHtml\|每模型只留一个替补号\|digits:mainDigit" /root/penta5-runner/panel.html | head -25
pm2 restart penta5-runner --update-env
sleep 3
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/pc.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -oE "one_backup_idle_v195|singleBackupRefHtml|暂不自动计算" | sort | uniq -c
curl -s -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:3001/health
pm2 show penta5-runner | awk '/status |uptime |restarts /{print}'
