#!/bin/bash
set -e
SRC_RUNNER="/tmp/penta5_backend_runner.js"
SRC_MIRROR="/tmp/mirror_incremental.js"
DST_RUNNER="/root/penta5-runner/penta5_backend_runner.js"
DST_MIRROR="/root/penta5-runner/api/mirror_incremental.js"
MIN_RUNNER_BYTES=100000

if [ ! -f "$SRC_RUNNER" ]; then
  echo "refuse: missing $SRC_RUNNER" >&2
  exit 1
fi
SRC_SIZE=$(wc -c < "$SRC_RUNNER")
if [ "$SRC_SIZE" -lt "$MIN_RUNNER_BYTES" ]; then
  echo "refuse: $SRC_RUNNER too small (${SRC_SIZE} bytes)" >&2
  exit 1
fi
if ! grep -q 'const MODEL_VERSION' "$SRC_RUNNER" || ! grep -q 'function startServer' "$SRC_RUNNER"; then
  echo "refuse: $SRC_RUNNER incomplete (missing MODEL_VERSION or startServer)" >&2
  exit 1
fi
cp -f "$SRC_RUNNER" "$DST_RUNNER"
if [ -f "$SRC_MIRROR" ]; then
  cp -f "$SRC_MIRROR" "$DST_MIRROR"
else
  echo "skip mirror: missing $SRC_MIRROR"
fi
echo "=== file check ==="
rg -n "stripRetiredFourCodeArtifacts|scheduleRdsMirrorAfterSave|killFrameworkItems" /root/penta5-runner/penta5_backend_runner.js | head -20
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
