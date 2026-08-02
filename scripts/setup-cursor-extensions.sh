#!/usr/bin/env bash
# 安装/保留推荐扩展，卸载与 penta5 无关的扩展，并写入 Cursor/VS Code 配置。
set -euo pipefail

ROOT="/root/penta5-runner"
CURSOR_BIN="$(find /root/.cursor-server/bin -path '*/bin/remote-cli/cursor' 2>/dev/null | sort | tail -1)"
ROO_VSIX_URL="https://marketplace.cursorapi.com/open-vsx-mirror/api/RooVeterinaryInc/roo-cline/3.54.0/file/RooVeterinaryInc.roo-cline-3.54.0.vsix"
ROO_VSIX_LOCAL="/tmp/roo-cline.vsix"
CACHE_DIR="/root/.cursor-server/data/CachedExtensionVSIXs"

if [ -z "${CURSOR_BIN}" ] || [ ! -x "${CURSOR_BIN}" ]; then
  echo "[setup] 未找到 cursor CLI，跳过扩展安装/卸载"
  exit 1
fi

echo "[setup] cursor=$CURSOR_BIN"

KEEP=(
  rooveterinaryinc.roo-cline
  anysphere.cursorpyright
  ms-python.python
  ms-python.debugpy
  ms-python.black-formatter
  dbaeumer.vscode-eslint
  humao.rest-client
  ms-ceintl.vscode-language-pack-zh-hans
  eamodio.gitlens
)

REMOVE=(
  mt-idekit.mt-idekit-code
  salesforce.salesforcedx-vscode-apex
  salesforce.salesforcedx-vscode-apex-log
  salesforce.salesforcedx-vscode-apex-replay-debugger
  salesforce.salesforcedx-vscode-services
  ms-toolsai.jupyter
  ms-toolsai.jupyter-keymap
  ms-toolsai.jupyter-renderers
  ms-toolsai.vscode-jupyter-cell-tags
  ms-toolsai.vscode-jupyter-slideshow
  ms-playwright.playwright
  vitest.explorer
  currents.currents
  llvm-vs-code-extensions.lldb-dap
  esbenp.prettier-vscode
  gruntfuggly.todo-tree
  usernamehw.errorlens
  christian-kohler.path-intellisense
)

for id in "${REMOVE[@]}"; do
  echo "[setup] uninstall $id"
  "$CURSOR_BIN" --uninstall-extension "$id" >/dev/null 2>&1 || true
done

mkdir -p "$CACHE_DIR" /root/.roo/rules "$ROOT/.roo/rules"
cp -f "$ROOT/.roo/roo-code-settings.json" /root/.roo/roo-code-settings.json

if [ ! -f "$ROO_VSIX_LOCAL" ]; then
  echo "[setup] 下载 Roo Code VSIX..."
  curl -4 -L --connect-timeout 30 --max-time 600 -o "$ROO_VSIX_LOCAL" "$ROO_VSIX_URL"
fi
cp -f "$ROO_VSIX_LOCAL" "$CACHE_DIR/rooveterinaryinc.roo-cline-3.54.0-universal.vsix"
"$CURSOR_BIN" --install-extension "$ROO_VSIX_LOCAL" --force

for id in \
  ms-python.python \
  ms-python.debugpy \
  ms-python.black-formatter \
  dbaeumer.vscode-eslint \
  humao.rest-client \
  ms-ceintl.vscode-language-pack-zh-hans \
  eamodio.gitlens
do
  echo "[setup] install $id"
  "$CURSOR_BIN" --install-extension "$id" --force
done

SETTINGS_JSON="$ROOT/.vscode/settings.json"
cat > "$SETTINGS_JSON" <<'EOF'
{
  "roo-cline.autoImportSettingsPath": "/root/penta5-runner/.roo/roo-code-settings.json",
  "roo-cline.useAgentRules": true,
  "roo-cline.apiRequestTimeout": 600,
  "python.defaultInterpreterPath": "/usr/bin/python3",
  "python.analysis.typeCheckingMode": "basic",
  "python.analysis.autoImportCompletions": true,
  "python.terminal.activateEnvironment": false,
  "[python]": {
    "editor.defaultFormatter": "ms-python.black-formatter",
    "editor.formatOnSave": false
  },
  "eslint.useFlatConfig": true,
  "eslint.workingDirectories": [{ "mode": "auto" }],
  "eslint.validate": ["javascript"],
  "editor.codeActionsOnSave": {
    "source.fixAll.eslint": "explicit"
  },
  "rest-client.previewOption": "body",
  "rest-client.followRedirect": true,
  "rest-client.timeoutinms": 60000,
  "files.associations": {
    "api.http": "http"
  },
  "gitlens.codeLens.enabled": true,
  "gitlens.currentLine.enabled": false
}
EOF

for f in \
  /root/.vscode/settings.json \
  /root/.cursor-server/data/User/settings.json \
  /root/.cursor-server/data/Machine/settings.json
do
  mkdir -p "$(dirname "$f")"
  cp -f "$SETTINGS_JSON" "$f"
done

echo "[setup] 扩展与配置完成。请在 Cursor 执行 Reload Window。"
