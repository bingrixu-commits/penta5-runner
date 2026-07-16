#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "keep_framework_backup_slots_v199\|正式框架会选 side=backup\|绝不能把 side=backup\|注意：框架正式选型可以是 side=backup" /root/penta5-runner/panel.html | head -20
# ensure no bogus filter remains in frameworkAttackItems
python3 - <<'PY'
from pathlib import Path
t=Path('/root/penta5-runner/panel.html').read_text(encoding='utf-8',errors='ignore')
# extract frameworkAttackItems function body roughly
i=t.find('function frameworkAttackItems')
j=t.find('function stripUntouchedFrameworkPositionSegments', i)
body=t[i:j]
assert 'side==="backup"' not in body or '绝不能把 side=backup' in body
assert 'keep_framework_backup_slots_v199' in t
print('frameworkAttackItems ok')
# selectedCodes must not drop backup
k=t.find('function selectedCodes')
m=t.find('function derivedReverseShapeItems', k)
sel=t[k:m]
assert 'if(item.side==="backup")return false' not in sel
print('selectedCodes ok')
PY
pm2 restart penta5-runner --update-env
sleep 3
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/pc.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -oE "keep_framework_backup_slots_v199|绝不能把 side=backup" | sort | uniq -c
# model status summary
curl -s -b "$COOKIE" http://127.0.0.1:3001/health | python3 -c 'import sys,json; h=json.load(sys.stdin); a=h.get("summary",{}).get("modelAudit",{}); print("integrity",a.get("integrityIssueCount"),"extras",a.get("validExtraModelCount"),"/",a.get("requiredExtraModelCount")); print("period",h.get("summary",{}).get("latestPeriod"))'
