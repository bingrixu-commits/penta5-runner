#!/usr/bin/env bash
# 在 T4 GPU 机上执行。四定回删缓存本机 :8790，由已放行的 cuOpt :8788 反代。
set -euo pipefail
ROOT="${1:-/opt/penta5-four-backdelete}"
mkdir -p "$ROOT/data"
cp -f "$(dirname "$0")/four_backdelete_gpu_server.py" "$ROOT/four_backdelete_gpu_server.py"
cp -f "$(dirname "$0")/penta5-four-backdelete.service" /etc/systemd/system/penta5-four-backdelete.service
systemctl daemon-reload
systemctl enable --now penta5-four-backdelete
sleep 1
curl -sS -m 5 http://127.0.0.1:8790/health
echo
echo "cuOpt :8788 需带 /four-backdelete-opened 反代；改完后 systemctl restart penta5-cuopt-autotop6"
