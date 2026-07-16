#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
echo "=== file ==="
grep -n "three_remain_pure_v189\|txtThreeRemainTopBox\|不加排除码\|applyBackup=size!==3\|纯回删" /root/penta5-runner/panel.html | head -30
grep -c "再叠加替补定位杀码" /root/penta5-runner/panel.html || true
pm2 restart penta5-runner --update-env
sleep 5
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
AUTH="-u ${PANEL_USER:-admin}:${PANEL_PASSWORD}"
echo "=== health ==="
curl -s --max-time 20 $AUTH http://127.0.0.1:3001/health
echo
echo "=== panel source ==="
curl -s --max-time 30 $AUTH http://127.0.0.1:3001/panel | grep -o "three_remain_pure_v189\|txtThreeRemainTopBox\|只回删旧码，不加排除码\|再叠加替补定位杀码" | sort | uniq -c
echo "=== ports ==="
ss -lntp | grep -E ':3000|:3001' || true
