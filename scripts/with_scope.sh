#!/bin/bash
# 按框架作用域运行 AI 任务：启动闸门 + 作用域标签 + 独立密钥。
# 用法： bash scripts/with_scope.sh kill15 python3 -u scripts/run_kill15_tail.py
#
# 闸门：作用域被 stop 后，这里直接退出，连 python 进程都不启动
#       （要看跳过日志：data/scope_skip.log；临时绕过：PENTA5_SCOPE_GATE=0）
set -u
scope="${1:?usage: with_scope.sh <scope> <command...>}"
shift

cd /root/penta5-runner || exit 1
export PENTA5_AI_SCOPE="$scope"

if [ "${PENTA5_SCOPE_GATE:-1}" = "1" ]; then
  python3 - <<PY >/dev/null 2>&1
import sys
sys.path.insert(0, 'lib')
import ai_scope
sys.exit(0 if ai_scope.is_scope_enabled('${scope}') else 3)
PY
  gate=$?
  if [ "$gate" = "3" ]; then
    mkdir -p data
    printf '%s skip-launch scope=%s (disabled)\n' "$(date -Iseconds)" "$scope" >> data/scope_skip.log
    exit 0
  fi
fi

# cron 不继承 .env，这里显式加载（与其它 runner 脚本一致）
set -a
# shellcheck disable=SC1091
. ./.env 2>/dev/null || true
set +a

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
