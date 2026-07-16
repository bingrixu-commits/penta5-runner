#!/bin/bash
set -e
cd /root/penta5-runner
# load panel auth without printing secrets
set -a
# shellcheck disable=SC1091
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
AUTH=""
if [ -n "${PANEL_PASSWORD:-}" ]; then
  USER_NAME="${PANEL_USER:-admin}"
  AUTH="-u ${USER_NAME}:${PANEL_PASSWORD}"
fi
echo "=== health ==="
curl -s --max-time 30 $AUTH http://127.0.0.1:3001/health
echo
echo "=== sync ==="
curl -s --max-time 90 $AUTH -X POST http://127.0.0.1:3001/sync
echo
sleep 3
echo "=== size ==="
ls -lh /root/penta5-runner/data/state.json
curl -s --max-time 90 $AUTH http://127.0.0.1:3001/state > /tmp/panel_state.json
python3 <<'PY'
import json
d=json.load(open("/tmp/panel_state.json"))
cp=d.get("currentPrediction") or {}
ev=cp.get("evaluation") or {}
print(json.dumps({
  "ok": True,
  "hasKillFramework": "killFramework" in cp,
  "killItemCount": len((cp.get("killFramework") or {}).get("items") or cp.get("killFrameworkItems") or []),
  "evalHasKill": "killFramework" in ev,
  "historyCount": d.get("historyCount"),
  "maxHistory": d.get("maxHistory"),
  "latestPeriod": (d.get("latestRecord") or {}).get("period"),
}, ensure_ascii=False))
# sample evaluated prediction for codesOmitted
preds=d.get("predictions") or []
omitted=0
for p in preds:
  ek=(p.get("extraKills") or {})
  for k,v in ek.items():
    if isinstance(v,dict) and v.get("codesOmitted"):
      omitted += 1
print("panelPredictions", len(preds), "codesOmittedSeenInPanelPreds", omitted)
PY
echo "=== mem ==="
pm2 jlist | python3 -c 'import sys,json; d=json.load(sys.stdin);
for x in d:
  if x.get("name") in ("penta5-runner","penta5-rds-api"):
    print(x.get("name"), x.get("monit"))'
echo "=== rds ==="
curl -s --max-time 10 http://127.0.0.1:8787/health
echo
echo "=== mirror once ==="
cd /root/penta5-runner/api
node --max-old-space-size=4096 mirror_incremental.js --from-live | tail -n 30
curl -s --max-time 10 http://127.0.0.1:8787/health
echo
