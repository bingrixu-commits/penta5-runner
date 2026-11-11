#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 复现第二段 ==="
python3 - <<'PY'
import json, os
p = 'data/kill15_tail/replay_v2_next.json'
rows = json.load(open(p, encoding='utf-8'))['records'] if os.path.exists(p) else []
n = len(rows)
gh = sum(1 for r in rows if r.get('group_hit'))
bets = sum(len(r.get('positions') or {}) for r in rows)
hits = sum(1 for r in rows for v in (r.get('positions') or {}).values() if v.get('hit'))
print(f'{n}/199 期  整组 {gh}/{n} = {100.0*gh/n if n else 0:.2f}%  单码 {hits}/{bets} = {100.0*hits/bets if bets else 0:.2f}%')
import math
if n > 30:
    p0 = 0.6561
    se = math.sqrt(p0*(1-p0)/n)
    print(f'95% CI: {100*(gh/n-1.96*se):.1f}% ~ {100*(gh/n+1.96*se):.1f}%')
PY
pgrep -f "/tmp/v2frozen/run.py" >/dev/null && echo "状态: 仍在跑" || echo "状态: 已结束"

echo
echo "=== 余额 / 框架 ==="
python3 - <<'PY'
import json
b = json.load(open('data/ai_balance_guard.json', encoding='utf-8'))
print('balance:', b.get('last_balance'), '| auto_paused:', b.get('auto_paused'))
s = json.load(open('data/kill15_tail/seals.json', encoding='utf-8'))['records']
print('kill15 seals:', len(s), '| latest', s[-1].get('target_period'), s[-1].get('mode'))
PY
TZ=Asia/Shanghai date '+now: %F %T'
