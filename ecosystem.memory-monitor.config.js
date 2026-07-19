/**
 * pm2: penta5-memory-monitor
 * 每 10 分钟记录 penta5 相关进程 RSS 到 memory.log
 *
 * 启动：
 *   pm2 start ecosystem.memory-monitor.config.js
 *   pm2 save
 */
const path = require("path");

const ROOT = "/root/penta5-runner";

module.exports = {
  apps: [
    {
      name: "penta5-memory-monitor",
      cwd: ROOT,
      script: path.join(ROOT, "monitor_memory.sh"),
      interpreter: "bash",
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "30s",
      kill_timeout: 5000,
      env: {
        MONITOR_INTERVAL_SEC: "600",
      },
    },
  ],
};
