#!/bin/bash
# 按框架作用域运行 AI 任务：注入作用域标签与独立密钥。
# 用法： bash scripts/with_scope.sh kill15 python3 -u scripts/run_kill15_tail.py
set -u
scope="${1:?usage: with_scope.sh <scope> <command...>}"
shift

cd /root/penta5-runner || exit 1
export PENTA5_AI_SCOPE="$scope"

key_env=$(python3 -c "
import sys
sys.path.insert(0, 'lib')
try:
    import ai_scope
    print(ai_scope.SCOPES.get('${scope}', {}).get('key_env', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -n "${key_env:-}" ] && [ -n "${!key_env:-}" ]; then
  export DEEPSEEK_API_KEY="${!key_env}"
fi

exec "$@"
