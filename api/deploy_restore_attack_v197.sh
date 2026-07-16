#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "restore_attack_keep_v197\|stripUntouchedFrameworkPositionSegments\|buildPracticeModel(DEFAULT_PRACTICE_MODEL\|buildFrameworkAttackShot\|除千" /root/penta5-runner/panel.html | head -30
# must NOT have wrong builder
if grep -q "function buildFrameworkAttackShot" /root/penta5-runner/panel.html; then
  echo "FAIL: old wrong builder still present"
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
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -oE "restore_attack_keep_v197|stripUntouchedFrameworkPositionSegments|buildPracticeModel\(DEFAULT_PRACTICE_MODEL" | sort | uniq -c
# wrong exclude tokens should not be in frameworkAttack path source
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -c 'pushToken("除"+lab+digit)' || true
curl -s -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:3001/health
