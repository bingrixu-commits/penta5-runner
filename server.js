"use strict";

/**
 * 根目录占位入口：真实 RDS API 在 api/server.js。
 * 误在仓库根目录执行 `node server.js` 时给出明确指引，避免 require("./db") 失败。
 */
const path = require("path");

console.error([
  "[penta5] 请从 api 目录启动 RDS API：",
  "  cd /root/penta5-runner/api && node server.js",
  "或：",
  "  pm2 start server.js --name penta5-rds-api --cwd /root/penta5-runner/api",
  "",
  `目标文件：${path.join(__dirname, "api", "server.js")}`
].join("\n"));
process.exit(1);
