#!/usr/bin/env bash
# 四定回删分批扔页包装脚本
# URL 读 four_throw_urls.json（三个格式，默认留空）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/four_throw_robot.py" "$@"
