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
grep -c "stripRetiredFourCodeArtifacts" penta5_backend_runner.js
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

echo "===3 state killFramework + retired cleanup==="
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
const retiredFrameworks=["sideStable","coldFramework","dingKill","pick5000"];
const leaks=[];
function has(obj,key){return !!obj&&Object.prototype.hasOwnProperty.call(obj,key);}
function scanRetiredExtraBlock(label,block){
  Object.keys(block||{}).filter((key)=>/^recentExactCodeAvoid(?:\d+|Max)$/i.test(key)).forEach((key)=>leaks.push(`${label}.${key}`));
}
function scanPrediction(label,p){
  if(!p||typeof p!=="object")return;
  const extraBlocks=[p.extraKills,p.detail&&p.detail.extraKills,p.evaluation&&p.evaluation.independentExtras,p.evaluation&&p.evaluation.previousExtraKills];
  extraBlocks.forEach((block,index)=>scanRetiredExtraBlock(`${label}.extra[${index}]`,block));
  ["autoTop6","recommend2","killFramework","compactAttack","threeDingTwo","threeDingOne","threeDuishu"].forEach((store)=>{
    const snapshot=p[store]||{};
    const items=Array.isArray(snapshot.items)?snapshot.items:(Array.isArray(p[`${store}Items`])?p[`${store}Items`]:[]);
    items.forEach((item,index)=>{
      if(/^recentExactCodeAvoid(?:\d+|Max)$/i.test(String(item&&item.key||"")))leaks.push(`${label}.${store}.items[${index}].${item.key}`);
    });
  });
  retiredFrameworks.forEach((key)=>{
    [key,`${key}Items`,`${key}RepairReport`].forEach((field)=>{if(has(p,field))leaks.push(`${label}.${field}`);});
    if(has(p.evaluation,key))leaks.push(`${label}.evaluation.${key}`);
  });
}
const cur=s.currentPrediction||{};
scanPrediction("currentPrediction",cur);
(s.predictions||[]).forEach((prediction,index)=>scanPrediction(`predictions[${index}]`,prediction));
scanRetiredExtraBlock("counters",s.counters);
retiredFrameworks.forEach((key)=>{
  if(has(s.dailyStreakLedger,key))leaks.push(`dailyStreakLedger.${key}`);
  if(has(s.algorithmProjectHealth,key))leaks.push(`algorithmProjectHealth.${key}`);
});
console.log(JSON.stringify({
  retiredLeakCount: leaks.length,
  retiredLeaks: leaks.slice(0,50),
  predictionCount: (s.predictions||[]).length,
  historyCount: (s.history||[]).length
}));
if(leaks.length)process.exitCode=1;
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
