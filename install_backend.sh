#!/usr/bin/env bash
# 一键拉起 Penta5 四件套：source-proxy + rds-api + runner + supervisor
set -euo pipefail

APP_DIR="/root/penta5-runner"
cd "$APP_DIR"

if ! command -v node >/dev/null 2>&1; then
  echo "错误：没有检测到 node。先安装 Node.js。"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：没有检测到 python3。"
  exit 1
fi

if ! command -v pm2 >/dev/null 2>&1; then
  echo "未检测到 pm2，正在安装 pm2..."
  npm install -g pm2
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
SOURCE_API_URL="${SOURCE_API_URL:-http://127.0.0.1:3000/latest}"

echo "==> 安装 RDS API 依赖"
(cd "$APP_DIR/api" && npm install --omit=dev)

echo "==> 启动 penta5-source-proxy"
pm2 delete penta5-source-proxy >/dev/null 2>&1 || true
pm2 start "$APP_DIR/ecosystem.source-proxy.config.js"

echo "==> 启动 penta5-rds-api"
pm2 delete penta5-rds-api >/dev/null 2>&1 || true
pm2 start "$APP_DIR/api/server.js" --name penta5-rds-api --cwd "$APP_DIR/api" --time

echo "==> 启动 penta5-runner（堆 ${NODE_HEAP_MB}MB）"
pm2 delete penta5-runner >/dev/null 2>&1 || true
PORT="$PORT" SOURCE_API_URL="$SOURCE_API_URL" \
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
echo "后台四件套已启动。"
echo "健康检查："
echo "  curl -s http://127.0.0.1:3000/health"
echo "  curl -s http://127.0.0.1:8787/health"
echo "  curl -s http://127.0.0.1:3001/health"
echo "面板：http://你的服务器IP:3001/panel"
echo ""
pm2 status
