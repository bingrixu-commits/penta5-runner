#!/usr/bin/env bash
# 在 T4 GPU 机上执行（不要在现网 CPU 面板机上跑）。
# 假设仓库已拷到 /root/penta5-runner
set -euo pipefail
ROOT="${1:-/root/penta5-runner}"
cd "$ROOT"
echo "==> 检查 GPU"
nvidia-smi
echo "==> 启动 cuOpt autoTop6 HTTP :8788"
docker compose -f scripts/sidecars/docker-compose.cuopt-autotop6.yml up -d --build
sleep 3
curl -sS -m 8 http://127.0.0.1:8788/health
echo
echo "安全组放行 8788，仅给现网 CPU 机 IP。"
echo "现网 .env 设置：PENTA5_CUOPT_URL=http://<这台T4内网IP>:8788"
echo "然后：pm2 restart penta5-runner --update-env"
echo "下班：阿里云控制台「停止」这台 T4，不要「释放」。"
