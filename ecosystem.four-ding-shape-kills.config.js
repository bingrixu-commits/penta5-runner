/**
 * 四定杀码形态（大小 + 单双）旁路
 * 用法: pm2 start ecosystem.four-ding-shape-kills.config.js
 */
module.exports = {
  apps: [
    {
      name: "four-ding-shape-kills",
      cwd: "/root/penta5-runner",
      script: "algorithms/kill/four-ding-shape-kills/sidecar.js",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "96M",
      max_restarts: 20,
      min_uptime: "15s",
      restart_delay: 4000,
      out_file: "/root/.pm2/logs/four-ding-shape-kills-out.log",
      error_file: "/root/.pm2/logs/four-ding-shape-kills-error.log",
      env: {
        NODE_ENV: "production",
        FOUR_DING_SHAPE_POLL_MS: "60000",
        SHAPE_HISTORY_LIMIT: "1500",
        SHAPE_TOP_N: "5",
        SHAPE_HISTORY_URL: "http://127.0.0.1:8787/history?limit=1500"
      }
    }
  ]
};
