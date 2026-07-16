#!/bin/bash
set -e
cp -f /tmp/penta5_backend_runner.js /root/penta5-runner/penta5_backend_runner.js
cp -f /tmp/mirror_incremental.js /root/penta5-runner/api/mirror_incremental.js
echo "=== file check ==="
rg -n "compactRecentExactCodesInState|scheduleRdsMirrorAfterSave|killFrameworkItems" /root/penta5-runner/penta5_backend_runner.js | head -20
rg -n "JSON.stringify\(value\)" /root/penta5-runner/penta5_backend_runner.js | head -5
echo "=== before size ==="
ls -lh /root/penta5-runner/data/state.json
pm2 restart penta5-runner
sleep 4
echo "=== health ==="
curl -s http://127.0.0.1:3001/health
echo
echo "=== sync ==="
curl -s -X POST http://127.0.0.1:3001/sync
echo
sleep 3
echo "=== after size ==="
ls -lh /root/penta5-runner/data/state.json
curl -s http://127.0.0.1:3001/state > /tmp/panel_state.json
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
pm2 list
curl -s http://127.0.0.1:8787/health || echo "rds_api_down"
echo
