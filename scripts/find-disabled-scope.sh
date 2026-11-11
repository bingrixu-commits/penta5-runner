#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 哪个作用域被停了 ==="
python3 - <<'PY'
import sys, json
sys.path.insert(0, 'lib')
import ai_scope
state = ai_scope.read_state()
for scope, entry in (state.get('scopes') or {}).items():
    if entry.get('enabled') is False:
        print(json.dumps({'scope': scope, **entry}, ensure_ascii=False, indent=2))
PY

echo
echo "=== skip 日志最近 5 条 ==="
tail -5 data/scope_skip.log 2>/dev/null || echo "(无)"

echo
echo "=== 提交未跟踪的运维脚本 ==="
git add scripts/*.sh 2>/dev/null
git -c user.name=ops-bot -c user.email=ops@local commit -m "chore(ops): 收录面板/分页/作用域相关运维脚本" 2>&1 | tail -2
git push origin main 2>&1 | tail -1
echo
echo "剩余未跟踪: $(git status --porcelain | wc -l)"
git status --porcelain | head -8
