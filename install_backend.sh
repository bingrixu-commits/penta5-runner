#!/usr/bin/env bash
# 一键拉起 Penta5：rds-api + runner + supervisor（开奖源直连官网，无 source-proxy）
# 用法：bash /root/penta5-runner/install_backend.sh
set -euo pipefail

APP_DIR="/root/penta5-runner"
cd "$APP_DIR"

REQUIRED_NODE_MAJOR=24
REQUIRED_NODE_MINOR=18
REQUIRED_NPM_MAJOR=11
REQUIRED_NPM_MINOR=16
REQUIRED_PM2_VERSION="7.0.3"

if ! command -v node >/dev/null 2>&1; then
  echo "错误：没有检测到 node。先安装 Node.js。"
  exit 1
fi

NODE_VERSION="$(node -p 'process.versions.node')"
IFS='.' read -r NODE_MAJOR NODE_MINOR _ <<< "$NODE_VERSION"
if [[ "$NODE_MAJOR" != "$REQUIRED_NODE_MAJOR" ]] || (( NODE_MINOR < REQUIRED_NODE_MINOR )); then
  echo "错误：需要 Node.js ${REQUIRED_NODE_MAJOR}.${REQUIRED_NODE_MINOR}.x，当前为 ${NODE_VERSION}。"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "错误：没有检测到 npm。"
  exit 1
fi

NPM_VERSION="$(npm --version)"
IFS='.' read -r NPM_MAJOR NPM_MINOR _ <<< "$NPM_VERSION"
if [[ "$NPM_MAJOR" != "$REQUIRED_NPM_MAJOR" ]] || (( NPM_MINOR < REQUIRED_NPM_MINOR )); then
  echo "错误：需要 npm ${REQUIRED_NPM_MAJOR}.${REQUIRED_NPM_MINOR}.x，当前为 ${NPM_VERSION}。"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：没有检测到 python3。"
  exit 1
fi

if ! command -v pm2 >/dev/null 2>&1 || [[ "$(pm2 -v)" != "$REQUIRED_PM2_VERSION" ]]; then
  echo "正在安装 pm2@${REQUIRED_PM2_VERSION}..."
  npm install -g "pm2@${REQUIRED_PM2_VERSION}"
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "错误：缺少 $APP_DIR/.env（RDS / 面板凭据）。"
  exit 1
fi

# 仅导出 KEY=VALUE，不打印内容
set -a
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    ''|\#*) continue ;;
  esac
  if [[ "$line" == *=* ]]; then
    export "$line"
  fi
done < "$APP_DIR/.env"
set +a

NODE_HEAP_MB="${NODE_HEAP_MB:-4096}"
PORT="${PORT:-3001}"
SOURCE_API_URL="${SOURCE_API_URL:-https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest}"

echo "==> 安装 RDS API 依赖"
(cd "$APP_DIR/api" && npm ci --omit=dev)

echo "==> 清理已废弃的 penta5-source-proxy（若存在）"
pm2 delete penta5-source-proxy >/dev/null 2>&1 || true

echo "==> 启动 penta5-rds-api"
pm2 delete penta5-rds-api >/dev/null 2>&1 || true
pm2 start "$APP_DIR/api/server.js" --name penta5-rds-api --cwd "$APP_DIR/api" --time

echo "==> 启动 penta5-runner（堆 ${NODE_HEAP_MB}MB）"
pm2 delete penta5-runner >/dev/null 2>&1 || true
PORT="$PORT" SOURCE_API_URL="$SOURCE_API_URL" \
  PENTA5_ALGO_PROCESS_MODE="${PENTA5_ALGO_PROCESS_MODE:-0}" \
  PENTA5_ORCHESTRATE_FORCE_INPROCESS="${PENTA5_ORCHESTRATE_FORCE_INPROCESS:-1}" \
  pm2 start "$APP_DIR/penta5_backend_runner.js" \
    --name penta5-runner \
    --cwd "$APP_DIR" \
    --time \
    --node-args="--max-old-space-size=${NODE_HEAP_MB}"

echo "==> 启动 penta5-supervisor"
pm2 delete penta5-supervisor >/dev/null 2>&1 || true
pm2 start "$APP_DIR/ecosystem.supervisor.config.js"

echo "==> 启动 penta5-memory-monitor"
pm2 delete penta5-memory-monitor >/dev/null 2>&1 || true
pm2 start "$APP_DIR/ecosystem.memory-monitor.config.js"

pm2 save

echo ""
echo "后台已启动（开奖源直连官网）。"
echo "健康检查："
echo "  curl -s http://127.0.0.1:8787/health"
echo "  curl -s http://127.0.0.1:3001/health"
echo "面板：http://你的服务器IP:3001/panel"
echo ""
pm2 status
