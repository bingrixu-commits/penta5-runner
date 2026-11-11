#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo -n "OFF 作用域数: "
python3 scripts/ai_scope_control.py status | grep -c '"enabled": false' || true

echo "--- 各作用域 ---"
python3 - <<'PY'
import json, subprocess
out = subprocess.run(['python3', 'scripts/ai_scope_control.py', 'status'],
                     capture_output=True, text=True).stdout
data = json.loads(out)
for row in data['scopes']:
    print('  %-10s %-4s own_key=%s' % (row['scope'], 'ON' if row['enabled'] else 'OFF', row['own_key']))
PY

echo "--- skip 日志（最近 2 条）---"
tail -2 data/scope_skip.log 2>/dev/null || echo "(无跳过记录)"
