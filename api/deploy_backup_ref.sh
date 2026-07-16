#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
echo "=== file ==="
grep -n "backup_ref_ring_v191\|fw-backup-ref__ring\|width:44px\|替补号" /root/penta5-runner/panel.html | head -25
pm2 restart penta5-runner --update-env
sleep 4
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/panel_cookie_br.txt
rm -f "$COOKIE"
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel > /tmp/panel_br.html
python3 - <<'PY'
html=open("/tmp/panel_br.html",encoding="utf-8",errors="ignore").read()
checks={
  "version":"backup_ref_ring_v191" in html,
  "ring44":"width:44px" in html and "fw-backup-ref__ring" in html,
  "pctInRing":'fw-backup-ref__ring" aria-hidden="true"><span class="fw-backup-ref__pct">' in html or "fw-backup-ref__ring\" aria-hidden=\"true\"><span class=\"fw-backup-ref__pct\">" in html,
  "canonical":"PANEL_CANONICAL_PATH" in html and "/panel" in html,
}
print(checks)
assert checks["version"] and checks["ring44"]
print("OK")
PY
curl -s -b "$COOKIE" -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:3001/health
ss -lntp | grep -E ':3001' || true
