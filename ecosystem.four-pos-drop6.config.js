/**
 * 四定共丢6码·剩余出号（反车页用）
 * 用法: pm2 start ecosystem.four-pos-drop6.config.js
 * 一次: node algorithms/kill/four-pos-drop6/sidecar.js --once
 */
module.exports = {
  apps: [
    {
      name: "four-pos-drop6",
      cwd: "/root/penta5-runner",
      script: "algorithms/kill/four-pos-drop6/sidecar.js",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "96M",
      max_restarts: 20,
      min_uptime: "15s",
      restart_delay: 4000,
      out_file: "/root/.pm2/logs/four-pos-drop6-out.log",
      error_file: "/root/.pm2/logs/four-pos-drop6-error.log",
      env: {
        NODE_ENV: "production",
        FOUR_POS_DROP_POLL_MS: "60000",
        FOUR_POS_DROP_N: "6",
        FOUR_POS_DROP_HISTORY_LIMIT: "1500",
        MODEL_LONG_WINDOW: "1000",
        MODEL_STABLE_WINDOW: "1000",
        SHAPE_HISTORY_URL: "http://127.0.0.1:8787/history?limit=1500"
      }
    }
  ]
};
