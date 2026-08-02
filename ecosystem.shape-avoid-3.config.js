/**
 * 形态避开3组·四定（千百十个，对标对数避开3组）
 * 用法: pm2 start ecosystem.shape-avoid-3.config.js
 */
module.exports = {
  apps: [
    {
      name: "shape-avoid-3",
      cwd: "/root/penta5-runner",
      script: "algorithms/kill/shape-avoid-3/sidecar.js",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "96M",
      max_restarts: 20,
      min_uptime: "15s",
      restart_delay: 4000,
      out_file: "/root/.pm2/logs/shape-avoid-3-out.log",
      error_file: "/root/.pm2/logs/shape-avoid-3-error.log",
      env: {
        NODE_ENV: "production",
        SHAPE_AVOID_POLL_MS: "60000",
        SHAPE_AVOID_GROUPS: "3",
        SHAPE_WIDTH: "4",
        SHAPE_HISTORY_LIMIT: "1500",
        MODEL_LONG_WINDOW: "1000",
        MODEL_STABLE_WINDOW: "1000",
        SHAPE_HISTORY_URL: "http://127.0.0.1:8787/history?limit=1500"
      }
    }
  ]
};
