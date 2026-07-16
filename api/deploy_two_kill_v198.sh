#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "restore_two_kill_digits_v198\|positionFormalKillDigits\|digits.slice(0,2)\|杀码(2码)\|正式定位杀码：最多两个" /root/penta5-runner/panel.html | head -25
# must not force single digit only
if grep -q 'return digits.slice(0,1).join' /root/penta5-runner/panel.html; then
  echo "FAIL still single-digit txt"
  exit 1
fi
pm2 restart penta5-runner --update-env
sleep 3
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/pc.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -oE "restore_two_kill_digits_v198|positionFormalKillDigits|最多两个排除号" | sort | uniq -c
curl -s -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:3001/health
