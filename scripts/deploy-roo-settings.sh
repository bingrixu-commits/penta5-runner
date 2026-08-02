#!/usr/bin/env bash
set -euo pipefail

SETTINGS_SRC="/root/penta5-runner/.roo/roo-code-settings.json"
CURSOR_BIN="/root/.cursor-server/bin/linux-x64/31e8d61c448c7472e371505838a0fe34083dad50/bin/remote-cli/cursor"
VSIX_URL="https://marketplace.cursorapi.com/open-vsx-mirror/api/RooVeterinaryInc/roo-cline/3.54.0/file/RooVeterinaryInc.roo-cline-3.54.0.vsix"
VSIX_LOCAL="/tmp/roo-cline.vsix"
CACHE_DIR="/root/.cursor-server/data/CachedExtensionVSIXs"

echo "[deploy] Roo Code 配置部署开始"

mkdir -p /root/.roo/rules /root/penta5-runner/.roo/rules
cp -f "$SETTINGS_SRC" /root/.roo/roo-code-settings.json
chmod 444 "$SETTINGS_SRC" /root/.roo/roo-code-settings.json

for f in \
  /root/.vscode/settings.json \
  /root/penta5-runner/.vscode/settings.json \
  /root/.cursor-server/data/User/settings.json \
  /root/.cursor-server/data/Machine/settings.json
do
  mkdir -p "$(dirname "$f")"
  cat > "$f" <<'EOF'
{
  "roo-cline.autoImportSettingsPath": "/root/penta5-runner/.roo/roo-code-settings.json",
  "roo-cline.useAgentRules": true,
  "roo-cline.apiRequestTimeout": 600
}
EOF
done

if [ ! -f "$VSIX_LOCAL" ]; then
  echo "[deploy] 下载 Roo Code VSIX..."
  curl -4 -L --connect-timeout 30 --max-time 600 -o "$VSIX_LOCAL" "$VSIX_URL"
fi

mkdir -p "$CACHE_DIR"
cp -f "$VSIX_LOCAL" "$CACHE_DIR/rooveterinaryinc.roo-cline-3.54.0-universal.vsix"
"$CURSOR_BIN" --install-extension "$VSIX_LOCAL" --force

echo "[deploy] 完成。请在 Cursor 中 Reload Window 使配置生效。"
