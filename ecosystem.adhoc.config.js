/**
 * 2026-10-30 整改新增:此前 penta5-rds-api / penta5-panel-sync 是手工
 * `pm2 start` 启动的,配置不在版本控制里(无法追溯、无内存上限)。
 * 此文件固化其定义。注意:penta5-panel-sync 跑的是 sync_state_to_panel.py,
 * 属于"跑完即退出 + pm2 再拉起"的批处模式,重启计数增长是预期行为。
 */
module.exports = {
  apps: [
    {
      name: "penta5-rds-api",
      cwd: "/root/penta5-runner",
      script: "api/server.js",
      interpreter: "node",
      time: true,
      autorestart: true,
      max_memory_restart: "512M"
    },
    {
      name: "penta5-panel-sync",
      cwd: "/root",
      script: "/root/penta5-runner/sync_state_to_panel.py",
      interpreter: "python3",
      time: true,
      autorestart: true,
      max_memory_restart: "256M"
    }
  ]
};
