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
- **cuopt（已停用，见文末）**：远端 `http://8.138.155.17:8788` 的机器不属于本账号，
  2026-10-30 已把 `PENTA5_CUOPT_URL` 置空，auto-top6 走 JS 穷举回退；无需再配超时。
- **证据异地副本（本机 Windows）**：`ops/fetch-evidence-offsite.ps1`（在本机 DSH
  workspace 的 ops/ 下）用现有 aliyun 私钥 scp 最新 verified 归档到
  `..\evidence-offsite\`，校验 sha256、保留最近 4 份、上传 marker 到服务器
  `/root/evidence-archives/offsite-verified/`。已登录时由启动项
  `DSH-EvidenceOffsite.cmd` 触发（6 天节流）；如需固定每周一 06:30，用管理员
  CMD 执行：
  ```
  schtasks /Create /SC WEEKLY /D MON /ST 06:30 /TN "DSH-EvidenceOffsite" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"D:\文档\deepseek-harness\default-workspace\ops\fetch-evidence-offsite.ps1\"" /F
  ```
- **selected_six 写放大重构（未完成，勿误认已上线）**：2026-10-30 已完成
  baseline（冷冻快照冷算 146–153s/轮，seals 309MB + hit_stats 157MB 全量重写）并写出
  一个默认关闭的 append-only 分片存储原型（见
  `/root/archive/wip-20261030/selected_six_store.wip.py`：base.json + seals.jsonl +
  attempts.jsonl，`PENTA5_SIX_SHARDED=1` 才启用）。该原型**未接线、未做逐字节等价
  证明**（实施窗口内 teammate 中止），因此没有进仓库、生产完全走旧路径。后续切换需：
  影子双写→等价比对→更新 evidence/REQUIRED_DATA 与 manifest→单期切换→回滚点。
- **cuopt 外部依赖已停用（2026-10-30 确认）**：`8.138.155.17` 属阿里云广州，但用户
  没有这台服务器（安全组/主机都不是我们的）。pm2 运行时与 `ecosystem.runner.config.js`
  的 `PENTA5_CUOPT_URL` 已置空（`""`），auto-top6 立即走 JS 穷举回退（与原先失败
  回退结果一致、不再白等 2×3s）；`PENTA5_CUOPT_TIMEOUT_MS` 恢复 3000，
  `PENTA5_AUTOTOP6_MILP` 仍为 `cuopt`。`scripts/ops_health_check.py` 把
  `cuopt-url-unset` 判为“已停用 OK”。若以后真部署了 cuopt 服务：填回 URL →
  `pm2 restart penta5-runner --update-env && pm2 save` 即可。
- **2026-10-30 成本控制：暂停 4 个统计型 AI 任务的 cron**（`selected_six`、`cold_framework`、
  `cold_fixed_framework`、`he3_refine`）。原因：这三项不是纯统计（会调 DeepSeek v4-pro），其中
  cold_fixed 每期还要 3 次校验重试，是余额消耗主因；用户要求统计型模型不再调用 API。
  处理：crontab 移除 4 行（25→21），创建 `data/ai_api_paused.json` 标记暂停，
  `scripts/ops_health_check.py` 对 paused 任务跳过 lag 告警（显示 paused/OK）；
  其余仍保留：framework、killer 两个 AI 主推（flash，费用极低）+ 所有本地统计/回测。
  面板上这 3 个区块会**冻结在最后发布期 099**，不再更新预测。
  恢复：`git log -p ops/crontab.txt` 找回 3 行 → 加回 crontab（或 `crontab_tool.sh install`
  历史版本），删除 `data/ai_api_paused.json`，等下一期即可。
- **纯统计任务本来就不调 API**（无成本）：stat50 对照、cold_backtest、strategy_settled、
  model_daily、model_significance、real_hitrate、cold_hit_stats、kill_three、
  position_cold 等。
