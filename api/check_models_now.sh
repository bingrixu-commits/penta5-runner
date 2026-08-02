#!/bin/bash
set -e
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/mchk.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
echo "=== health ==="
curl -s -b "$COOKIE" http://127.0.0.1:3001/health; echo
curl -s -b "$COOKIE" "http://127.0.0.1:3001/state?_=modelcheck" -o /tmp/state_chk.json
python3 <<'PY'
import json
d=json.load(open("/tmp/state_chk.json"))
pred=d.get("currentPrediction") or {}
audit=d.get("modelAudit") or {}
latest=d.get("latestRecord") or {}
print("ok", d.get("ok"))
print("latest", latest.get("period"), latest.get("numbers"))
print("basePeriod", pred.get("basePeriod"), "baseUid", pred.get("baseUid"))
print("generatedAt", pred.get("generatedAt") or pred.get("generatedAtText"))
print("history", len(d.get("history") or []))
print("integrityCount", audit.get("integrityIssueCount"), "checkedAt", audit.get("checkedAt"))
for i in (audit.get("integrityIssues") or [])[:30]:
    print("ISSUE", i.get("key"), "|", i.get("message") or i.get("detail") or i.get("type") or i)
pk=pred.get("positionKills") or {}
for k in ["wan","qian","bai","shi"]:
    e=pk.get(k) or {}
    print("POS", k,
          "killDigits=", e.get("killDigits"),
          "rec=", e.get("recommendDigit") or e.get("selectedDigit"),
          "main=", e.get("main") or e.get("primaryDigit") or e.get("mainDigit"),
          "backup=", e.get("originalBackupDigit") or e.get("backupAlgorithmDigit") or e.get("backup") or e.get("backupDigit"))
for name in ["autoTop6","recommend2","killFramework","compactAttack","threeDingTwo","threeDingOne","threeDuishu"]:
    snap=pred.get(name) or {}
    items=snap.get("items") if isinstance(snap, dict) else []
    print("---", name, "version=", snap.get("version"), "sealed=", snap.get("sealed"), "n=", len(items or []))
    for idx, it in enumerate(items or []):
        print(" ", idx+1, it.get("kind"), it.get("key"), "side="+str(it.get("side")), it.get("name"), "code=", it.get("code"))
tm=(audit.get("truthMetrics") or {})
for name in ["autoTop6","recommend2","killFramework","compactAttack","threeDingTwo","threeDingOne","threeDuishu"]:
    m=tm.get(name) or {}
    print("TRUTH", name, "total=", m.get("total"), "hit=", m.get("hit"), "ver=", m.get("version"), "method=", m.get("method"))
# residuals / watchdog
print("watchdog", json.dumps(audit.get("watchdog") or audit.get("residuals") or {}, ensure_ascii=False)[:500])
PY
echo "=== panel version ==="
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -oE 'PANEL_UI_VERSION = "[^"]+"' | head -1
echo "=== pm2 ==="
pm2 jlist | python3 -c 'import sys,json; d=json.load(sys.stdin);
[print(x.get("name"), x.get("pm2_env",{}).get("status"), "restarts", x.get("pm2_env",{}).get("restart_time")) for x in d if "penta5" in x.get("name","")]'
