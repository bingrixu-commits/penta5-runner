#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 提交内容 ==="
git status --porcelain | grep -E "ai_scope|penta5_backend_runner|panel.html|test-scope" | head -10

git add ai_scope_routes.js lib/ai_scope_view.py penta5_backend_runner.js panel.html \
        scripts/test-scope-api.sh scripts/deploy-scope-ui.sh 2>/dev/null
git -c user.name=ops-bot -c user.email=ops@local commit -m "feat(scope-ui): 面板按框架启停开关（/api/ai-scope + 面板区块，管理员鉴权）" 2>&1 | tail -3
git push origin main 2>&1 | tail -2

echo
echo "=== 最终状态 ==="
python3 scripts/ai_scope_control.py status 2>&1 | grep -c '"enabled": false' | xargs -I{} echo "OFF 数: {}"
pm2 list 2>/dev/null | grep -E "penta5-runner" | head -1
