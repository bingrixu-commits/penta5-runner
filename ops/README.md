# ops/ — reviewed operational baselines

| file | purpose |
|---|---|
| `crontab.txt` | Reviewed root-crontab snapshot. Install with `scripts/crontab_tool.sh install`; check drift with `scripts/crontab_tool.sh diff` (also reported by `scripts/ops_health_check.py` as `crontab_drift`). |
| `../scripts/ops_health_check.py` | 19-check read-only probe (evidence age, AI-job lag, module lag, panel freshness, manifest drift, disk/mem, pm2 restart deltas, cuopt, tmp leftovers, crontab drift). Writes `data/ops_health.json`, `data/ops_health_state.json`; appends transitions to `data/ops_alerts.log`. Cron: `*/10 * * * *`. |
| `../scripts/refresh_source_manifest.py` | Rebuilds `/root/backups/<auto-baseline-*>/verification-manifest.json` when live source bytes no longer match any manifest. `--if-drift` is cron-safe (no write when clean). Cron: `55 4 * * *`, 14 min before the 05:09 evidence backup. |

## Notes

- `sync_state_to_panel.py` refreshes panel statistics **once per settled draw**
  (`latestRecord.period` changed + 180s), not continuously; prediction cards
  are written directly by each job. Heartbeat 600s, business-day switch,
  failure retry 120s.
- Heavy `*/5` jobs are intentionally **not** staggered away from their draw
  window: `ai_operation_window.PLATFORM_CLOSE_SECONDS = 70` closes a period at
  draw + 230s, so a job scheduled at draw + >3 min is rejected with
  `operation-window-insufficient-budget` (observed 2026-10-30 07:08 for he3).
- The evidence backup needs a matching source manifest; whenever the tree
  changes after the last baseline, either run
  `python3 scripts/refresh_source_manifest.py --root /root/penta5-runner --if-drift`
  or wait for the 04:55 cron.

## 2026-10-30 补充

- **退役数据**：`data/selected_framework_*.json`、`data/pro_compare_*.json` 已归档到
  `/root/archive/retired-20261030/`（gzip 共 12MB，原始 93MB）。evidence_backup 的
  `REQUIRED_DATA` 已移除两项，strategy_comparison 不再读取 legacy `'selected'` 账本
  （该行本就不在 `DISPLAY_MODELS` 展示）。如需找回：`gunzip` 回 `data/` 并回退该 commit。
- **framework 按模型统计**：`scripts/model_daily_report.py` 输出
  `framework_by_model`（依 `r1_framework_seals.json` 的
  `model_metadata.requested_model` 拆分 flash / v4-pro 的今日/近3日/全期命中率）。
- **cuopt**：远端 `http://8.138.155.17:8788` 不可达期间，pm2 运行时
  `PENTA5_CUOPT_TIMEOUT_MS=800`（原 3000）；服务恢复后仍会自动调用，改回即
  `PENTA5_CUOPT_TIMEOUT_MS=3000 pm2 restart penta5-runner --update-env && pm2 save`。
- **证据异地副本（本机 Windows）**：`ops/fetch-evidence-offsite.ps1`（在本机 DSH
  workspace 的 ops/ 下）用现有 aliyun 私钥 scp 最新 verified 归档到
  `..\evidence-offsite\`，校验 sha256、保留最近 4 份、上传 marker 到服务器
  `/root/evidence-archives/offsite-verified/`。已登录时由启动项
  `DSH-EvidenceOffsite.cmd` 触发（6 天节流）；如需固定每周一 06:30，用管理员
  CMD 执行：
  ```
  schtasks /Create /SC WEEKLY /D MON /ST 06:30 /TN "DSH-EvidenceOffsite" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"D:\文档\deepseek-harness\default-workspace\ops\fetch-evidence-offsite.ps1\"" /F
  ```
