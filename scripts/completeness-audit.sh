#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"

echo "########## A. 全部分页可达性 ##########"
fail=0
for s in "" kill1 kill3 kill10 kill11 kill12 kill13 kill14 kill15 cold15 cold30 he3 killer framework; do
  code=$(curl -s -o /tmp/pg.html -w '%{http_code}' -u "$A" "http://127.0.0.1:3001/f/$s")
  size=$(wc -c < /tmp/pg.html)
  [ "$code" = "200" ] || fail=$((fail+1))
  printf '  %-16s %s %7sB\n' "/f/$s" "$code" "$size"
done
echo "  失败数: $fail"

echo
echo "########## B. 每页是否只含自己的开关 ##########"
for s in kill1 kill3 kill10 kill11 kill12 kill13 kill14 kill15 cold15 cold30 he3 killer framework; do
  n=$(curl -s -u "$A" "http://127.0.0.1:3001/f/$s" | grep -o 'data-scope="[a-z0-9]*"' | sort -u | wc -l)
  printf '  %-10s 开关块=%s\n' "$s" "$n"
done

echo
echo "########## C. 接口行为 ##########"
echo -n "  GET 无凭据      : "; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3001/api/ai-scope
echo -n "  GET 有凭据      : "; curl -s -o /dev/null -w '%{http_code}\n' -u "$A" http://127.0.0.1:3001/api/ai-scope
echo -n "  POST 非法 scope : "; curl -s -o /dev/null -w '%{http_code}\n' -u "$A" -X POST -H 'Content-Type: application/json' -d '{"action":"stop","scope":"../x"}' http://127.0.0.1:3001/api/ai-scope

echo
echo "########## D. 作用域与 key ##########"
python3 - <<'PY'
import sys
sys.path.insert(0, 'lib')
import ai_scope
rows = ai_scope.describe()
print('  总数:', len(rows), '| 停用:', sum(1 for r in rows if not r['enabled']), '| 独立 key:', sum(1 for r in rows if r['own_key']))
PY

echo
echo "########## E. 各框架是否仍在出号 ##########"
python3 - <<'PY'
import json
from pathlib import Path
for name in ('kill1_tail','kill3_tail','kill10_tail','kill11_tail','kill12_tail',
             'kill13_tail','kill14_tail','kill15_tail','cold15_tail','cold30_tail'):
    p = Path('data') / name / 'seals.json'
    try:
        recs = json.loads(p.read_text(encoding='utf-8')).get('records') or []
    except Exception:
        print(f'  {name:14s} 读取失败'); continue
    last = recs[-1] if recs else {}
    print(f"  {name:14s} 最新 {last.get('target_period','-')}  {str(last.get('generated_at',''))[11:19]}")
PY

echo
echo "########## F. git 完整性 ##########"
echo "  分支: $(git status -sb | head -1)"
echo "  未提交/未跟踪: $(git status --porcelain | wc -l) 项"
git status --porcelain | head -12

echo
echo "########## G. 复现最终结果 ##########"
python3 - <<'PY'
import json, os
p = 'data/kill15_tail/replay_v2_next.json'
rows = json.load(open(p, encoding='utf-8'))['records'] if os.path.exists(p) else []
n = len(rows); gh = sum(1 for r in rows if r.get('group_hit'))
bets = sum(len(r.get('positions') or {}) for r in rows)
hits = sum(1 for r in rows for v in (r.get('positions') or {}).values() if v.get('hit'))
print(f'  {n}/199 期  整组 {gh}/{n} = {100.0*gh/n if n else 0:.2f}%  单码 {hits}/{bets} = {100.0*hits/bets if bets else 0:.2f}%')
PY
pgrep -f "/tmp/v2frozen/run.py" >/dev/null && echo "  状态: 仍在跑" || echo "  状态: 已结束"

echo
echo "########## H. 磁盘与余额 ##########"
df -h / | tail -1
python3 -c "import json;print('  balance:', json.load(open('data/ai_balance_guard.json')).get('last_balance'))"
du -sh data/kill15_tail 2>/dev/null
