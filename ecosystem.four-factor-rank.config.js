/**
 * 四因子排码（90期加权频次+短期热度+一阶转移+弱遗漏）
 * 用法: pm2 start ecosystem.four-factor-rank.config.js
 */
module.exports = {
  apps: [
    {
      name: "four-factor-rank",
      cwd: "/root/penta5-runner",
      script: "algorithms/kill/four-factor-rank/sidecar.js",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "96M",
      max_restarts: 20,
      min_uptime: "15s",
      restart_delay: 4000,
      out_file: "/root/.pm2/logs/four-factor-rank-out.log",
      error_file: "/root/.pm2/logs/four-factor-rank-error.log",
      env: {
        NODE_ENV: "production",
        FOUR_FACTOR_POLL_MS: "60000",
        FOUR_FACTOR_HISTORY_LIMIT: "200",
        SHAPE_HISTORY_URL: "http://127.0.0.1:8787/history?limit=200"
      }
    }
  ]
};
