#!/bin/bash
set -e
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/panel_cookie.txt
rm -f "$COOKIE"
curl -s -c "$COOKIE" -H 'Content-Type: application/json' \
  -d "{\"password\":\"${PANEL_PASSWORD}\"}" \
  http://127.0.0.1:3001/login > /tmp/login_out.json
echo "login=$(head -c 120 /tmp/login_out.json)"
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel > /tmp/panel_authed.html
python3 - <<'PY'
html=open("/tmp/panel_authed.html","r",encoding="utf-8",errors="ignore").read()
print("len",len(html))
checks={
  "version":"three_remain_pure_v189" in html,
  "topBox":"txtThreeRemainTopBox" in html,
  "pureText":"只回删旧码，不加排除码" in html,
  "brand":"五路横财数据分析" in html,
  "brandCenter":'class="panel-hero__center"' in html and "<h1>五路横财数据分析</h1>" in html,
  "oldChallenge":'class="panel-hero__center">挑战</div>' in html,
  "oldStackPhrase":"再叠加替补定位杀码" in html,
  "applyBackup":"applyBackup=size!==3" in html,
  "canonicalPanel":"/panel" in html and "PANEL_CANONICAL_PATH" in html,
}
print(checks)
assert checks["version"] and checks["topBox"] and checks["pureText"] and checks["brand"] and checks["brandCenter"]
assert not checks["oldChallenge"]
assert not checks["oldStackPhrase"]
print("DOM_SOURCE_OK")
PY
