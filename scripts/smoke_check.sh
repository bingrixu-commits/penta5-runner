#!/usr/bin/env bash
# Penta5 冒烟检查：rds-api + runner + 官网开奖源 + 更稳缓存期号对齐
# 开奖源直连 SOURCE_API_URL；本机 source-proxy(:3000) 已废弃，不再检查。
set -euo pipefail

ROOT="/root/penta5-runner"
ok=0
fail=0

check_json() {
  local name="$1" url="$2"
  local body
  if ! body=$(curl -sS -m 8 "$url" 2>/dev/null); then
    echo "[FAIL] $name unreachable: $url"
    fail=$((fail + 1))
    return
  fi
  if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok') is True" 2>/dev/null; then
    echo "[OK]   $name"
    ok=$((ok + 1))
  else
    echo "[FAIL] $name bad payload: ${body:0:160}"
    fail=$((fail + 1))
  fi
}

SOURCE_API_URL="$(python3 - <<'PY'
from pathlib import Path
env = {}
for line in Path("/root/penta5-runner/.env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip().strip('"').strip("'")
print(env.get("SOURCE_API_URL", "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest"))
PY
)"

if curl -sS -m 10 "$SOURCE_API_URL" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('Status') is True
results = ((d.get('Data') or {}).get('Results') or [])
assert results and results[0].get('PeriodNo') and results[0].get('OpenCode')
" 2>/dev/null; then
  echo "[OK]   official-source"
  ok=$((ok + 1))
else
  echo "[FAIL] official-source unreachable or bad payload"
  fail=$((fail + 1))
fi

check_json "rds-api" "http://127.0.0.1:8787/health"
check_json "runner" "http://127.0.0.1:3001/health"

PANEL_AUTH=()
readarray -t PANEL_CREDS < <(python3 - <<'PY'
from pathlib import Path
env = {}
for line in Path("/root/penta5-runner/.env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip().strip('"').strip("'")
user = env.get("PANEL_USER", "")
password = env.get("PANEL_PASSWORD", "")
if user and password:
    print(f"{user}:{password}")
PY
)
if [[ ${#PANEL_CREDS[@]} -gt 0 && -n "${PANEL_CREDS[0]:-}" ]]; then
  PANEL_AUTH=(-u "${PANEL_CREDS[0]}")
fi

if curl -sS -m 8 "${PANEL_AUTH[@]}" "http://127.0.0.1:3001/exclusion-model" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
m=d.get('model') or {}
assert d.get('ok') is True
assert m.get('version','').startswith('exclusion-model-v')
assert isinstance(m.get('exclusions'), dict)
pos=(m.get('exclusions') or {}).get('positionDetail') or {}
# v2 应带双码；若仍是旧进程则至少有 position
if pos:
    wan=pos.get('wan') or {}
    assert isinstance(wan.get('killDigits'), list)
"; then
  echo "[OK]   exclusion-model"
  ok=$((ok + 1))
else
  echo "[FAIL] exclusion-model"
  fail=$((fail + 1))
fi

if curl -sS -m 8 "${PANEL_AUTH[@]}" "http://127.0.0.1:3001/stable-math-filter" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
f=d.get('filter') or {}
assert d.get('ok') is True
assert not f.get('rules')
"; then
  echo "[OK]   stable-math-filter slim"
  ok=$((ok + 1))
else
  echo "[FAIL] stable-math-filter slim"
  fail=$((fail + 1))
fi

# 形态旁路：shape-avoid-3 + 四定形态杀码必须可读且期号贴近 runner
if curl -sS -m 12 "${PANEL_AUTH[@]}" "http://127.0.0.1:3001/shape-avoid-3" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d.get('ok') is True
assert d.get('latestPeriod')
assert isinstance(d.get('groups'), list) and len(d.get('groups'))>=3
print('shape-avoid-3', d.get('latestPeriod'), 'groups', len(d.get('groups')))
"; then
  echo "[OK]   shape-avoid-3"
  ok=$((ok + 1))
else
  echo "[FAIL] shape-avoid-3"
  fail=$((fail + 1))
fi

if curl -sS -m 12 "${PANEL_AUTH[@]}" "http://127.0.0.1:3001/four-ding-shape-kills" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d.get('ok') is True
size=d.get('size') or {}
parity=d.get('parity') or {}
assert (size.get('kill') or {}).get('code'), 'missing size kill'
assert (parity.get('kill') or {}).get('code'), 'missing parity kill'
print('four-ding', d.get('latestPeriod'), 'size', (size.get('kill') or {}).get('code'), 'parity', (parity.get('kill') or {}).get('code'))
"; then
  echo "[OK]   four-ding-shape-kills"
  ok=$((ok + 1))
else
  echo "[FAIL] four-ding-shape-kills"
  fail=$((fail + 1))
fi

# wan-avoid-3 已退役（algorithms/_retired_wan_avoid_3_20260803-055601），端点已删，不再检查。

if curl -sS -m 12 "${PANEL_AUTH[@]}" "http://127.0.0.1:3001/claude/state" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert isinstance(d, dict)
assert d.get('ok') is not False or d.get('aggregate') is not None or d.get('mode')
assert 'aggregate' in d or d.get('source')=='state-file-fallback' or d.get('pending') is not None
print('claude', 'pending', len(d.get('pending') or []), 'updated', d.get('updatedAt'))
"; then
  echo "[OK]   claude/state proxy"
  ok=$((ok + 1))
else
  echo "[FAIL] claude/state proxy"
  fail=$((fail + 1))
fi

# Claude 搭配侧挂：当前 tip 必须带完整 claudeDualPosHewei 快照（含同策略 walk-forward）
if curl -sS -m 20 "${PANEL_AUTH[@]}" "http://127.0.0.1:3001/state" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
p=d.get('currentPrediction') or {}
snap=p.get('claudeDualPosHewei') or {}
assert snap.get('complete') is True, 'incomplete claudeDualPosHewei'
assert str(snap.get('version','')).startswith('claude-dual-pos-hewei-'), snap.get('version')
joint=snap.get('joint') or {}
assert int(joint.get('evalPeriods') or 0) >= 120, 'walk-forward periods too few'
print('claude-dual', snap.get('tipText'), '| wf', joint.get('evalPeriods'), 'periods')
"; then
  echo "[OK]   claude-dual-pos-hewei"
  ok=$((ok + 1))
else
  echo "[FAIL] claude-dual-pos-hewei"
  fail=$((fail + 1))
fi

# 门道：当前 tip 必须走 orchestrate L0/L1，且 L0 关键字段在 /state 可见
if curl -sS -m 20 "${PANEL_AUTH[@]}" "http://127.0.0.1:3001/state" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
p=d.get('currentPrediction') or {}
assert p, 'missing currentPrediction'
meta=p.get('orchestrateMeta') or {}
assert meta.get('path')=='orchestrate-l0-l1', meta
assert p.get('hewei') and p.get('independentHeweiKills'), 'missing hewei fields'
assert p.get('codexStable') is not None, 'missing codexStable'
base=str(p.get('basePeriod') or '')
smf=str((p.get('stableMathFilter') or {}).get('latest_period') or '')
assert base and smf and smf==base, f'smf lag tip base={base} smf={smf}'
# compactAttack / threeDingOne / threeDingTwo 已退役，不再要求封存
for k in ('autoTop6','recommend2','killFramework','threeDuishu','fourPosExclude','fourDingFramework'):
    snap=p.get(k) or {}
    assert isinstance(snap, dict) and snap.get('sealed') is True, k
print('base', base, 'path', meta.get('path'), 'smf', smf)
"; then
  echo "[OK]   orchestrate-tip"
  ok=$((ok + 1))
else
  echo "[FAIL] orchestrate-tip (L0/L1 path, smf lag, or sealed frameworks missing)"
  fail=$((fail + 1))
fi

python3 <<'PY'
import json
import urllib.request
from pathlib import Path

cache = Path("/root/penta5-runner/data/stable_math_filter.json")
if not cache.exists():
    print("[FAIL] stable_math_filter.json missing")
    raise SystemExit(2)
d = json.loads(cache.read_text())
latest = str(d.get("latest_period") or "")
if not latest:
    print("[FAIL] stable-math latest_period empty")
    raise SystemExit(2)

runner_period = ""
try:
    with urllib.request.urlopen("http://127.0.0.1:3001/health", timeout=8) as resp:
        body = json.loads(resp.read().decode("utf-8", "replace"))
    summary = body.get("summary") or body
    runner_period = str(summary.get("latestPeriod") or "")
except Exception as exc:
    print(f"[FAIL] runner health for align check: {type(exc).__name__}")
    raise SystemExit(2)

print(f"[INFO] stable-math latest={latest} runner={runner_period or 'n/a'}")
if runner_period and latest != runner_period:
    try:
        lag = abs(int(runner_period) - int(latest))
    except ValueError:
        lag = 999
    # 磁盘缓存可能异步落后；冒烟只告警，不因更稳缓存拖垮核心三件套
    if lag > 2:
        print(f"[WARN] stable-math lag vs runner: {lag} (disk cache; not a source-proxy issue)")
    else:
        print(f"[WARN] stable-math lag={lag} (async refresh window)")
else:
    print("[OK]   stable-math period aligned")
PY

pm2 jlist 2>/dev/null | python3 -c '
import sys,json
# source-proxy 已废弃；核心三件套 + supervisor
need=["penta5-runner","penta5-rds-api","penta5-supervisor"]
apps={a.get("name"): (a.get("pm2_env") or {}).get("status") for a in json.load(sys.stdin)}
bad=False
for n in need:
    st=apps.get(n,"missing")
    mark="OK" if st=="online" else "FAIL"
    if st!="online": bad=True
    print(f"[{mark}]   pm2 {n}: {st}")
st=apps.get("penta5-source-proxy")
if st and st != "stopped":
    print(f"[WARN] pm2 penta5-source-proxy still {st} (deprecated; should be deleted)")
sys.exit(1 if bad else 0)
' || fail=$((fail + 1))

echo ""
if [[ "$fail" -gt 0 ]]; then
  echo "SMOKE FAIL ($fail)"
  exit 1
fi
echo "SMOKE OK"
