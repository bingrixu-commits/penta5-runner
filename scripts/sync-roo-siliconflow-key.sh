#!/usr/bin/env bash
# 从 .env 读取 SILICONFLOW_API_KEY，写入 Roo Code 配置（default + siliconflow）
set -euo pipefail

ROOT="/root/penta5-runner"
ENV_FILE="${PENTA5_ENV_PATH:-$ROOT/.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "[sync-roo-key] 缺少 $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | sed 's/\r$//')
set +a

KEY="${SILICONFLOW_API_KEY:-}"
if [ -z "$KEY" ]; then
  echo "[sync-roo-key] .env 里没有 SILICONFLOW_API_KEY"
  echo "请在 $ENV_FILE 增加一行："
  echo "SILICONFLOW_API_KEY=sk-你的SiliconFlow密钥"
  exit 1
fi

export SYNC_KEY="$KEY"

node <<NODE
const fs = require("fs");
const key = process.env.SYNC_KEY;
const files = [
  "$ROOT/.roo/roo-code-settings.json",
  "/root/.roo/roo-code-settings.json",
  "/root/roo-code-settings.json",
];
for (const file of files) {
  if (!fs.existsSync(file)) continue;
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  for (const name of ["default", "siliconflow"]) {
    if (data.providerProfiles && data.providerProfiles.apiConfigs && data.providerProfiles.apiConfigs[name]) {
      data.providerProfiles.apiConfigs[name].openAiApiKey = key;
    }
  }
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n", { mode: 0o600 });
  try { fs.chmodSync(file, 0o600); } catch (_) {}
  console.log("[sync-roo-key] updated", file);
}
NODE

# 验证 SiliconFlow（不打印 key）
HTTP_CODE=$(curl -s -o /tmp/sf_test.json -w '%{http_code}' \
  --connect-timeout 15 --max-time 30 \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-Coder-30B-A3B-Instruct","messages":[{"role":"user","content":"ping"}],"max_tokens":8}' \
  https://api.siliconflow.com/v1/chat/completions)

echo "[sync-roo-key] siliconflow chat test http=$HTTP_CODE"
if [ "$HTTP_CODE" != "200" ]; then
  echo "[sync-roo-key] 密钥可能无效或余额/权限问题，请检查 SiliconFlow 控制台"
  head -c 200 /tmp/sf_test.json 2>/dev/null || true
  echo
  unset SYNC_KEY KEY || true
  exit 1
fi

unset SYNC_KEY KEY || true
echo "[sync-roo-key] OK。请在 Cursor Reload Window，Roo 里点「重试」。"
