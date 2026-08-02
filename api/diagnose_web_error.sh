#!/bin/bash
set -e
cd /root/penta5-runner
echo "=== TIME ==="
date -Is
echo "=== PM2 ==="
pm2 jlist | python3 -c 'import sys,json; d=json.load(sys.stdin);
for x in d:
  n=x.get("name","");
  if "penta5" not in n: continue
  e=x.get("pm2_env",{})
  print(n, e.get("status"), "restarts", e.get("restart_time"), "unstable", e.get("unstable_restarts"))'
echo "=== PORTS ==="
# source-proxy(:3000) 已废弃；核心端口 8787/3001 + 可选官网 SOURCE_API_URL
ss -lntp 2>/dev/null | grep -E ':3001|:8787' || netstat -lntp 2>/dev/null | grep -E ':3001|:8787' || true
if [[ -f .env ]]; then
  SOURCE_API_URL="$(grep -E '^SOURCE_API_URL=' .env | head -1 | cut -d= -f2- | tr -d '\r')"
  if [[ -n "$SOURCE_API_URL" ]]; then
    curl -sS -m 8 -o /dev/null -w "official_source=%{http_code}\n" "$SOURCE_API_URL" || echo "official_source=fail"
  fi
fi
echo "=== LOG TAIL ==="
tail -n 40 /root/.pm2/logs/penta5-runner-error.log 2>/dev/null || true
tail -n 20 /root/.pm2/logs/penta5-runner-out.log 2>/dev/null || true
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/wterr.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null || echo "LOGIN_FAIL"
echo "=== HEALTH ==="
curl -s -m 15 -b "$COOKIE" http://127.0.0.1:3001/health | python3 -c 'import sys,json
try:
 h=json.load(sys.stdin)
 s=h.get("summary") or {}
 a=s.get("modelAudit") or {}
 print("ok",h.get("ok"),"syncing",h.get("syncing") or s.get("syncing"))
 print("period",s.get("latestPeriod"),"base",s.get("currentBasePeriod"))
 print("history",s.get("historyCount"),"rssMb",s.get("rssMb"))
 print("integrity",a.get("integrityIssueCount"),"extras",a.get("validExtraModelCount"),"/",a.get("requiredExtraModelCount"))
 print("lastError",s.get("lastError"))
 print("lastSync", (s.get("lastSync") or {}).get("text"), (s.get("lastSync") or {}).get("trigger"))
 for i in (a.get("integrityIssues") or [])[:15]:
  print("ISSUE", i.get("key"), i.get("message") or i)
except Exception as e:
 print("health_parse_fail",e); print(sys.stdin.read()[:300] if False else "")'
echo
echo "=== STATE BRIEF ==="
curl -s -m 60 -b "$COOKIE" "http://127.0.0.1:3001/state?_=$(date +%s)" -o /tmp/wt_state.json -w "state_http=%{http_code} bytes=%{size_download} time=%{time_total}\n"
python3 - <<'PY'
import json
try:
 d=json.load(open('/tmp/wt_state.json'))
except Exception as e:
 print('state_json_fail',e); raise SystemExit
pred=d.get('currentPrediction') or {}
print('state.ok', d.get('ok'))
print('latest', (d.get('latestRecord') or {}).get('period'), (d.get('latestRecord') or {}).get('numbers'))
print('base', pred.get('basePeriod'), 'gen', pred.get('generatedAt'))
print('hist', len(d.get('history') or []), 'preds', len(d.get('predictions') or []))
for name in ['autoTop6','recommend2','killFramework','compactAttack','threeDingTwo','threeDingOne','threeDuishu']:
 snap=pred.get(name) or {}
 items=snap.get('items') or []
 print(name, 'sealed=', snap.get('sealed'), 'n=', len(items), 'ver=', snap.get('version'))
a=d.get('modelAudit') or {}
print('audit.integrity', a.get('integrityIssueCount'), 'checkedAt', a.get('checkedAt'))
PY
echo "=== PANEL ==="
curl -s -m 20 -b "$COOKIE" http://127.0.0.1:3001/panel -o /tmp/wt_panel.html -w "panel_http=%{http_code} bytes=%{size_download}\n"
grep -oE 'PANEL_UI_VERSION = "[^"]+"' /tmp/wt_panel.html | head -1
grep -c 'strip_full_keep_segments_v200\|keep_framework_backup_slots_v199\|restore_two_kill_digits_v198' /tmp/wt_panel.html || true
# syntax smoke: count critical fns
for f in stripUntouchedFrameworkPositionSegments frameworkAttackTextFromItems positionFormalKillDigits buildPracticeModel; do
  c=$(grep -c "function $f" /tmp/wt_panel.html || true)
  echo "fn $f count=$c"
done
echo "=== MEM ==="
free -m | head -2
ps -o pid,rss,cmd -p $(pgrep -f 'penta5_backend_runner' | head -1) 2>/dev/null || true
