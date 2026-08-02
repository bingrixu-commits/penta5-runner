/**
 * penta5-runner 持久启动配置（堆上限 / cwd）
 * 用法: pm2 start ecosystem.runner.config.js
 */
module.exports = {
  apps: [
    {
      name: "penta5-runner",
      cwd: "/root/penta5-runner",
      script: "penta5_backend_runner.js",
      interpreter: "node",
      node_args: "--max-old-space-size=2048",
      time: true,
      autorestart: true,
      max_restarts: 20,
      env: {
        NODE_HEAP_MB: "2048",
        NODE_OPTIONS: "--max-old-space-size=2048",
        // 主链路默认 in-process orchestrate（与 AGENTS / 门道修复一致）
        PENTA5_ALGO_PROCESS_MODE: "0",
        PENTA5_ORCHESTRATE_FORCE_INPROCESS: "1",
        QUALIFIED_PICKS_WINDOW: "100",
        QUALIFIED_PICKS_MIN_RATE: "92",
        QUALIFIED_PICKS_EXIT_MIN_RATE: "85",
        QUALIFIED_PICKS_MIN_LOCK_PERIODS: "24",
        QUALIFIED_PICKS_STICKY: "1",
        QUALIFIED_PICKS_DUAL_ENABLED: "1",
        QUALIFIED_PICKS_DUAL_MIN_RATE: "90",
        QUALIFIED_PICKS_DUAL_EXIT_MIN_RATE: "86",
        QUALIFIED_PICKS_MAX_SHOW_STREAK: "0",
        QUALIFIED_PICKS_MAX_ENTRY_CONSECUTIVE: "0",
        QUALIFIED_PICKS_FAIL_COOLDOWN: "6",
        QUALIFIED_PICKS_ENTRY_LOOKBACK: "12"
      }
    }
  ]
};
