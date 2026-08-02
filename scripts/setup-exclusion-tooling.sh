#!/usr/bin/env bash
# 排除模型实战三件套：Ruff + SQLTools(MySQL) + Error Lens
set -euo pipefail

ROOT="/root/penta5-runner"
CURSOR_BIN="$(find /root/.cursor-server/bin -path '*/bin/remote-cli/cursor' 2>/dev/null | sort | tail -1)"

if [ -z "${CURSOR_BIN}" ] || [ ! -x "${CURSOR_BIN}" ]; then
  echo "[exclusion-setup] 未找到 cursor CLI"
  exit 1
fi

echo "[exclusion-setup] cursor=$CURSOR_BIN"

for id in \
  charliermarsh.ruff \
  mtxr.sqltools \
  mtxr.sqltools-driver-mysql \
  usernamehw.errorlens
do
  echo "[exclusion-setup] install $id"
  "$CURSOR_BIN" --install-extension "$id" --force
done

node <<'NODE'
const fs = require("fs");
const path = require("path");

const root = "/root/penta5-runner";
const envPath = path.join(root, ".env");
const env = {};
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) continue;
    const i = t.indexOf("=");
    const k = t.slice(0, i).trim();
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    env[k] = v;
  }
}

const required = ["RDS_HOST", "RDS_USER", "RDS_PASSWORD", "RDS_DATABASE"];
for (const k of required) {
  if (!env[k]) throw new Error("Missing .env key: " + k);
}

const settingsPath = path.join(root, ".vscode", "settings.json");
const settings = {
  "roo-cline.autoImportSettingsPath": "/root/penta5-runner/.roo/roo-code-settings.json",
  "roo-cline.useAgentRules": true,
  "roo-cline.apiRequestTimeout": 600,
  "python.defaultInterpreterPath": "/usr/bin/python3",
  "python.analysis.typeCheckingMode": "basic",
  "python.analysis.autoImportCompletions": true,
  "python.terminal.activateEnvironment": false,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": false,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": "explicit",
      "source.organizeImports.ruff": "explicit"
    }
  },
  "ruff.nativeServer": true,
  "ruff.configuration": "${workspaceFolder}/ruff.toml",
  "eslint.useFlatConfig": true,
  "eslint.workingDirectories": [{ "mode": "auto" }],
  "eslint.validate": ["javascript"],
  "editor.codeActionsOnSave": {
    "source.fixAll.eslint": "explicit"
  },
  "errorLens.enabled": true,
  "errorLens.enabledDiagnosticLevels": ["error", "warning"],
  "errorLens.excludeBySource": [],
  "rest-client.previewOption": "body",
  "rest-client.followRedirect": true,
  "rest-client.timeoutinms": 60000,
  "files.associations": {
    "api.http": "http",
    "*.sql": "sql"
  },
  "gitlens.codeLens.enabled": true,
  "gitlens.currentLine.enabled": false,
  "sqltools.useNodeRuntime": true,
  "sqltools.autoConnectTo": ["Penta5 RDS · 排除核对"],
  "sqltools.connections": [
    {
      "name": "Penta5 RDS · 排除核对",
      "driver": "MySQL",
      "previewLimit": 50,
      "server": env.RDS_HOST,
      "port": Number(env.RDS_PORT || 3306),
      "database": env.RDS_DATABASE,
      "username": env.RDS_USER,
      "password": env.RDS_PASSWORD,
      "mysqlOptions": {
        "authProtocol": "default",
        "enableSsl": "Disabled"
      },
      "connectionTimeout": 30
    }
  ]
};

fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
// settings.json 已被 .gitignore；密码仅写本地，勿入库
fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n", { mode: 0o600 });
try { fs.chmodSync(settingsPath, 0o600); } catch (_) {}

for (const dest of [
  "/root/.vscode/settings.json",
  "/root/.cursor-server/data/User/settings.json",
  "/root/.cursor-server/data/Machine/settings.json",
]) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, fs.readFileSync(settingsPath), { mode: 0o600 });
  try { fs.chmodSync(dest, 0o600); } catch (_) {}
}

console.log("[exclusion-setup] settings written (gitignored, mode 600), SQLTools host:", env.RDS_HOST);
NODE

echo "[exclusion-setup] 完成。请 Reload Window，然后在 SQLTools 连接 Penta5 RDS · 排除核对。"
echo "[exclusion-setup] 提示：.vscode/settings.json 含库密，已 gitignore，勿手动 git add。"
