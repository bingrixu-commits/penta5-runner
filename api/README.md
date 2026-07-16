# Penta5 RDS API（existing-v71）

独立只读 API，默认监听 `127.0.0.1:8787`。开奖主库在阿里 RDS；`penta5-runner`（3001）先写 RDS 再灌注内存。

## 端点

- `GET /health`
- `GET /history?limit=100&offset=0`
- `GET /state`（从 RDS 组装精简状态）

## Schema

- `schema.sql` 与线上 **existing-v71** 对齐（`state_key` / `counter_key` / `ball5_display`）。
- 空库初始化：`node init_schema.js --force`
- 已有表时默认跳过，避免误建旧 phase-1 列名。核对：`node dump_ddl.js` / `node probe_schema.js`

## 阿里部署

```bash
cd /root/penta5-runner/api
npm install
# 仅空库需要：node init_schema.js --force
pm2 start server.js --name penta5-rds-api --cwd /root/penta5-runner/api
curl -s http://127.0.0.1:8787/health
```

连接信息来自 `/root/penta5-runner/.env` 的 `RDS_*`，不要把密码写入本目录。
