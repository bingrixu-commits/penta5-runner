#!/bin/bash
set -e
sleep 6
echo "=== health ==="
curl -s --max-time 30 http://127.0.0.1:3001/health || true
echo
echo "=== sync ==="
curl -s --max-time 90 -X POST http://127.0.0.1:3001/sync || true
echo
sleep 4
echo "=== size ==="
ls -lh /root/penta5-runner/data/state.json
echo "=== markers ==="
grep -n "compactRecentExactCodesInState\|scheduleRdsMirrorAfterSave\|killFrameworkItems" /root/penta5-runner/penta5_backend_runner.js | head -20
grep -n 'JSON.stringify(value)' /root/penta5-runner/penta5_backend_runner.js | head -5
echo "=== state fields ==="
curl -s --max-time 60 http://127.0.0.1:3001/state > /tmp/panel_state.json
python3 <<'PY'
import json
d=json.load(open("/tmp/panel_state.json"))
cp=d.get("currentPrediction") or {}
ev=cp.get("evaluation") or {}
print(json.dumps({
  "hasKillFramework": "killFramework" in cp,
  "hasKillItems": bool(cp.get("killFrameworkItems")),
  "evalHasKill": "killFramework" in ev,
  "historyCount": d.get("historyCount"),
  "maxHistory": d.get("maxHistory"),
  "latestPeriod": (d.get("latestRecord") or {}).get("period"),
}, ensure_ascii=False))
PY
echo "=== mem ==="
pm2 jlist | python3 -c 'import sys,json; d=json.load(sys.stdin);
for x in d:
  if x.get("name")=="penta5-runner":
    print(x.get("monit"))'
curl -s --max-time 10 http://127.0.0.1:8787/health || true
echo
