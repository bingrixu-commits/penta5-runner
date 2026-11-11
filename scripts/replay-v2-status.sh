#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

if flock -n /var/lock/penta5-kill15-replay-v2.lock -c true 2>/dev/null; then
  echo "state: finished/not running"
else
  echo "state: RUNNING"
fi
echo
echo "=== log (tail) ==="
grep -vE "Warning|warnings.warn" data/kill15_tail/replay_v2.log 2>/dev/null | tail -25
echo
echo "=== replay_v2 ledger ==="
python3 - <<'PY'
import json, os
p = 'data/kill15_tail/replay_v2.json'
if not os.path.exists(p):
    print('no v2 replay records yet'); raise SystemExit
rows = json.load(open(p, encoding='utf-8')).get('records', [])
for r in rows:
    a = r.get('allocation') or {}
    print(r.get('target_period'), 'HIT' if r.get('group_hit') else 'miss',
          f"{r.get('position_hits')}/4",
          'A=' + '+'.join(a.get('a') or []), 'B=' + '+'.join(a.get('b') or []),
          a.get('b_sub'))
print('total', len(rows))
PY
