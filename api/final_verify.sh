#!/bin/bash
set -e
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
AUTH=""
if [ -n "${PANEL_PASSWORD:-}" ]; then
  AUTH="-u ${PANEL_USER:-admin}:${PANEL_PASSWORD}"
fi

echo "===1 file markers==="
grep -c "compactRecentExactCodesInState" penta5_backend_runner.js
grep -c "scheduleRdsMirrorAfterSave" penta5_backend_runner.js
grep -c "killFrameworkItems" penta5_backend_runner.js
grep -n 'JSON.stringify(value)' penta5_backend_runner.js | head -3

echo "===2 services==="
curl -s --max-time 20 $AUTH http://127.0.0.1:3001/health > /tmp/h3001.json
python3 - <<'PY'
import json
h=json.load(open("/tmp/h3001.json"))
s=h.get("summary") or {}
print(json.dumps({
  "ok": h.get("ok"),
  "latestPeriod": s.get("latestPeriod"),
  "historyCount": s.get("historyCount"),
  "rssMb": s.get("rssMb"),
  "integrityIssueCount": ((s.get("modelAudit") or {}).get("integrityIssueCount")),
}, ensure_ascii=False))
PY
curl -s --max-time 10 http://127.0.0.1:8787/health
echo
curl -s -o /dev/null -w "panel_http=%{http_code}\n" http://127.0.0.1:3001/panel
curl -s --max-time 30 http://127.0.0.1:3001/panel | tr '\n' ' ' | grep -o "killFrameworkBox\|自动推荐使用6项\|今日六项最高连中\|PANEL_UI_VERSION" | sort -u

echo "===3 state killFramework + disk compact==="
curl -s --max-time 60 $AUTH http://127.0.0.1:3001/state > /tmp/st.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/st.json"))
cp=d.get("currentPrediction") or {}
print(json.dumps({
  "latestPeriod": (d.get("latestRecord") or {}).get("period"),
  "hasKillFramework": isinstance(cp.get("killFramework"), dict),
  "killItems": len((cp.get("killFramework") or {}).get("items") or []),
  "historyCount": d.get("historyCount"),
}, ensure_ascii=False))
PY
ls -lh data/state.json
node --max-old-space-size=4096 - <<'NODE'
const fs=require("fs");
const s=JSON.parse(fs.readFileSync("/root/penta5-runner/data/state.json","utf8"));
let omitted=0, withCodes=0, evaluated=0;
for (const p of (s.predictions||[])) {
  if (!p || !p.evaluated) continue;
  evaluated += 1;
  const ek=p.extraKills||{};
  for (const k of Object.keys(ek)) {
    if (!/^recentExactCodeAvoid/.test(k)) continue;
    const e=ek[k]||{};
    if (e.codesOmitted) omitted += 1;
    if (Array.isArray(e.codes) && e.codes.length) withCodes += 1;
  }
}
const cur=s.currentPrediction||{};
const curHasCodes = !!(cur.extraKills && cur.extraKills.recentExactCodeAvoid300 && Array.isArray(cur.extraKills.recentExactCodeAvoid300.codes) && cur.extraKills.recentExactCodeAvoid300.codes.length);
console.log(JSON.stringify({
  evaluatedPreds: evaluated,
  recentExactOmittedSlots: omitted,
  recentExactStillHasCodes: withCodes,
  currentUnevaluatedHasCodes: !cur.evaluated && curHasCodes,
  predictionCount: (s.predictions||[]).length,
  historyCount: (s.history||[]).length
}));
NODE

echo "===4 rds vs 3001==="
python3 - <<'PY'
import json,urllib.request
h3001=json.load(open("/tmp/h3001.json")).get("summary") or {}
rds=json.load(urllib.request.urlopen("http://127.0.0.1:8787/health"))
print(json.dumps({
  "panelLatest": h3001.get("latestPeriod"),
  "panelHistory": h3001.get("historyCount"),
  "rdsLatest": rds.get("latestPeriod"),
  "rdsDraws": rds.get("drawCount"),
  "aligned": h3001.get("latestPeriod")==rds.get("latestPeriod") and h3001.get("historyCount")==rds.get("drawCount"),
}, ensure_ascii=False))
PY
