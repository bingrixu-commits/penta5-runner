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
- **2026-10-30 08:31 合尾(he3_refine)恢复并改用 v4-pro**：用户认为合尾重要，要求用好的
  模型。`lib/he3_refine.py` 的 MODEL 改为可配置(`PENTA5_MODEL_HE3`，未配置时仍 flash)，
  `.env` 设 `PENTA5_MODEL_HE3=deepseek-v4-pro`，cron 恢复。he3 每次 prompt 仅 ~640
  tokens、每天约 50 次调用，pro 成本约 0.5 元/天级；其余 3 个统计型任务
  (selected_six/cold_framework/cold_fixed_framework) 仍暂停(见 data/ai_api_paused.json)。
- **2026-10-30 08:48 合尾(he3_refine)切 pro 的两个配套修复**：
  1) `_validate_saved` 原来把 `returned_model` 硬编码为 flash 白名单，切 pro 会报
     `unverified-refinement-model`；已改成"模型族一致"校验（flash 族/pro 族，历史
     flash 封存仍合法、新 pro 封存也合法，跨族降级/升级仍拒）。
  2) he3 cron 的 `flock -n` 改为 `flock -w 30`：run_stats 刷新末尾会短暂占用
     `/var/lock/penta5-he3.lock`（he3_extra 阶段），`-n` 会让 he3 静默跳过整期；
     改为最多等 30s 再执行。手动验证 ref=105 已用 v4-pro 成功发布。
- **2026-10-30 08:57 恢复 3 个暂停任务（用户要求）**：selected_six、cold_framework、
  cold_fixed_framework 的 cron 已按 git 历史原样加回（crontab 22→25 行），删除
  data/ai_api_paused.json，ops_health 恢复正常 lag 检查。注意：08:59-09:10 账户余额
  一度归零，所有 AI 任务（含 framework/killer/he3）在该窗口 402，期号 107-110 缺口
  无法回补；09:12 充值后自动恢复，无需重启。
- **已知待优化（不影响出数）**：cold_fixed_framework 每期会用 3 次尝试才通过
  （前两次 `strategy-response-cold-he3-parity` 校验被拒，第 3 次才过），token 约 3 倍；
  若要优化需查 pro 模型对 he3_keep 单双约束的遵循率（可加更强的重试反馈或本地校正）。
- **2026-10-30 09:23 cold_fixed/cold_framework 的 he3 单双校验改为本地兜底（省 3 倍 token）**：
  诊断发现 deepseek-v4-pro 对「he3_keep 必须 2 单 3 双或 3 单 2 双」的硬约束遵循率只有约
  1/3（实测连续 3 次输出 01234/02468/04568，仅第一次合规），原先靠 3 次重试硬扛：
  cold_fixed 今日 1033 次请求 vs 684 期发布、其中 200 次 parity 拒绝。现改为在
  `checked_selection` 里捕获 `strategy-response-cold-he3-parity` 后，用
  `core.repair_he3_keep()` 把模型自己的 5 个数字确定性投影到最近的合法均衡集合
  （最大重合、字典序破同，纯本地、不额外调 API），再次校验通过即接受，并在
  cron 日志记 `COLD_HE3_PARITY_REPAIRED ref=... from=... to=...`。
  封存 schema/selection_policy 不变，只有审计日志多一行；仅当修复后仍不合法
  （如 he2/字段错误）才走原来的重试。
- **2026-10-30 09:32 三位置杀码建议加「当前连中/连败」**：原面板只显示历史极值
  （最多连中/最多连败），看不到规则当下状态。scripts/kill_three_pick.py 的
  streaks() 额外返回回测序列末尾仍在延续的 current_win/current_loss，JSON
  backtest 加 current_win_streak/current_loss_streak；lib/kill_three_view.py 增加
  「当前 连中 N 手（进行中）」绿色 /「连败」红色展示。position-cold 是 systemd
  守护(penta5-position-cold.service)调用 kill_three_pick.main()，已重启加载新代码。
- **2026-10-30 09:56「当前连中/连败」继续铺到冷码杀号战绩**：其他区块（framework/八项/
  cold/he3/kill_three）已有当前连续段，只剩 cold_hit_stats 只有历史极值。已改
  scripts/cold_hit_stats.py（streaks 返回 current）与 lib/cold_hit_stats_view.py
  （卡片头加「当前 连中/连败(进行中)」、两张表加「当前」列），重启
  penta5-position-cold.service 加载新脚本，面板即时更新。
- **2026-10-30 10:08 三位置杀码：位置对齐 + 口径注明**：卡片改为按固定位置顺序
  千→百→十→个 展示入选的 3 个位置（选择逻辑仍按命中率取前 3，排序只影响展示）；
  面板脚注明确「核算只使用前 4 球(千、百、十、个)，第 5 球不参与」。代码核对：
  kill_three_pick 的所有命中/遗漏/回测只索引 numbers[0..3]，第 5 球从未参与。
- **2026-10-30 10:14 全模型口径对齐审计（用球/命中/位置/连中）**：
  - 用球口径：所有活跃模块只使用前 4 球(千、百、十、个)，第 5 球仅展示不参与。
    代码路径两类：① `stats_history.valid_numbers` 对 4/5 元素输入统一 `[:4]` 截断，
    framework/killer/he3/selected_six/cold/cold_fixed/stat50/position 系列全部走它；
    ② 显式 `numbers[:4]` 或索引 0..3（run_stats 实时区、kill_three、cold_hit_stats、
    position_input_features、model_significance、real_hitrate、strategy_settled）。
    AI 提示词(六套 contract/request_options)均明写「第五球不参与」。
  - 命中口径：杀号类=实际开奖号≠排除码；保留集类=实际合尾∈保留集；整组=全部项通过。
    各块脚注统一标注。
  - 位置顺序：全站 q/b/s/g = 千/百/十/个；三位置杀码展示已改为固定位置顺序。
  - 连中/连败：封存型模型(框架/八项/冷码/合尾)按「缺预测/冲突/未核验即中断」；
    规则型回测(三位置杀码/冷码杀号战绩)按已结算期连续计算——两类语义已在脚注注明。
  - 审计发现并修复的历史地雷：`lib/fix_all.py`(遗留脚本会把 `[:4]` 改成 `[-4:]`，
    即丢千位+用第5球)已改为只抛禁用异常；`lib/auto_r1_match5.py`(已退役)内 2 处
    `[-4:]` 改为 `[:4]`。活跃代码未发现使用第5球的路径。
- **2026-10-30 10:22 冷码杀号战绩块顶部显示「本期三位置建议」**：用户反馈两个区块容易混淆
  ——冷码杀号战绩是按位置独立统计（四个位置 × Top1/2/3 杀号数），并不选位置；
  真正"从4个位置里取3个"的是三位置杀码建议块。已在冷码杀号战绩块顶部加一行
  「本期三位置建议：百位 杀x · 十位 杀y · 个位 杀z（放弃:千位）（详见下方三位置杀码建议）」，
  数据直接读 data/kill_three_pick.json，随每期刷新。
- **2026-10-30 10:35 出码/统计区块提前显示**：用户反馈"出码都快关盘才蹦出来"。
  诊断：面板同步进程原来在 draw+180s 结算点才跑 /root/run_stats.py，整轮 134s，
  写到面板时已是 draw+285~309s（关盘=draw+230s）→ 统计/建议区块总在关盘后出现。
  修复：① SETTLE_SECONDS 180→45（开奖数据 draw+3s 已入 state.json）；
  ② /root/run_stats.py 在快阶段(base/cold/framework/killer/stat50 ~15s)后先写一次
  面板(early_panel)，把三位置杀码/冷码杀号战绩/位置冷码/显著性/真实命中率等
  出码与统计块提前展示；selected_six(~60s)/he3_extra(~52s)两个重算放到后面，
  结束时再最终写一次面板。实测：建议块 draw+~65s 可见(关盘前 ~165s)，整轮
  draw+183s 完成(关盘前 ~47s)。/root/run_stats.py 备份在
  /root/archive/run_stats.py.bak-20261030-early-panel。

- **2026-10-30 11:00 AI 三位置杀码 + 一合尾 + 两反向形态（替换规则版三位置杀码）**：
  用户要求把「选 3 个位置各杀 1 码」从固定冷码规则改为由 DeepSeek 决定，并追加
  「五个合尾（千百十个/千百十/千百个/千十个/百十个）里选 1 个排除 1 个和尾数字」与
  与八项独立框架同口径的两项四位反向形态（单双/大小；被杀位置取杀码形态反面，放弃位置自选）。
  新增 `lib/kill3_tail.py`（提示词/严格校验/封存/结算/今日战绩）、`scripts/run_kill3_tail.py`
  （每 5 分钟开奖后等待数据就绪→结算→发布窗口内请求→封存→写面板）与 `data/kill3_tail/`；
  `lib/kill_three_view.py` 改为读取 AI 封存并展示 6 项（3 位置 + 1 合尾 + 2 反向形态），
  整组命中 = 6 项全中，只统计开奖前发布、开奖后核验的记录；
  `lib/cold_hit_stats_view.py` 顶部摘要行改读新 AI 数据；`scripts/position_cold_daemon.py`
  停用旧 `kill_three_pick` 调用；cron 新增 `penta5-kill3t`（flock + sleep 25，不占用既有任务窗口）；
  证据备份 JSON_DIRS 增加 `kill3_tail`；ops_health 新增 `kill3_tail` 检查（最新期无封存超 7 分钟即 FAIL）。
  旧规则版 `data/kill_three_pick.json` 保留为历史证据，不再参与面板与统计。

- **2026-10-30 11:15 AI 三位置+一合尾区块新增攻击语（与八项独立框架同格式）**：
  `lib/kill3_tail.py` 新增 `build_attack()`：按八项格式输出
  「千/百/十/个 位置保留码（被杀位置=去掉杀码，未选位置=1234567890）＋四定＋合尾保留码
  ＋除<四位单双形态>除<四位大小形态>」；`lib/kill_three_view.py` 在区块内渲染只读攻击语
  文本与「复制攻击语」按钮（含 clipboard 回退）；`lib/attack_editor_view.py` 把
  `kill3-attack-text` 注册进全局攻击语编辑框并把版本升到 `all-attack-editor-v5-kill3-tail`，
  编辑/复制行为与八项攻击语一致。

- **2026-10-30 11:25 修复「必须手动刷新数字才跳」**：面板 live refresh 只合并
  `panel_live_refresh.py` 里 `names` 名单登记的标记区块，`KILL_THREE`（AI 三位置+一合尾）
  与 `COLD_HIT_STATS`/`POSITION_COLD`/`MODEL_SIGNI`/`REAL_HIT` 此前未登记，导致这些周期块
  只有整页刷新才更新。已把 5 个标记加入 names，版本升到 `quiet-panel-v16-live-sections`；
  刷新脚本变化后浏览器需要手动刷新一次以装载新脚本，之后每 8 秒自动合并最新封存/统计。

- **2026-10-30 12:06 合尾改为「五选一且不得与上一期相同」，并把五种合尾对比喂给 AI**：
  用户反馈「合尾从五种合尾选，不是固定百十个」。排查确认不是硬编码：132–139 期百十个合尾的
  候选码 7 连续 47–50 期未开（超长遗漏），模型按统计连续选它是有依据的；但观感上像固定。
  现改为：提示词新增五种合尾候选对比（候选码/近50期出现次数/当前遗漏/近50与近30期该尾候选
  杀号命中率，按命中率排序，并注明顺序不代表推荐），且本期合尾不得与上一期相同（从其余四种
  中选），本地校验 `validate_tail_choice` 拒绝重复并在重试反馈里说明；封存记录写入
  `tail_restriction`（上期合尾），面板显示「合尾限制」行，攻击语随之更新。
  实测 145 期百十个 → 146 期千百十 杀 9，理由「避开上期百十个合尾」。
- **2026-10-30 12:09 修复 live refresh 整轮失败（必须手动刷新）**：上一轮把 5 个周期块加入
  live refresh 名单后，`REAL_HIT` 的 START/END 标记在 HTML 解析后不配对（START 落入未闭合 DIV），
  导致 `ranges()` 抛 unbalanced-marker、整轮刷新失败并显示「数据更新暂未完成」。
  已把 REAL_HIT 移出名单（其余 KILL_THREE/COLD_HIT_STATS/POSITION_COLD/MODEL_SIGNI 保留），
  版本升到 `quiet-panel-v17-live-sections`；用 jsdom 复跑 apply()：无异常、KILL_THREE 区块
  随新封存更新（23 个 plan），标记配对全部通过。REAL_HIT 区块自身的未闭合 DIV 留待后续修正。
