module.exports = {
  apps: [
    {
      name: "claude-ensemble",
      cwd: "/root/penta5-runner",
      script: "algorithms/ai/claude-ensemble/sidecar.js",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "512M",
      max_restarts: 50,
      min_uptime: "30s",
      restart_delay: 5000,
      out_file: "/root/.pm2/logs/claude-ensemble-out.log",
      error_file: "/root/.pm2/logs/claude-ensemble-error.log",
      env: {
        NODE_ENV: "production",
        CLAUDE_ENSEMBLE_WEB_PORT: "3002",
        CLAUDE_ENSEMBLE_LATEST_URL: "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest",
        CLAUDE_ENSEMBLE_HISTORY_URL: "http://127.0.0.1:8787/history?limit=3400",
        CLAUDE_ENSEMBLE_LONG_WINDOW: "3000",
        CLAUDE_ENSEMBLE_POOL_SIZE: "900"
      }
    }
  ]
};
