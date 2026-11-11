#!/bin/bash
# 200 期回放进度与结论
set -u
cd /root/penta5-runner || exit 1

echo "=== 运行状态 ==="
pgrep -f "replay_v2_200" >/dev/null && echo "RUNNING" || \
  { pgrep -f "replay_kill15.py" >/dev/null && echo "RUNNING (shared process)" || echo "FINISHED"; }

echo
echo "=== 进度 ==="
python3 - <<'PY'
import json, os
p = 'data/kill15_tail/replay_v2_200.json'
if not os.path.exists(p):
    print('no records yet'); raise SystemExit
rows = json.load(open(p, encoding='utf-8')).get('records', [])
n = len(rows)
gh = sum(1 for r in rows if r.get('group_hit'))
bets = sum(len(r.get('positions') or {}) for r in rows)
hits = sum(1 for r in rows for v in (r.get('positions') or {}).values() if v.get('hit'))
print(f'已完成 {n}/200 期')
if n:
    print(f'整组 {gh}/{n} = {100.0*gh/n:.2f}%   (基线 65.61%)')
    print(f'单码 {hits}/{bets} = {100.0*hits/bets:.2f}%   (基线 90.00%)')
    import math
    p0 = 0.6561
    se = math.sqrt(p0*(1-p0)/n)
    lo, hi = (gh/n - 1.96*se)*100, (gh/n + 1.96*se)*100
    print(f'整组 95% 置信区间: {lo:.1f}% ~ {hi:.1f}%')
PY

echo
echo "=== 最近 3 条 ==="
grep -vE "Warning|warnings.warn|AI_HTTP" data/kill15_tail/replay_v2_200.log 2>/dev/null | tail -3
