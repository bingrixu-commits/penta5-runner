#!/bin/bash
set -e
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/panel_cookie_v219.txt
rm -f "$COOKIE"
curl -s -c "$COOKIE" -H 'Content-Type: application/json' \
  -d "{\"password\":\"${PANEL_PASSWORD}\"}" \
  http://127.0.0.1:3001/login > /tmp/login_v219.json
curl -s -b "$COOKIE" "http://127.0.0.1:3001/panel?t=$(date +%s)" > /tmp/panel_authed_v219.html
python3 - <<'PY'
html=open('/tmp/panel_authed_v219.html','r',encoding='utf-8',errors='ignore').read()
checks={
 'v219':'20260714_qianbai_only_v219' in html,
 'twoTargets':'TXT_POSITION_TWO_REMAIN_TARGETS = [80,70,60,50]' in html,
 'threeTargets':'TXT_POSITION_THREE_REMAIN_TARGETS = [400,300,200,100]' in html,
 'twoOnly':'TXT_POSITION_TWO_ONLY_POSITIONS = [0,1]' in html,
 'threeOnly':'TXT_POSITION_THREE_ONLY_POSITIONS = [0,1,2]' in html,
 'combo':'if(size===2)return [TXT_POSITION_TWO_ONLY_POSITIONS.slice()];' in html,
 'twoTitle':'二定旧码回删（千百 · 剩80 / 70 / 60 / 50）' in html,
 'threeTitle':'三定旧码回删（千百十 · 剩400 / 300 / 200 / 100）' in html,
 'pure':'var applyBackup=false;' in html,
 'noSix':'六个位置模型' not in html,
 'noFourPos':'四个位置模型（纯回删）' not in html,
 'no500':'TXT_POSITION_THREE_REMAIN_TARGETS = [500,400,300,200]' not in html,
}
print(checks)
assert all(checks.values())
print('AUTHED_OK')
PY
