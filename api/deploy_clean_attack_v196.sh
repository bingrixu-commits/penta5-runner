#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "clean_attack_shot_v196\|buildFrameworkAttackShot\|框架攻击语：只写\|除千\|frameworkAttackItems" /root/penta5-runner/panel.html | head -25
# ensure old messy path not used for framework attack
! grep -n "buildPracticeModel(DEFAULT_PRACTICE_MODEL,list" /root/penta5-runner/panel.html | grep -q frameworkAttack || true
grep -n "frameworkAttackTextFromItems\|buildPracticeModel(DEFAULT_PRACTICE_MODEL" /root/penta5-runner/panel.html | head -10
pm2 restart penta5-runner --update-env
sleep 3
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/pc.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -oE "clean_attack_shot_v196|buildFrameworkAttackShot|千1234567890百1234567890" | sort | uniq -c
curl -s -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:3001/health
