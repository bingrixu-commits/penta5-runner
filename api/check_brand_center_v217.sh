#!/bin/bash
set -e
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/panel_cookie_v217c.txt
rm -f "$COOKIE"
curl -sS -c "$COOKIE" -H 'Content-Type: application/json' \
  -d "{\"password\":\"${PANEL_PASSWORD}\"}" \
  http://127.0.0.1:3001/login > /tmp/login_v217c.json
curl -sS -b "$COOKIE" http://127.0.0.1:3001/panel -o /tmp/panel_authed_v217c.html -w 'authed %{http_code}\n'
python3 - <<'PY'
import re
from http.cookiejar import MozillaCookieJar
html=open("/tmp/panel_authed_v217c.html","r",encoding="utf-8",errors="ignore").read()
print("len", len(html))
print("version", "20260714_brand_center_v217" in html)
print("center_title", bool(re.search(r"panel-hero__center[\s\S]{0,120}<h1>五路横财数据分析</h1>", html)))
print("old_title", "不误正夜点单系统" in html)
print("old_challenge", 'class="panel-hero__center">挑战</div>' in html)
print("text_align_center", "text-align:center" in html)
print("login_json_ok", open("/tmp/login_v217c.json","r",encoding="utf-8",errors="ignore").read()[:80])
PY
ss -lntp | grep -E '3000|3001' || true
