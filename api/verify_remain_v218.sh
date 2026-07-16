#!/bin/bash
set -e
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/panel_cookie_v218.txt
rm -f "$COOKIE"
curl -s -c "$COOKIE" -H 'Content-Type: application/json' \
  -d "{\"password\":\"${PANEL_PASSWORD}\"}" \
  http://127.0.0.1:3001/login > /tmp/login_v218.json
curl -s -b "$COOKIE" "http://127.0.0.1:3001/panel?t=$(date +%s)" > /tmp/panel_authed_v218.html
python3 - <<'PY'
html=open('/tmp/panel_authed_v218.html','r',encoding='utf-8',errors='ignore').read()
checks={
 'len':len(html),
 'v218':'20260714_two80_three500_v218' in html,
 'two':'TXT_POSITION_TWO_REMAIN_TARGETS = [80,70,60,50]' in html,
 'three':'TXT_POSITION_THREE_REMAIN_TARGETS = [500,400,300,200]' in html,
 'twoTitle':'二定旧码回删（剩80 / 70 / 60 / 50）' in html,
 'threeTitle':'三定旧码回删（剩500 / 400 / 300 / 200）' in html,
 'pure':'只回删旧码，不加排除码' in html,
 'applyBackup':'var applyBackup=false;' in html,
 'noRemain1':'删剩1组在跑' not in html,
 'noTwo1':'TXT_POSITION_TWO_REMAIN_TARGETS = [1]' not in html,
 'twoBox':'txtTwoRemainTopBox' in html,
 'threeBox':'txtThreeRemainTopBox' in html,
 'navTwo':'href="#txtTwoRemainTopBox">二定回删' in html,
 'navThree':'href="#txtThreeRemainTopBox">三定回删' in html,
}
print(checks)
assert all(v for k,v in checks.items() if k!='len')
assert checks['len']>100000
print('AUTHED_PANEL_OK')
PY
