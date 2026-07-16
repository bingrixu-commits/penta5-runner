/**
 * pm2: penta5-source-proxy
 * 运行入口：remote_state_proxy.py（替代 source_proxy.js）
 *
 * 启动：
 *   pm2 start ecosystem.source-proxy.config.js
 *   pm2 save
 */
module.exports = {
  apps: [
    {
      name: "penta5-source-proxy",
      cwd: "/root/penta5-runner",
      script: "remote_state_proxy.py",
      interpreter: "python3",
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 30,
      min_uptime: "5s",
      kill_timeout: 5000,
      env: {
        LOCAL_PROXY_HOST: "0.0.0.0",
        LOCAL_PROXY_PORT: "3000",
        // 与旧 source_proxy.js 同源官方开奖接口；禁止默认回环读本机 /state
        OFFICIAL_LATEST_URL:
          "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest",
        REMOTE_REQUEST_TIMEOUT: "10",
        PROXY_MAX_STALE_SECONDS: "180",
        PROXY_ALLOW_STATE_FALLBACK: "0",
        REMOTE_PROXY_CACHE_FILE: "/root/penta5-runner/data/proxy_latest_cache.json",
      },
    },
  ],
};
