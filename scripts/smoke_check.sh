#!/usr/bin/env bash
# Penta5 冒烟检查：四件套健康 + 更稳缓存期号对齐
set -euo pipefail

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

check_json "source-proxy" "http://127.0.0.1:3000/health"
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

python3 <<'PY'
import json
from pathlib import Path
cache = Path("/root/penta5-runner/data/stable_math_filter.json")
proxy = Path("/root/penta5-runner/data/proxy_latest_cache.json")
if not cache.exists():
    print("[FAIL] stable_math_filter.json missing")
    raise SystemExit(2)
d = json.loads(cache.read_text())
latest = str(d.get("latest_period") or "")
proxy_period = ""
if proxy.exists():
    try:
        proxy_period = str(json.loads(proxy.read_text()).get("period") or "")
    except Exception:
        pass
print(f"[INFO] stable-math latest={latest} proxy={proxy_period or 'n/a'}")
if proxy_period and latest and proxy_period != latest:
    # 允许落后 1～2 期（异步刷新窗口）
    try:
        lag = abs(int(proxy_period) - int(latest))
    except ValueError:
        lag = 999
    if lag > 2:
        print(f"[FAIL] stable-math lag too large: {lag}")
        raise SystemExit(3)
    print(f"[WARN] stable-math lag={lag} (async refresh window)")
else:
    print("[OK]   stable-math period aligned")
PY

pm2 jlist 2>/dev/null | python3 -c '
import sys,json
need=["penta5-runner","penta5-rds-api","penta5-source-proxy","penta5-supervisor"]
apps={a.get("name"): (a.get("pm2_env") or {}).get("status") for a in json.load(sys.stdin)}
bad=False
for n in need:
    st=apps.get(n,"missing")
    mark="OK" if st=="online" else "FAIL"
    if st!="online": bad=True
    print(f"[{mark}]   pm2 {n}: {st}")
sys.exit(1 if bad else 0)
' || fail=$((fail + 1))

echo ""
if [[ "$fail" -gt 0 ]]; then
  echo "SMOKE FAIL ($fail)"
  exit 1
fi
echo "SMOKE OK"
