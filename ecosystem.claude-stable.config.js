module.exports = {
  apps: [
    {
      name: "claude-stable",
      cwd: "/root/penta5-runner",
      script: "algorithms/ai/claude-stable/sidecar.js",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "200M",
      max_restarts: 10,
      min_uptime: "30s",
      restart_delay: 5000,
      out_file: "/root/.pm2/logs/claude-stable-out.log",
      error_file: "/root/.pm2/logs/claude-stable-error.log",
      env: {
        NODE_ENV: "production",
        CLAUDE_STABLE_WEB_PORT: "3003",
        CLAUDE_STABLE_LATEST_URL: "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest",
        CLAUDE_STABLE_HISTORY_URL: "http://127.0.0.1:8787/history?limit=3400",
        CLAUDE_ENSEMBLE_LONG_WINDOW: "3000",
        CLAUDE_ENSEMBLE_ITEM_COUNT: "6",
        CLAUDE_ENSEMBLE_MIN_ITEM_SAMPLE: "100",
        CLAUDE_ENSEMBLE_MIN_ITEM_LOWER: "86",
        CLAUDE_ENSEMBLE_MIN_POSITION_LOWER: "88",
        CLAUDE_ENSEMBLE_ITEM_WEIGHT: "0.75",
        CLAUDE_ENSEMBLE_VOTE_BONUS: "0.8",
        CLAUDE_ENSEMBLE_VOTE_BONUS_CAP: "3"
      }
    }
  ]
};
