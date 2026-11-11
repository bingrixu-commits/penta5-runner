#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"

echo "===== 最终验收 $(TZ=Asia/Shanghai date '+%F %T') ====="

echo
echo "[1] 入口与分页"
for u in / /panel /full /f/ /f/kill1 /f/kill15 /f/framework; do
  printf '  %-14s ' "$u"
  curl -s -o /dev/null -w '%{http_code} %{size_download}B\n' -u "$A" "http://127.0.0.1:3001$u"
done

echo
echo "[2] 作用域开关"
python3 scripts/ai_scope_control.py status | grep -c '"enabled": false' | xargs -I{} echo "  停用数: {}"
python3 - <<'PY'
import sys
sys.path.insert(0, 'lib')
import ai_scope
rows = ai_scope.describe()
print('  作用域总数:', len(rows), '| 独立 key:', sum(1 for r in rows if r['own_key']))
PY

echo
echo "[3] kill15 线上"
python3 - <<'PY'
import json
s = json.load(open('data/kill15_tail/seals.json', encoding='utf-8'))['records']
t = json.load(open('data/kill15_tail/settlements.json', encoding='utf-8'))['records']
gh = sum(1 for r in t if r.get('group_hit'))
print(f"  封存 {len(s)} 期，最新 {s[-1].get('target_period')}，{s[-1].get('mode')}")
print(f"  结算 {len(t)} 期，整组命中 {gh}")
PY

echo
echo "[4] 复现结论"
python3 - <<'PY'
import json, os
p = 'data/kill15_tail/replay_v2_next.json'
rows = json.load(open(p, encoding='utf-8'))['records'] if os.path.exists(p) else []
n = len(rows); gh = sum(1 for r in rows if r.get('group_hit'))
print(f'  第二段 {n}/199  整组 {100.0*gh/n if n else 0:.2f}%  (基线 65.61%)')
PY
pgrep -f "/tmp/v2frozen/run.py" >/dev/null && echo "  状态: 仍在跑" || echo "  状态: 已结束"

echo
echo "[5] 守护进程与 git"
pm2 list 2>/dev/null | grep -c online | xargs -I{} echo "  pm2 online: {}"
git status -sb | head -1
git log --oneline -3

echo
echo "[6] 余额"
python3 -c "import json;b=json.load(open('data/ai_balance_guard.json'));print('  ',b.get('last_balance'),'元 | auto_paused:',b.get('auto_paused'))"

echo
echo "[7] 健康巡检 kill15"
timeout 120 python3 -u scripts/ops_health_check.py --root /root/penta5-runner 2>&1 | grep -iE "kill15_(tail|policy|retry)" | head -3
