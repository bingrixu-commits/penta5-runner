/**
 * pm2: penta5-supervisor
 * 阿里常驻监督：核心进程在线 + RDS/代理/面板健康 + 模型降智升级（sync → restart）
 *
 * 启动：
 *   pm2 start ecosystem.supervisor.config.js
 *   pm2 save
 *
 * 环境变量（可选，默认已够用）：
 *   PENTA5_SUPERVISOR_INTERVAL=90
 *   PENTA5_SUPERVISOR_RESTART_COOLDOWN=300
 *   PENTA5_SUPERVISOR_INTEGRITY_FAILS=3
 *   PENTA5_SUPERVISOR_WATCHDOG_ERRORS=2
 *
 * 面板鉴权：从 /root/penta5-runner/.env 读取 PANEL_*，禁止在本文件硬编码密码。
 */
const fs = require("fs");
const path = require("path");

function loadDotEnv(filePath) {
  const out = {};
  try {
    if (!fs.existsSync(filePath)) return out;
    const text = fs.readFileSync(filePath, "utf8");
    for (const rawLine of text.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || line.indexOf("=") <= 0) continue;
      const eq = line.indexOf("=");
      const key = line.slice(0, eq).trim();
      let value = line.slice(eq + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      out[key] = value;
    }
  } catch (_) {}
  return out;
}

const ROOT = "/root/penta5-runner";
const dotenv = loadDotEnv(path.join(ROOT, ".env"));
const envGet = (key, fallback) => {
  if (process.env[key] !== undefined && process.env[key] !== "") return process.env[key];
  if (dotenv[key] !== undefined && dotenv[key] !== "") return dotenv[key];
  return fallback;
};

module.exports = {
  apps: [
    {
      name: "penta5-supervisor",
      cwd: ROOT,
      script: "penta5_supervisor.py",
      interpreter: "python3",
      args: "--loop --interval 90",
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 50,
      min_uptime: "10s",
      kill_timeout: 8000,
      env: {
        PENTA5_RUNNER_ROOT: ROOT,
        PENTA5_SUPERVISOR_INTERVAL: "90",
        PENTA5_SUPERVISOR_RESTART_COOLDOWN: "300",
        PENTA5_SUPERVISOR_INTEGRITY_FAILS: "5",
        PENTA5_SUPERVISOR_WATCHDOG_ERRORS: "2",
        PENTA5_SUPERVISOR_SYNC_COOLDOWN: "180",
        // 启动灌注/换码会阻塞事件循环数十秒，过短会导致误判 timeout→重启风暴
        PENTA5_SUPERVISOR_HTTP_TIMEOUT: "90",
        PENTA5_SUPERVISOR_RDS_LAG_PERIODS: "3",
        PENTA5_SUPERVISOR_LOG: path.join(ROOT, "data/penta5_supervisor.log"),
        PENTA5_SUPERVISOR_STATE: path.join(ROOT, "data/penta5_supervisor_state.json"),
        // 与 runner/.env 对齐：Basic 鉴权探测，避免无鉴权 401→误重启
        PANEL_USER: envGet("PANEL_USER", "admin"),
        PANEL_PASSWORD: envGet("PANEL_PASSWORD", ""),
        PANEL_VIEWER_USER: envGet("PANEL_VIEWER_USER", "viewer"),
        PANEL_VIEWER_PASSWORD: envGet("PANEL_VIEWER_PASSWORD", ""),
        // 可选：钉钉/企微/飞书 webhook；也可用阿里 .env 的 PENTA5_ALERT_WEBHOOK_URL
        // PENTA5_ALERT_WEBHOOK_URL: "",
        PENTA5_STORM_RESUME_ROBOTS: "0",
        PENTA5_ALERT_COOLDOWN: "180",
      },
    },
  ],
};
