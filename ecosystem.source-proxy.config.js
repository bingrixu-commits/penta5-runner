/**
 * DEPRECATED — 不要再启动本配置。
 * 开奖源已改为 runner 直连 .env 的 SOURCE_API_URL（官网 latest）。
 * install_backend.sh 会 pm2 delete penta5-source-proxy。
 * 保留文件仅作历史对照；若误启动请立即 pm2 delete penta5-source-proxy。
 *
 * 旧启动方式（已废弃）：
 *   pm2 start ecosystem.source-proxy.config.js
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
