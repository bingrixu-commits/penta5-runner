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

- **2026-10-30 12:22 完善 live refresh：REAL_HIT 修好并重入名单，25 个周期块全覆盖**：
  `real_hitrate_view.render_block()` 末尾多了一个 `</div>`，解析后提前闭合外层 `div.container`，
  使 REAL_HIT 的 START 落在 container 内、END 落在 body，`ranges()` 抛 unbalanced-marker。
  已删除多余闭合标签、把口径说明包进 `rh-foot`；REAL_HIT 与 STRATEGY_BOARD_DERIVED 重新加入
  `names`，版本升 `quiet-panel-v18-live-sections`。现在面板上 25 个动态标记块全部登记且配对
  通过（含 KILL_THREE/COLD_HIT_STATS/POSITION_COLD/MODEL_SIGNI/REAL_HIT/STRATEGY_BOARD_DERIVED）。
  jsdom 实测：29 个 name 配对 BAD=0；apply() 对 KILL_THREE 产出 25 个 plan、对 REAL_HIT
  （89.91%→88.88%）正常合并，无异常、无「数据更新暂未完成」提示。


- **2026-10-31 11:55 彻底退役「30期固定冷码七项框架」(cold_fixed)**：用户要求把这套框架清理干净。
  处理：① 删除 `*/5` cron（28→27 jobs，crontab_tool.sh diff matches）；② 面板区块与攻击语
  编辑器条目移除（attack editor v6），`lib/model_retirement.py` 每轮幂等 strip 该区块；
  ③ 运行观测/命中率累计/模型用量/面板上下文/发布时效等派生视图全部去 cold_fixed，聚合时过滤
  历史 ledger；④ 29 个数据文件（8.8M → gz 772K）归档到
  `/root/archive/retired-20261031-cold-fixed/`（manifest.json + README.md，含逐文件 sha256
  与回滚步骤）；⑤ 删除剩余代码文件 `lib/cold_fixed_framework{,_model,_view}.py`、
  `scripts/run_cold_fixed_framework.py`（9791b08，无调用方），清理 model_usage 采集状态与 pycache。
  相关提交：297a0b2、c61effe、d3d1fe4、9791b08。回滚：`git revert 9791b08 d3d1fe4 c61effe 297a0b2`
  + 按归档 README 执行 gunzip 恢复数据 + `bash scripts/crontab_tool.sh install` + 恢复 logrotate 备份。
  注意：本日志中 2026-10-30 关于 cold_fixed 的条目均为历史记录，该框架现已不再运行。

- **2026-10-31 12:14 AI 三位置杀码升 v6：3 个位置全部计入整组（6 项 / 3 注）**：
  用户确认「定位排除设置三个」——v4/v5 只在面板显示 3 张位置卡、其中 2 个计入整组（第 3 个
  display_only 仅展示），现改为 3 个位置全部计入整组与攻击语：整组 = 3 位置 + 1 合尾 + 2 反向形态
  共 6 项，位置杀码 3 注/期，攻击语 3 个位置各 9 码、弃位保留 10 码；`RULE_VERSION` 升
  `kill3-tail-v6-three-positions`，提示词/校验/封存/统计同步更新，settlement 新增 `rule_version`
  字段、`today_stats` 改为版本锁定（只计 v6，v1–v5 归档），面板新增「今日四手连败 N 次（不重叠计数）」。
  旧 v1–v5 封存与结算逐字节保留；v1–v3 本为 3 位置 6 项版，历史参考 55 期 observed 56.36%（exp 54.70%）。
  10^4 精确枚举：5 项口径 exp 62.07% / obs 63.16%（95 期），6 项口径 exp 54.78% / obs 53.68%（同 95 期）。
  提交 e20b9e1；备份 /root/ops/kill3-v6-121237/（含 golden.json 与回滚 README）；首个 v6 封存
  ref 20261031147。回滚：`git revert e20b9e1`，并把 /root/ops/kill3-v6-121237/ 下 5 个 .py 回拷原路径。

- **2026-10-31 15:50 二数合/三数合全链路审计 + 面板口径与冷码修正追溯修复**：用户要求检查服务器
  「二数合(he2_keep)/三数合(he3_keep)」并修复问题。只读审计结论：三项框架（固定八码/七项独立 v7/
  冷码七项）he2 与 he3 的封存→结算独立重算 0 处不一致（r1 2156 期、selected_six 1146 期、
  cold 933 期、he3_refine 1570 期），10^4 枚举复核基线（he2 留4码 98.08%、he3 留5码 93.75%、
  留6码 97.44%）精确；提示词/校验/结算/回放口径一致；历史封存（无 parity 约束的 v1/v2）用现行
  代码全部可回放。**he3_refine 当前 revision 88~89%（对 93.75%）为单日统计波动**（全量 1472/1570
  =93.758%，p=1.0；v3-only 45/50=90.0%，p=0.24），无选择/结算 bug。实际修复 3 处展示/追溯缺陷
  （commit 6fb3825）：① 面板 r1 第7/8项徽章改为「4码命中率 / 6码命中率」并在 tooltip 标注随机
  基线，避免把 98%/97% 的保留集项误读；② he3_refine 行内注入的剩余5码统计在 ready 状态也加
  「剩余5码独立参考（不计入原8项）」前缀，消除同一行 6码≈97% 与 5码≈88% 两口径并排的歧义；
  ③ 冷码框架 repair_he3_keep 修正时把模型原值写入封存 selection_metadata
  （he3_keep_raw/he3_parity_repaired/he3_repair_policy）并把 reason 改写为含原值→修正值，
  旧封存无该字段仍可回放（当日 80 个旧封存 _validate_seal 全 PASS）。仍建议（未改）：hitrate_guard
  对单日窗口 n≥30 且 p<0.05 即报「偏离随机基线」假阳性偏高（本日 he3_refine 随样本在 0.036↔0.061
  间穿越阈值），可考虑 n≥100 + p<0.01 或累计窗口；he3_refine 日统计混 v2+v3 两 revision（5码基线
  恒 93.75%，无数值影响，仅口径不纯）。历史 22 次 repair 已回填到 data/cold_he3_repair_audit.jsonl
  （ref/target/raw/repaired，原始 cron 日志行无时间戳）；今后新封存的 raw 值以 selection_metadata 为准。
  回滚：git revert 6fb3825 + 备份 /root/ops/he2he3-fix-20261031-154056/ 回拷 4 个文件。

- **2026-10-31 16:36 合尾杀码展示补基线说明 + 稳定化影子回放（不改口径）**：用户反馈
  「每个框架各尾排一个数字都不稳、命中率太低」。只读复核：单尾杀1码数学上限即90%，长跑
  r1 90.16%（n=4340）、冷码 89.12%（n=1894）、killer 90.02%（n=6465）、合计 89.9%，均贴基线；
  2172期真实开奖上5种尾值均匀（χ² 6.3~13.2<16.9），最冷/最热/上期值/固定0/冻结五种选法
  回放 89.4%~90.0%，无任何选法可超90%。「不稳」主因：每期按滑动窗口重选极值（实测换码率
  r1 100%、冷码90.7%、AI三位置100%—提示词硬规则"不得与上一期相同"、七项v7 57%）。
  本次只做展示层：各框架合尾/杀码项标注随机基线（90%/93.75%/98.08%/97.44%）与单日n<30
  波动提示，命中率基线区块加"90%是数学上限"脚注（备份
  /root/ops/tail-display-20261031-163208/）。稳定化影子回放（只读，无现网改动）：
  /root/ops/tail-stability-shadow-20261031-163308/report.md — 冻结到失手再换命中率与现行
  持平（killer 90.05% vs 90.02%、冷码 89.33% vs 89.12%、固定八码 89.15% vs 90.16%），
  换码次数降至1/3~1/6；若上线需各框架出新 revision，且 AI三位置须同时删除"不得与上一期
  相同"规则（待用户确认）。

- **2026-10-31 16:46 合尾稳定化候选影子任务上线（只读对照，不改现网口径/封存）**：用户确认
  「照你的意思来」；按建议先做影子而非直接改口径。政策 tail-hold3-until-miss：同一尾类+尾码
  命中后沿用、最多连用3期，失手或满3期改模型本期新选码。新增 lib/tail_stability_shadow.py
  + scripts/run_tail_stability_shadow.py + 面板区块 TAIL_STABILITY_SHADOW（插入 HITRATE_BASE 之后），
  cron 每5分钟（sleep50，flock penta5-tail-shadow，日志 data/tail_stability_shadow_cron.log），
  已写入 ops/crontab.txt 并 crontab_tool.sh install / diff 一致（27→28 jobs）；面板 live refresh
  names 注册该区块（quiet-panel-v19-live-sections）。产物 data/tail_stability_shadow.json：
  backfill（全历史同批开奖回放）+ forward（从 start_period=20261031201 起逐期前瞻，按封存回放）。
  首轮回放：killer 90.04%（n=6474）/固定八码 90.17%（n=4344）/冷码 89.11%（n=1900），稳定化
  命中率持平（killer 90.04%、冷码 90.67%、固定八码 89.94%），换码次数约降至 1/3~1/6。
  注意：AI三位置v7 现行"合尾不得与上一期相同"硬规则未动（影子策略忽略该规则）；正式上线需
  各框架出新 revision 并删除该规则，待前瞻样本（n≥50）后再决定。回滚：删除 cron 行并
  crontab_tool.sh install；回滚代码 git revert；区块可由 lib/tail_stability_shadow.py:
  apply_block 保留/删除（删除时同时移除面板块）。

- **2026-10-31 17:26–17:56 全服务器模型/命中率项目级审计 + 监控与看板口径修复**：用户要求「全面检查服务器模型，找出命中率的项目原因」并「修复完善部署落地」。四路只读审计（AI 六框架逐项精确枚举 / 模型运行健康 / cold-he3-kill3 与覆盖率 / accuracy 经典层与 pm2）结论：7 个 AI 框架整组命中率全部贴各自 10^4 精确枚举基线（killer 39.67% vs 41.62% p=0.064、framework 55.93% vs 55.59% p=0.748、cold 54.1% vs 54.3% p=0.95、he3 v3 89.4% vs 93.75% p=0.14/0.19、six v7/kill3 v7 n<200），逐项无短板、失败归因均摊；模型分档 Pro 无命中率收益（he3 v2 同版 flash 94.09% vs pro 94.08% p=1.000；framework 55.91% vs 57.27% p=0.843）且延迟/成本更高；真实损失来自覆盖率（killer/framework coverage 55.3%、plan_hit 22.0%/31.1%，缺期=总闸关闭 1127 + 引入前 346 + 开启时段失败 283）。本次修复 6 文件 3 提交（fdffd46 monitor 口径：样本阈值统一 n<200、guard 缺期分类（总闸/未发布）、402 时间戳误报；f6ac635 stats 标注：joint_form 口径分界、cold 旧口径 57 条披露、data_lag_note；c76bb9f accuracy 看板：33 项对照基线、样本不足、球位中性命名、退役行隐藏）。部署验证：13 项独立回归全 PASS（HEAD c76bb9f、git status 干净、py_compile/node --check、脚本 exit=0、panel HITRATE_BASE 区块 as_of=20261031214 含口径分界/样本不足且六个区块标记各 1、cron 17:42/17:52 已用新代码）；manifest 已按新源码重建（auto-baseline-20261031-173533），ops_health manifest_drift=OK。回滚：git revert c76bb9f f6ac635 fdffd46，并从 /root/ops/fix-20261031-1726/backups/ 回拷 6 个文件（服务无需重启：run_stats.py 每轮新解释器加载 view）。待决策（未做）：①framework/he3 是否从 pro 回退 flash（无命中率收益、p95 53s/98.5s、402 与漏发风险更高）；②penta5-supervisor/qualified-picks 未运行、多个 .disabled 配置仍在跑的 pm2 漂移是否清理。真实遗留告警：model_usage warn（今日 402=8 次为真实额度事件，非本次误报项）。
- **2026-10-31 18:05-18:25 第二批完善: 模型档位回退 + pm2 配置治理 + kill3 遥测 + 面板入口**
  - 模型回退: framework/he3 由 deepseek-v4-pro 回退 deepseek-flash(依据同日审计: he3 同版本 v2 flash 94.09% vs pro 94.08% p=1.000, framework 55.91% vs 57.27% p=0.843, 无命中率收益; pro p95 53s/98.5s 且 402 漏发风险更高). 实测 18:05 起 framework ref 20261031217-220, he3 ref 20261031217-219 全部 requested=returned=deepseek-flash 且发布连续; he3 单次耗时 45-62s -> 2.2-3.5s. 回退备份 /root/ops/fix-20261031-1726/env/env.before-rollback (chmod 600), 恢复即把 .env 两行改回 pro.
  - pm2 配置对齐(a05711b): 4 个在跑进程配置恢复启用名(claude-ensemble/claude-stable/four-ding-shape-kills/shape-avoid-3), 删除 four-pos-drop6 旧 .disabled 重复文件, supervisor/qualified-picks/source-proxy 改 .disabled 并标 RETIRED(均未运行, 保留应急启用路径); 未执行任何 pm2 启停/删除/save, jlist 10 进程 name/restart/pid/status 前后一致.
  - kill3 遥测(766d4e6): kill3 补 AI_HTTP_PHASE start/complete(复用 lib.ai_http_observability, 不改重试/发布语义); 18:10/18:15 两周期各 1 start + 1 complete(status=200), model_usage ledger 出现 kill3 kind=http 事件, 402/429/timeout 分类链路打通.
  - 面板入口(766d4e6): 累计/今日 fallback 区块加 打开命中率看板(/accuracy) 链接; 18:18 面板重建后已 live, 六区块标记各 1.
  - 回滚: git revert 766d4e6 a05711b + 恢复 .env 两行(备份见上); 无需重启服务.

  - 补充(18:26): 累计报告 killer/framework 的 model_split 改为从封存 prediction_seals 的 model_metadata 取 requested_model（此前 hit_stats publication 无该字段导致分档为空）；修复后 framework = flash n=1689 55.83% / pro n=124 54.84%(冻结), killer = flash n=1182 39.17% / pro n=6 0%。
- **2026-10-31 19:12 kill3 面板单项补 95%CI 与小样本波动提示**：用户问「AI 三位置杀码的合尾杀码 81% 为什么低」。复核：v7 合尾单项 n=59–60、命中率 81.4–81.7%，与账本/封存独立复算 0 不一致；90% 基线经 800 期开奖的 10 类开奖尾 chi²（4.6–15.8，<16.9）验证均匀；11 次未中全部是「合尾实际值=杀码」的唯一失败模式；长跑全部 kill3 版本 207/232=89.22%（p=0.66）、今日早前 v1–v6 90.32%（n=62）均正常；整组 62.7% vs 精确基线 61.9% 反而略高（形态 96.6/98.3 高于 93.75 抵消）。结论：单日 n≈60 的 -2.2σ 波动（p≈0.046，未过 37 条单项序列的 Bonferroni 阈值 0.0014），不是逻辑/数据 bug。为防误读，lib/kill_three_view.py 给合尾项加 95% Wilson CI、并在 n<200 时标注「1σ≈3.9pp/2σ≈7.7pp，单日单项只作波动参考」；逻辑/结算/封存未改。回滚：git revert 本提交。

- **2026-10-31 20:20 命中率漂移监控 + joint_form 版本化基线上线（dac07ef / ee5eae2）**：用户问「模型运行久了命中率会下降，如何预防」。只读复核（2200 期级）未发现真实下降：killer 全期 39.67% vs 逐期加权基线 41.65%（p=0.058）、framework 55.87% vs 55.63%（p=0.82）、30 个窗口检验下 |z|>=2 仅 2 个（零假设期望 1.5 个，P=0.785）；运行侧 returned!=requested 0/8824、prompt_tokens 自 10-23 完全平坦、finish_reason=length 仅早期出现。真正造成「越跑越低」观感的是版本/项数切换（selected_six v2 6 项 60.29% -> v4 8 项 51.14% -> v7 7 项 53.12%，对应精确基线 60.54%->52.14%->57.26%，每个版本都贴自身基线）与 16:44 joint_form 转正后新基线未落盘。本次上线：①framework_hitrate_cumulative.py 新增 drift 段（近 100/200/300/500 期 Wilson/z + 500 期 CUSUM 峰），killer/framework 随机期望改为按记录逐期加权（分界前 expected_ai / 分界后 expected_opt，来自 data/joint_form_shadow.json 的逐期 10^4 精确枚举），并写 data/joint_form_baseline.json（killer 41.64%->44.30%、framework 55.63%->57.60%，与旧基线 rounding check PASS）；②hitrate_guard.py 读取 drift 段告警（近200/300期同向 z<=-2 或 CUSUM峰<=-3sigma 预警；单窗 z<=-3 或峰<=-4sigma 显著下漂；累计报告 >45min 未刷新 stale warn），替代此前日样本 n≈129<200 永远「样本不足」的静默；③HITRATE_BASE 面板区块新增「滚动漂移监控」表与版本化基线说明。部署验证：py_compile 全 PASS；手动跑累计报告 7.7s（6 框架 6 drift 行；killer z200=-1.80/峰=-2.18、framework z200=-1.15/峰=-1.02，均 ok）；阈值/随机序列/强下漂/注入 warn 的 tmp-root 用例全 PASS；20:22 cron 已用新代码（累计日志含 drift 行、guard 日志 drift.status=ok age=4.2min）；20:24 面板 as_of=20261031244 含「滚动漂移监控」，六区块标记各 1；manifest 重建 auto-baseline-20261031-201924，ops_health 仅遗留已知 model_usage warn（402 8 次），其余 18 项 OK。回滚：git revert ee5eae2 dac07ef + 从 /root/ops/hitrate-drift-20261031-2035/backups/ 回拷 framework_hitrate_cumulative.py / hitrate_guard.py / hitrate_baseline_view.py；无需重启（run_stats 每轮新解释器加载 view，cron 每轮新进程）。

- **2026-10-31 20:56–21:00 kill3 口径回滚：恢复 v6「3 个位置全部计入整组」（撤销 v7 的 2 计入）**：用户要求「显示的 3 个位置都加入统计」，确认按计入整组处理。回滚 81a3d61（v7 三位置展示、2 个计入、5 项/2 注），恢复到 kill3-tail-v6-three-positions：3 个被杀位置全部计入整组与攻击语，整组 = 3 位置 + 合尾 + 2 形态共 6 项，位置 3 注/期，不再有 display_only。v7 的 81 条封存与基线元数据原样保留（归档；旧口径标签更正为 v1–v5、v7）。提交 c39148a（2 文件，21+/30-；备份 /root/ops/kill3-rewind-205518/ 含回滚前 4 文件 + seals/settlements 快照 + rewind.diff）。精确期望：6 项 0.5478 / 5 项 0.6191（v6-prep 枚举，见 /root/ops/kill3-v6-121237/README-v6.md）。参考：v7 81 期若按 6 项口径回算 43/81=53.1%。
  证据：①20:56:19 手动补发封存 ref=20261031251（20:55:35 cron 因编辑窗口撞上模块导入冲突标记失败一次，未造成缺期）→ target 20261031252，rule=v6、display_only=None、counted=[q,b,s]；②21:00:53 cron 正常封存 ref=20261031252 → 253（v6）；③结算 20261031252 独立复算逐项一致（千位杀8 vs 实际8 未中 → 6 项整组未中；百/十/合尾/两形态命中），counted 3；④面板 KILL_THREE 21:00 重渲染：3 张位置卡均「排除 1 码」、「全部计入整组」、统计行「整组命中率（6 项全中：3 位置+合尾+2 形态）14/23=60.87% · 位置杀码（3 注/期）63/69=91.30% · 旧口径（v1–v5、v7）121 期归档」、攻击语 3 个 9 码位置 + 弃位 10 码（四定×1、除×2）；⑤ops_health kill3_tail=ok，manifest 重建 auto-baseline-20261031-205851，整体仅遗留已知 model_usage/余额 warn。回滚：git revert c39148a + 从上述备份回拷 lib/kill3_tail.py、lib/kill_three_view.py（v7 封存无需处理）。

- **2026-10-31 21:10 AI 三位置杀码升 v8（尾码稳定化 hold3-until-miss，影子复核达标转正）**：
  按 21:05 定点复核结论转正（影子 forward 51~53 期：合计 88.70% vs 88.70%，95%CI
  [-2.90,+2.90]、McNemar p=1.0，换码次数全部下降，其中 AI 三位置 50→12；复盘报告
  /root/ops/tail-stability-shadow-20261031-163308/review-20261031-2105.md）。改动：
  RULE_VERSION 升为 「kill3-tail-v8-three-positions-hold3」；尾码改为「上期尾码命中且连用
  未满 3 期 → 沿用上期尾码；失手或连用满 3 期 → 用模型本期新选」；删除提示词
  「合尾不得与上一期相同」硬规则；封存新增 tail_policy（policy/hold_max/model_tail/
  reused_previous/note/previous_*），沿用时的 reason 改写为「稳定化沿用 + AI 候选 +
  模型原话」；提示词改【合尾稳定化规则】；面板新增「尾码稳定化：…」行与脚注；
  today_stats 按 v8 版本锁定、v1–v6/v7 归档；命中率基线映射新增 v8=(6 项, 0.5478)。
  旧封存逐字节保留（v6/v7 结算继续核验）。
  证据：①首期 v8 封存 ref=20261031254→target 20261031255，reused=True（上期 tail_all/8
  命中），封存 tail_all/8、模型候选 tail_qsg/5 留档；结算 target 255：tail hit=True、
  group_hit=True（v8 统计 1/1）；②第二期 ref 255 继续沿用（第 2 期，满 3 期后下期强制
  换码）；③面板 21:17 重渲染：尾码稳定化行、旧口径（v1–v6、v7）146 期归档、合尾单项
  显示 95%CI；④影子区块已移除 AI 三位置行并标注「已转正 v8」；⑤ops_health manifest
  已刷新，仅余额/用量已知 warn。备份：/root/ops/kill3-v8-hold3-210925/（6 个 .py +
  golden-v6.json）。回滚：git revert 2b7c958；回滚后下一期按 v6 口径封存，v8 封存保留
  （不影响历史核验）。

- **2026-10-31 21:40–21:51 kill3 稳定方案落地（冻结 v8 + 面板口径追溯 + manifest 自动基线 + kill3_stability 探针）**：用户要求「给一个稳定方案」，按四层落地：
  1) 冻结：新增 ops/kill3-stability-plan.md，声明冻结 kill3-tail-v8-three-positions-hold3（3 位置全部计入/整组 6 项/位置 3 注/尾码 hold3），冻结 72 小时且 ≥100 期；之后变更必须影子→门禁（n≥50、整组相对现行 95%CI 不劣、单项不低于精确基线 −2σ）→ 新 revision，一天最多 1 次、一版只改一项、先算 10^4 精确基线；并写清单写者部署、备份/回滚、监控阈值、余额与 402 纪律。
  2) 面板：KILL_THREE 新增只读「口径追溯行」——口径 kill3-tail-v8-three-positions-hold3 · 自 ref 20261031254 起（本版 N 期）· 整组精确基线 54.78%（6 项）· 口径已冻结（ops/kill3-stability-plan.md）；21:46:11 发布期起自然渲染，未改任何统计口径（当时今日整组 4/7=57.14%、位置 19/21=90.48%，与存档一致）。提交 99e3f1b（lib/kill3_tail.py 新增只读 version_info()，lib/kill_three_view.py 渲染色行）。
  3) 监控：ops_health 新增 kill3_stability 探针：24h rule_version 切换次数（≥2 仅 WARN，提示口径churn）、24h 缺期槽数（历史缺期/AI 总闸关闭所致 → WARN）、最近 6 个应封存槽位缺期（≥2 → FAIL，可操作）、最近 20 期发布延迟中位数（>180s WARN、>300s FAIL）。实测 version_changes=8 missing_slots=61 recent_missing=0 p50_latency=40s。提交 dfb5184。
  4) manifest：复核确认此前「每小时 fail」不是误报——是每次源码变更后未刷新基线的预期 drift；新增版本化 .githooks/post-commit（core.hooksPath=.githooks），lib|scripts|ops 变更的 commit 提交后自动 refresh_source_manifest --if-drift；实测提交 dfb5184 后自动写 auto-baseline-20261031-215029，21:50:32 manifest_drift 恢复 OK。
  验证：HEAD dfb5184、git status 干净；ops_health overall=warn，只剩 kill3_stability（口径churn 历史提示）与 model_usage（今日 402 历史 8 次）两项已解释 warn，无 fail；kill3 最新封存 ref 20261031261 正常发布、面板 data-as-of 同步；余额已充值至 21.96 元、余额守护自动恢复（warn→ok）。回滚：git revert dfb5184 99e3f1b，并 git config --unset core.hooksPath（如需移除自动基线 hook）；已封存记录一律不动。

- **2026-10-31 22:00 固定八码框架启用合尾 pair-hold3 稳定化（本地确定性后处理，不升 revision）**：
  延续影子复核与 kill3 结论，对固定八码框架（marginal-frequency-v2）的两项合尾杀码做本地
  确定性后处理：上期两项合尾均命中且自动沿用未满 3 期时沿用上期两项尾码；失手/满 3 期改由
  模型本期重选。新增 lib/framework_tail_hold.py（纯函数、只读），lib/auto_r1_framework.py 在
  请求校验后、卡片/封存前调用，封存 model_metadata.tail_hold3 留档（policy/hold_max/
  previous_ref_period/previous_tails/previous_hits/previous_streak/model_tails/reused_previous/note），
  攻击语与卡片使用最终封存尾码；命中口径与随机基线不变（两项仍各为杀 1 码 90%，整组 8 项
  精确基线不受影响）；旧封存无 tail_hold3 字段仍可原样回放。面板统计说明补「合尾两项启用
  稳定化」提示。
  历史回放（r1_framework_hit_stats 2235 期真实封存 + 开奖，逐期确定性重放）：两项合计命中率
  90.20%→90.76%（+0.56pp，n=4470，噪声内），尾码对换码次数 1197→637（-47%），沿用占 43.6% 期。
  首期实盘：ref 20261031264→target 20261031265，tail_hold3 写入（上期 qbs 命中/bsg 未命中 →
  本期换码；模型本次仍选同码），面板卡片显示「合尾稳定化：本期换码」，framework 任务 success。
  备份：/root/ops/tail-hold3-framework-215629/（3 个 .py + golden-framework.json）。
  回滚：git revert <hash>；无需回滚数据（封存仅多 metadata 字段）。

- **2026-10-31 22:05 七项独立 v7 启用合尾单码 hold3 稳定化（本地确定性后处理，不升 revision）**：
  延续框架级稳定化方案，对七项独立 v7 的单合尾做本地确定性后处理：上期尾码命中且自动沿用
  未满 3 期时沿用上期尾码；失手/满 3 期由模型本期重选。新增 lib/six_tail_hold.py（纯函数、只读），
  lib/selected_six_model.py 在响应校验后、卡片/封存前调用，封存 model_metadata.tail_policy 留档
  （policy/hold_max/reused_previous/note/model_tail/previous_tail/previous_hit/previous_streak/
  previous_ref_period）；沿用时 response.reason 改写为含 AI 候选与模型原话；命中口径与随机基线
  不变（单尾 90%，七项整组精确基线 57.26% 不变）；旧封存无 tail_policy 仍可原样校验。
  面板统计规则补「合尾尾码稳定化」说明。
  历史回放（selected_six v7 单尾 92 期真实封存 + 开奖，逐期确定性重放）：单尾命中率 84.78%→89.13%
  （+4.35pp，n=92 小样本），尾码换码次数 39→19（-51%），沿用占 38% 期。
  首期实盘：ref 20261031265→target 20261031266，tail_policy 写入（上期千百十杀0 命中、连用第2期 →
  沿用），published=True；状态 published。备份：/root/ops/tail-hold3-six-220235/（3 个 .py +
  golden-six.json）。回滚：git revert；无需回滚数据（封存仅多 metadata 字段）。

- **2026-10-31 22:10 冷码七项启用合尾 pair-hold3 稳定化（本地确定性后处理，不升 revision）**：
  冷码框架两合尾做本地确定性后处理：上期两项合尾均命中且自动沿用未满 3 期时沿用上期两项
  （类型+冷码），失手/满 3 期改由本期冷码重选。新增 lib/cold_tail_hold.py（只读），
  lib/cold_framework.py 的 build_prediction 支持从 selection_metadata.tail_hold3 取 sealed_tails
  并以同一 metadata 可重放（_validate_seal 回归通过）；lib/cold_framework_model.py 在响应
  校验后写入 tail_hold3；视图统计规则补说明。历史回归：现有 9 个日账本、1072 条封存
  _validate_day/_validate_seal 全部 PASS（旧封存无 tail_hold3 不受影响）。首期实盘：
  ref 20261031266→target 20261031267 published=True，tail_hold3 reused=True（上期 qbs/2、qsg/8
  两项均命中，沿用第1期），model_tails 留档 all/8、qbs/2。
  备份：/root/ops/tail-hold3-cold-220822/（4 个 .py + golden-cold.json）。回滚：git revert。

- **2026-10-31 22:15 killer 九项启用三合尾 triple-hold3 稳定化（本地确定性后处理，不升 revision）**：
  三项合尾（all/qbs/bsg）做本地确定性后处理：上期三项均命中且自动沿用未满 3 期时沿用上期
  三项尾码，失手/满 3 期改由模型本期重选。新增 lib/killer_tail_hold.py（只读），
  lib/auto_r1_killer.py 在请求校验后、卡片/封存前调用，封存 model_metadata.tail_hold3 留档；
  命中口径与基线不变（三项仍各为杀 1 码 90%）。历史回放（r1_stats verified_evaluations 2223 期/
  6669 项）：命中率 89.98%→90.18%（+0.19pp，噪声内），三尾码组换码次数 1997→943（-53%），
  沿用占 53.5% 期。备份：/root/ops/tail-hold3-killer-221211/（3 个 .py + golden-killer.json）。
  回滚：git revert。至此五个框架（AI三位置/固定八码/七项独立/冷码七项/killer）合尾稳定化全部落地；
  影子任务停止对照，保留只读历史与面板说明。

- **2026-10-31 22:14 kill3 冻结守卫上线（机器强制口径冻结 / 单写者）**：用户确认冻结期「≥100 期或 72 小时先到为准」并要求其他窗口不得再改 kill3。新增 ops/kill3-freeze.json（rule_version=kill3-tail-v8-three-positions-hold3、frozen_at=2026-10-31T21:41:42+08:00、freeze_ref_period=20261031254、min_periods=100、freeze_hours=72、4 个受控源码 sha256：lib/kill3_tail.py / lib/kill_three_view.py / scripts/run_kill3_tail.py / lib/panel_context.py）+ ops_health 新探针 kill3_freeze（RULE_VERSION 或受控 hash 变化即 WARN；同时显示进度与已冻结时长）。实测：kill3_freeze OK「freeze kill3-tail-v8-three-positions-hold3 · 进度 13/100 期 · 已冻结 0.5h/72h · 冻结中」。冻结期内其他框架的 hold3 稳定化提交（b6bc46f/0bb4f57/38fd332，15 文件）不含 kill3 受控文件，守卫判定未违反。回滚：git revert 本提交（冻结戳与探针一并移除）。

- **2026-10-31 22:25 五框架合尾稳定化补「沿用/换码」标记 + 转正后复盘定时任务**：
  面板标记：killer 卡片标题行、冷码七项卡片内各加一行「尾码稳定化：
  沿用上期（第N期）/本期换码（原因）」；七项独立 v7 的同类标记因面板刷新异常已回滚待重做，均从封存元数据 tail_hold3 / tail_policy 派生；
  固定八码卡片与 AI 三位置卡片此前已有同类标记。标记只在 post-hold 周期生成/渲染，
  旧封存卡片字节不变（六项/框架的 card_html 摘要校验未改动）。
  复盘任务：新增只读 scripts/tail_hold_review.py，统计各框架转正后首期起：期数、尾项样本、
  尾项命中率(95%CI)、沿用期数/沿用率、换码期数，写 data/tail_hold_review.json 与
  /root/ops/tail-hold-review/review-<stamp>.md；cron 每日 22:35（flock penta5-tail-review），
  已写入 ops/crontab.txt 并安装（28→29 jobs，diff 一致）。
  首轮回放：AI三位置 v8 自 ref 254 起 14 期尾项 12/14=85.71%（沿用率 42.9%）；
  固定八码 4 期 87.50%、七项 v7 3 期 100%、冷码 2 期 75%、killer 1 期 66.67%
  （样本尚小，3 日复盘再看）。备份：/root/ops/tail-markers-222012/（3 个 view 原文件）。
  回滚：git revert；cron 回滚 = 删除 ops/crontab.txt 该行并 crontab_tool.sh install。

## 2026-10-31 22:28–22:50 新增独立框架：AI 二位置杀码 + 一合尾 + 二数合/三数合（kill2_tail）

用户要求「增加一个同样框架，位置就选择俩个，其他一样，加二数合跟三数合」。按与线上「AI 三位置杀码 + 一合尾」同款机制（独立 DeepSeek 请求、独立封存/结算/今日战绩/攻击语/面板卡、不读写其他框架预测）实现，整组 **7 项**：

- **2 个位置杀码**：AI 从千/百/十/个中选恰好 2 个，各排除 1 码；其余 2 位不参与定位项（攻击语保留 10 码）。同一位置同杀码最多连用 3 期，中间未选该位置不清零；满 3 期下一期必须换码。
- **1 个合尾杀码**：5 种合尾 5 选 1、排除 1 个和尾；尾码 hold3-until-miss 稳定化（上期命中且自动沿用未满 3 期则沿用，失手或满 3 期由模型重选；与 kill3 v8 同口径，封存写 tail_policy）。
- **二数合保留 4 码**：从 02468 留 4 码；前 4 球任取 2 位 6 种组合和值个位任一落入保留集即命中（保留集语义，基线 98.08%）。
- **三数合保留 5 码**：从 0-9 留 5 码且必须 2 奇 3 偶或 3 奇 2 偶；任取 3 位 4 种组合和值个位任一落入即命中（基线 93.75%）。
- **2 项四位反向形态**：被杀 2 位取杀码单双/大小反面、未选 2 位字符 AI 自选；实际四位形态不等于封存形态即命中（基线 93.75%）。
- 整组命中 = 7 项同时命中；**整组精确基线沿用同构的 selected-six v7 的 10^4 精确枚举值 57.26%（7 项）**，独立验证员已从零复算确认。

落点与改动（commit 见文末）：
- 新增 `lib/kill2_tail.py`（RULE_VERSION `kill2-tail-v1-two-positions-he2he3`，封存/结算/今日战绩/攻击语/尾码稳定化/位置连用限制）、`scripts/run_kill2_tail.py`（每期任务，源就绪等待 + 发布窗口 + 面板发布）、`lib/kill_two_view.py`（面板 `KILL_TWO` 区块：2 位置卡 + 合尾 + 二数合 + 三数合 + 2 形态 = 7 卡，含 7 项统计行、口径追溯行、攻击语与复制按钮）。
- 数据：`data/kill2_tail/{seals,settlements}.json`；日志 `data/kill2_tail_cron.log`；任务锁 `data/.kill2_tail_job.lock`。
- 面板/攻击语接入：`lib/attack_editor_view.py`（v7，新增 kill2 攻击语定义）、`lib/panel_live_refresh.py`（v20，names 注册 `KILL_TWO`）、`lib/panel_context.py`（新增可选 `kill2_stats/kill2_previous`，kill3 输出经固定 fixture 对比逐字节不变）、`/root/run_stats.py`（early/final 面板链接入 `apply_kill_two_block`，备份 `/root/ops/kill2-framework-20261031/run_stats.py.bak`）。
- 统计/监控/备份/用量增量：`lib/hitrate_baseline.py`（(7, 0.5726)、label、今日行）、`scripts/framework_hitrate_cumulative.py`（版本锁定行 + drift）、`scripts/ops_health_check.py`（check_kill2，22→23 项，402 日志）、`lib/evidence_backup.py`（JSON_DIRS + kill2_tail）、`lib/model_usage.py`（SOURCE_LOGS + kill2）。
- cron：`*/5 ... flock -n /var/lock/penta5-kill2t.lock bash -c 'sleep 30 && ... run_kill2_tail.py >> data/kill2_tail_cron.log 2>&1'`，已写入 `ops/crontab.txt` 并 `scripts/crontab_tool.sh install`（29→30 jobs，diff 干净）；logrotate 已加 `data/kill2_tail_cron.log`（备份 `/root/ops/kill2-framework-20261031/penta5.logrotate.bak`）。

上线证据（2026-10-31 22:35 起）：
- 首期封存 ref 20261031271 → target 20261031272，published_at **22:35:55**（开奖 22:40:12 前，operation window 内），攻击语 `千123457890百123567890十1234567890个1234567890四定千百十个合123456790除单单双单除小大小大二数合0268三数合03578各`，与 `kill2_tail.build_attack` 逐字节一致。
- 首期结算 target 272 开奖 **1972**：位置 2/2 命中、合尾命中、二数合命中、三数合命中、两形态命中 → **整组 7 项命中，verified=true**（独立验证员用独立实现逐字段对账 0 差异）。
- :22:41:11 第二期封存 ref 272（tail_policy=沿用上期第 1 期，hold3 生效）；:22:45 第三期 ref 273 正常发布。
- 面板 `panel.html` 含 `KILL_TWO_START/END` 各 1、section `kill-two-ai`、7 张卡与攻击语输入框；live refresh v20 / attack editor v7 已随 `/root/run_stats.py` 重建生效。
- `ops_health` 23 项：kill2_tail=OK（published）；hitrate_baseline 行 n=1/hits=1/expected 57.26%；framework_hitrate_cumulative 行 n=1/coverage 100%；crontab_drift=OK。

部署期副作用（已恢复，非代码回归）：
- 22:28–22:41 本机部署过程中一个补丁脚本（`/tmp/patch_runstats.py` 的 while 循环 bug）残留并满核空转；22:41 另有外部排障进程（`timeout 600 ... selected_six_stats.refresh_selected_stats`，RSS≈1.2GB，/tmp/six_warm.out）叠加。4 核机器上 CPU 争用导致 **selected_six 在 ref 270/271/272 连续 3 期错过 operation window**（封存标记 expired-unpublished，历史逐字节保留，未回填）。清理这两个进程后 load 3.8→2.7，selected_six 在 ref 20261031273 于 22:46:47 恢复 published=True。教训：部署/诊断脚本必须加循环上限与 nice/单进程约束，不要在每 5 分钟开奖窗口跑重计算。
- 后续（未做）：`evidence_backup REQUIRED_DATA` 暂不加 `data/kill2_tail/seals.json`，待首个完整 evidence verify 通过后再加（避免 preflight 立刻 required-evidence-members-missing）。

- 二期结算（2026-10-31 22:45 起）：ref 20261031272 → target 273 开奖 4453，7 项再次全部命中，verified；今日 kill2 为 2/2 期（位置 4/4 注、合尾/二数合/三数合/两形态均 2/2），面板统计行已自动显示「整组 100.00%（2/2 期）· 精确基线 57.26%（7 项）」。
- **整组基线口径备注（独立验证发现）**：本框架登记 57.26% 沿用同构 selected-six v7 的历史封存配置逐配置枚举均值（/tmp/gain_gate_v7.json 的 items7_exact_v46=0.5726806，git 136d74f）；若按「合法封存配置均匀分布」期望，独立复算为 0.5702（1500 配置均值，stdev 0.0077，范围 0.5510–0.5899），两者差 0.24pp，远小于配置间波动 ±1.9pp，对监控影响 <0.25pp。登记沿用 0.5726 以保持与 selected-six v7 可比；若后续统一改用 0.5702 需同批改三个基线表（hitrate_baseline/framework_hitrate_cumulative/视图兜底）并重算历史 delta。
- kill3 冻结守卫：因本次在共享模块 lib/panel_context.py 增加可选 kill2 参数，按 ops/kill3-stability-plan.md「经批准变更后重算 hash 并更新本文件」更新 ops/kill3-freeze.json 的 panel_context.py sha256（818f6f1），并在 hash_updates 记录 kill3 固定 fixture snapshot/prompt sha 逐字节不变的证据；kill3 RULE_VERSION/口径/封存未动。ops_health 当前仅剩 kill3_stability（历史版本切换提示）与 model_usage（历史 402）两条已知 WARN，无 FAIL。

回滚：`git revert <本批 commit>`；`/root/run_stats.py` 回拷 `/root/ops/kill2-framework-20261031/run_stats.py.bak`；logrotate 回拷 `/root/ops/kill2-framework-20261031/penta5.logrotate.bak`；crontab 删除 kill2 行后 `scripts/crontab_tool.sh install`；`data/kill2_tail/` 历史证据保留不动。

- **2026-10-31 22:30–22:48 七项独立 v7 发布/显示短暂异常（3 期 expired-unpublished，已恢复）**：
  现象：ref 20261031270/271/272（target 271/272/273）连续 3 期封存为 expired-unpublished，
  panel 刷新报 Exception/ImportError，selected_six_status 短暂 rejected；其余四框架
  （kill3/固定八码/冷码/killer）同期发布正常。处理：先回滚本窗口 22:20 加的六项面板标记
  （提交 19d9240，怀疑其触发刷新异常），随后确认模块 import 已恢复；22:45 周期起
  正常，22:48 封存 ref 20261031273→274 published=True、status=published、面板卡片恢复。
  归因：与并行编辑窗口的未提交改动时间重合（当时 git status 有 panel_context/
  panel_live_refresh/hitrate_baseline 等多文件未提交，疑似编辑窗口撞模块导入），
  非合尾稳定化逻辑本身（其余四框架正常）。影响：三期六项统计按缺期处理，不补发历史期；
  已封存数据未改动。教训：多窗口并行时避免同时编辑共享模块；代码改动后以
  selected_six_status=poll 确认恢复再继续。回滚：无需（异常已自行恢复）。

## 2026-11-01 00:10 新框架 kill2 升 v2：三选二口径（3 位置展示、2 个计入整组）

用户要求「照这个模型之前 3 选 2 那版」。kill2_tail 由 v1（2 位置全计入）升 **v2**：
- AI 从千/百/十/个中选 **3 个位置**各杀 1 码，其中恰好 **2 个计入整组与攻击语**，第 3 个用 `display_only` 标明仅展示（不参与整组/攻击语/位置注数）；剩 1 个位置为放弃位置（攻击语 10 码）。
- 合尾 hold3 / 二数合留 4 码 / 三数合留 5 码 / 2 项反向形态不变；整组仍 **7 项 = 2 计入位置 + 合尾 + 二数合 + 三数合 + 2 形态**，精确基线仍 57.26%（display_only 不影响机械基线）。
- 位置连用限制恢复 v7 同款：同一位置同杀码最多连用 3 期（中间放弃该位置不清零）+ 同一放弃位置最多连用 3 期，满后必须更换。
- 封存新增 `display_only` / `dropped`；结算、攻击语、面板（3 张位置卡，其中 1 张标注「仅展示，不计入整组」）、统计（2 注/期）全部按“2 个计入位置”口径。旧 v1 封存/结算原样保留为 legacy，不计入 v2 统计。
- 回归：kill3 v8 的 panel_context 固定 fixture snapshot/prompt sha256 仍逐字节不变（9d45ac95…），kill3 口径/封存未动；kill3 冻结 stamp 的 panel_context.py hash 已按变更门禁更新并留 hash_updates 证据。
- 回滚：git revert 本提交；下一期起按 v1 口径封存，v2 封存保留。
- **v2 首期正式封存与结算（2026-11-01 00:05 起）**：ref 20261101001 → target 20261101002，published 00:05:54，positions q1/b8/s6，display_only=s，counted q/b，dropped=g；攻击语 `千234567890百123456790十1234567890个1234567890四定百十个合123456890除双单单双除大小小大二数合0268三数合34689各`（display_only 与放弃位置均保留 10 码）。target 002 结算：display_only=s、counted q/b、7 项整组命中（verified）。面板 KILL_TWO 已显示 8 卡（3 张位置卡中 1 张标注「仅展示，不计入整组」）、统计行「7 项全中：2 位置计入 + 合尾 + 二数合 + 三数合 + 2 形态」、旧 v1 17 期（整组 8 中）归档不计入；hitrate/cumulative 已切 v2 规则行（expected 57.26%）。
- **过渡期 1 期（ref 20261031288→20261101001）**：该期封存在 00:00:57 热更新窗口内由旧版 create_seal 写入，rule 已标 v2 但缺 display_only/dropped 字段（counted q/b 正常，按 counted 结算 group=False），其攻击语按当时旧口径生成。属 1 期过渡记录，封存不改写、不补写；其后封存字段完整。整组机械基线仍 57.26%（7 项构成未变，display_only 不影响概率）；若要把整组拉回旧 5 项那版的 ~61.9%，需另立 revision 把二数合/三数合移出整组。

## 2026-11-01 00:35–01:00 真实命中率口径升级 + kill2 v3 五项目径只读影子

用户目标「提高真实命中率、扛住压力」。先做只读审计再只增不改地落地：

**① 覆盖率审计（只读）**：按 24h 窗口（288 期）复算各框架覆盖率，缺口段全部命中 2026-10-31 AI 总闸关闭区间（control audit：02:23–03:06 / 07:31–10:40 / 11:06–11:53），与缺失期 01030..01038、01091..01128、01135..01143 逐段对应；**11:53 恢复后主链未再连续掉期**。累计覆盖率低还包含各框架「引入前」期数，不代表现行掉链。
**② 口径升级（commit 1b691b8）**：
- framework_hitrate_cumulative.py 每行新增 coverage_24h/7d、plan_hit_rate_24h/7d 与 rolling 明细（窗口锚点、已上线期数、漏期、last_missed、最近24期）；最新一期待结算记 pending_period，不计入分母/漏期；分母 = 窗口内该框架已上线期数，计划命中率 = 命中数/分母（漏期按 0 命中）；旧字段零变化。
- ops_health 新增 check_coverage（23→24 项）：coverage_24h<95% WARN；<80% 仅当缺口延续到最近 24 期（仍漏≥3期）才 FAIL，已恢复历史缺口只 WARN。
- 面板「整组命中率 vs 随机期望」表新增「覆盖率(24h)」「计划命中率(24h)」两列 + 发布/计划命中率口径说明（漏期算 0、历史缺口来源）。
- 实测 as_of 20261101010（pending 20261101011）：kill2 100%（online 27）、killer/framework/cold/kill3 77–80%（全部 recent_missed=0、last_missed 为历史期，缺口随 24h 窗口滚出后自动转 OK）。
**③ kill2 v3 五项目径只读影子（commit 711ef7b）**：新增 scripts/run_kill2_v35_shadow.py，用同一批封存号码重算「2 counted 位置 + 合尾 + 2 反向形态 = 5 项」（he2/he3 仅展示不计入；机械基线 61.91%），与现行 7 项（57.26%）配对；输出 data/kill2_v35_shadow.json（backfill/forward/gate/periods），不新增 AI 请求、不改现网封存/结算/面板口径；cron 每 5 分钟（sleep 45，flock penta5-kill2v35）已安装，logrotate 已加日志。首跑：backfill n=24 五项 12/24=50.00% vs 七项 11/24=45.83%（Δ+4.17pp，机械预期 +4.65pp 量级）；forward 自 20261101008 起累计 n=4（5/7 项均 2/4）。面板 KILL_TWO 区块已显示只读「影子对照 v3」行与门禁状态（burn-in，需 forward n≥100 才进入转正评审；脚本绝不自动切换现网）。
**备注**：并行 DSH 窗口正在开发 kill1_tail（未提交），本批 commit 只含本任务文件；ops_health 的 kill3_freeze WARN 来自该并行窗口对 lib/panel_context.py 的未提交改动，非本批引入。

## 2026-11-01 01:05 新框架 kill1：AI 单位置杀码 + 一合尾 + 一形态（独立封存/统计）

用户要求「从四个位置选一个位置排除一个号码，从五个合尾模型取一个排除，排除一个形态」，并用 ask_user_question 钉死口径：**整组 3 项 = 1 位置 + 1 合尾 + 1 形态**；形态由 AI 从单双/大小反向形态中选 1 种；尾码沿用 hold3-until-miss；位置/杀码**不做连用限制**（用户选「不限制，完全由模型选」）。

- **口径（写死）**：DeepSeek 从 千/百/十/个 四位置中选恰好 1 个、排除 1 码（位置 1 注/期）；五合尾（千百十个/千百十/千百个/千十个/百十个）选 1 种排除 1 个和尾数字（hold3-until-miss：上期命中且沿用未满 3 期则沿用，失手或满 3 期后模型重选）；从 reverse_parity（四位单双）/reverse_size（四位大小）中选 1 种输出 4 字符，被杀位置取杀码形态反面、其余 3 位 AI 自选；整组命中 = 3 项同时命中；只用前 4 球；只统计开奖前发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零。
- **RULE_VERSION**：kill1-tail-v1-one-position-one-form（独立封存 data/kill1_tail/{seals,settlements}.json，独立 AI 请求 PENTA5_MODEL_KILL1 优先，独立攻击语，独立面板区块 kill-one-ai）。
- **随机基线**：整组 3 项精确期望 **0.7538**（10^4 均匀配置枚举平均；位置 90%、合尾 90%、四位反向形态 93.75%，位置项与形态项负相关修正后 0.75375，不是简单乘积 0.7594）；单项基线 90% / 90% / 93.75%。
- **改动文件（repo 内）**：新增 lib/kill1_tail.py、lib/kill_one_view.py、scripts/run_kill1_tail.py；改 lib/panel_context.py（可选 kill1_stats/kill1_previous → kill1_self）、lib/hitrate_baseline.py、scripts/framework_hitrate_cumulative.py、scripts/ops_health_check.py、lib/model_usage.py、lib/evidence_backup.py、lib/attack_editor_view.py、lib/panel_live_refresh.py、scripts/tail_hold_review.py、ops/crontab.txt、ops/kill3-freeze.json、ops/README.md。
- **12 处挂接点全中**：1) /root/run_stats.py import + 2 处调用链；2) panel_context.build kill1_self；3) hitrate_baseline RULE_BASELINE (3, 0.7538)；4) framework_hitrate_cumulative cum_kill1；5) ops_health check_kill1；6) model_usage SOURCE_LOGS；7) evidence_backup JSON_DIRS；8) attack_editor 注册 kill1 攻击语（版本升 all-attack-editor-v8-kill1）；9) panel_live_refresh names 加 KILL_ONE；10) tail_hold_review 第 3 框架条目；11) ops/crontab.txt 每 5 分钟行（sleep 35，flock /var/lock/penta5-kill1t.lock，log data/kill1_tail_cron.log）；12) /etc/logrotate.d/penta5 加 kill1 日志。
- **行为保持证明**：lib/panel_context.py 改动用旧版备份模块 vs 新版模块做固定 fixture A/B：kill3 snapshot/prompt_text sha256 = 2492ed52b8f5a36d…、kill2 = 65cb1b363cf79a81… 逐字节相同，仅 kill1 调用新增 kill1_self；6 个挂接点文件在冻结 data 快照上对比改前备份模块 vs 改后模块 build()，既有 7 行/6 框架逐字段完全相等（证据 /root/ops/kill1-hooks-20261101-005500/compare_behavior.log）。
- **首期真实证据**：ref **20261101013** → target 20261101014，published **2026-11-01T01:05:54+08:00**（deepseek-flash 16.6s，prompt 3869 tokens / reasoning 2464），封存 positions {g:9}、tail tail_qsg 杀3、form_type reverse_parity 单双单双，tail_policy=新选（无上一期尾码）；攻击语「千1234567890百1234567890十1234567890个123456780四定千十个合124567890除单双单双」。target 20261101014 于 01:10:12 开奖后结算：actual [9,4,8,3]，位置 个位杀9 实际3 命中、合尾 千十个 9+8+3=20→0≠3 命中、形态 实际「单双双单」≠封存「单双单双」命中，**整组 3 项全中 group_hit=True、verified=True**。
- **第二期（尾码稳定化）**：ref 20261101014 → target 20261101015，published 01:10:48，千位杀3 · 千十个合尾杀3 · 单双反向形态 双单单单，tail_policy=沿用上期尾码（第1期，上期命中，reused_previous=true）。
- **面板**：kill-one-ai 区块标记 START/END 与 section 各 1、data-as-of 20261101014 == 最新封存 ref；3 张卡 + 统计行「整组命中率（3 项全中：1 位置 + 合尾 + 1 形态）100.00%（1/1 期）」+ 连中 1 期 + 口径追溯「整组精确基线 75.38%（3 项）」+ 攻击语 textarea 文本 == build_attack(seals[-1])；两次 publish + 流水线重建后仍各 1（幂等）。
- **ops_health**：kill1_tail **OK ref=20261101014 published_at=01:10:48**；kill3_freeze OK（panel_context.py hash 已按变更门禁更新，hash_updates 记录 commit 1e96ae5 与 A/B 证据）。
- **commit**：1e96ae5（核心框架 kill1_tail/kill_one_view/run_kill1_tail/panel_context）、052be4f（6+2 挂接点注册）、07dbf42（kill3 冻结守卫 panel_context hash）、本批（cron + logrotate + 台账）。
- **repo 外改动**：/root/run_stats.py（import + 2 处调用链；备份 /root/ops/kill1-framework-20261101-005247/run_stats.py）；/etc/logrotate.d/penta5（加 kill1 日志；备份同目录 penta5.logrotate 另存于 /root/ops/kill1-framework-20261101-005247/crontab.txt 同级）；crontab 通过 scripts/crontab_tool.sh install 安装（版本化文件 ops/crontab.txt，diff 无差异）。
- **成本提示**：kill1 每期 1 次 DeepSeek 请求（288 次/日）；ops_health 首查时余额 8.54 元（按近 1h 扣费速率仅够约 1.6 小时），01:13 复测时用户已充值至 25.92 元；余额守护会在低余额时自动停 AI 总闸。
- **回滚**：git revert 1e96ae5 052be4f 07dbf42 <本批>；/root/run_stats.py 回拷 /root/ops/kill1-framework-20261101-005247/run_stats.py；/etc/logrotate.d/penta5 删除 kill1 日志行（或回拷 /root/ops/kill1-framework-20261101-005247/ 内对应备份）；crontab 删除 kill1 行后 bash scripts/crontab_tool.sh install；data/kill1_tail/ 历史封存/结算保留不动（回滚后新期不再写入）。

## 2026-11-01 02:08 彻底退役 cold_framework（AI 冷码七项框架）

用户发图要求「撤除这个框架」（图中「DeepSeek Pro · 冷码七项框架」= cold_framework v3 30期冷码七项），按 cold_fixed（2026-10-31）退役同规格彻底撤除；**其它冷码块保留**：冷码杀号战绩（cold-hit-stats）、位置冷码（position-cold）、冷码观察、冷码回测每 6 小时 cron，以及 cold50/cold50_fixed 休眠代码。

- **关停运行**：ops/crontab.txt 删除 cold 每 5 分钟行（flock penta5-cold，AI 每期请求）并 `scripts/crontab_tool.sh install`（diff 一致，32→31 行）；/etc/logrotate.d/penta5 删除 data/cold_framework_cron.log 行；lib/ai_runtime_jobs.py 从 AI 作业入口移除 run_cold_framework.py / cold_framework_model.py。
- **面板移除**：lib/model_retirement.py 新增 COLD_MARKERS，run_stats 每次重建时剥除旧 COLD_FRAMEWORK 区块（旧块已一次性剥除）；panel_live_refresh（names/hooks）、attack_editor（v9-retire-cold-framework）、panel_delivery_timing、operational_availability、runtime_observability(_view) 全部去 cold 注册（cold50/cold50_fixed 休眠条目保留）。
- **派生统计移除**：hitrate_baseline、framework_hitrate_cumulative（RULE_BASELINE/LABELS/WINDOWS/_cold_row/cum_cold 及仅 cold 使用的常量）、tail_hold_review（「冷码七项」）、model_usage（cold 日志源）、ops_health（cold status/402 日志）、evidence_backup（cold 账本 REQUIRED_DATA 与 cold 日文件必含校验）。重生成后 hitrate/cumulative **rows 8→7**、tail_hold_review **7→6 框架**、model_usage 7 源。
- **面板上下文（冻结受控）**：lib/panel_context.py 删除 _cold_frame 与 frameworks.cold_framework 注入。A/B 证明：kill1/kill2/kill3 固定 fixture 下新版 snapshot 与旧版逐字节相同、仅去掉 frameworks.cold_framework 一个键；kill3 RULE_VERSION/选码口径未变。ops/kill3-freeze.json 已按变更门禁更新 hash（commit a950546，hash_updates 记录 A/B 证据）。
- **代码删除**：commit **7ce1814** 含 git rm lib/cold_framework.py、lib/cold_framework_model.py、lib/cold_framework_view.py、lib/cold_tail_hold.py、scripts/run_cold_framework.py；全仓 .py/.txt/.sh 无 cold_framework 业务引用（仅 model_retirement 的 COLD_MARKERS 常量用于剥除旧块，保留）。
- **数据归档**（不入 git）：/root/archive/retired-20261101-cold-framework/cold-framework-data.tar.gz（37 文件：每日预测/封存、attempts、cron 日志、ledger、status、lock，含 lib/data 下同源残留；原 12MB 压缩 1.1MB，sha256 eb3d802b…），同目录 README.md 记录回滚步骤；改前文件备份 /root/ops/cold-retire-20261101-021406/（含 /root/run_stats.py）。
- **行为验证**：面板 panel.html 0 个 COLD_FRAMEWORK 标记、0 个「冷码七项框架」；ops_health 无 cold 项、hitrate_cumulative rows=7 OK；撤除后真实周期 ref 20261101029（02:25）kill3/kill2/kill1 三个 AI 框架均照常 published（02:25:40/50、02:25:44）。旧模块 vs 新模块同数据 A/B：hitrate/cumulative 存活行、tail 其余 6 框架逐字段完全相同。
- **回滚**：git revert 7ce1814 a950546 本提交；回拷 /root/ops/cold-retire-20261101-021406/ 下 20 个文件（含 run_stats.py）与 /etc/logrotate.d/penta5；tar -xzf cold-framework-data.tar.gz -C /root/penta5-runner/；ops/crontab.txt 加回 cold 行后 crontab_tool.sh install；重启无必要（run_stats 每轮重读源码）。


- **2026-11-01 02:33–02:38：命中率归因审计后的逻辑修复（commit 0fe6900 / 1f66a68）**
  - **审计口径**：整组命中 = 一期全部项同时命中；随机期望用 10^4 开奖空间对每条封存号码逐条精确枚举（不用单项 90%、不用概率相乘）；逐项用自身分母；双侧 p；只用已结算记录。
  - **结论（数据侧，只读）**：8 框架中 framework 55.64% vs 精确基线 55.68%（p=0.96）、selected_six v7 49.65% vs 57.37%（-7.73pp，p=0.0635，n=141 样本不足）、killer 39.60% vs 41.72%（-2.12pp，p=0.0394，197 项多重比较下 p<0.05 条数无超额）、he3/kill3/kill2/kill1 均样本不足；selected_six v3 的 0/9 已解释（v3 两项反向形态各 25%，整组逐期精确基线仅 4.78%，9 期全不中概率 64%，p=0.50，非 bug）；全账本 outcomes/group_hit 用封存号码重算 0 不一致，跨账本同期开奖号 0 不一致。**没有证据支持「模型退化/结算算错」**。
  - **修复 1（fix(predict) 0fe6900）**：killer/framework 的 joint_form 联合形态优化原先在 tail-hold 之前执行，tail-hold 再改写合尾 → 封存形态不是最终合尾下的最优，审计按最终封存重算的 expected_opt 会略高于实盘（转正后 114 期中 killer 有 17 期发布形态 != shadow 重算最优）。改为先 tail-hold 再 optimize（主判断不变，仅辅助反向形态）。同时给 killer/framework/selected_six 的 hold3 加漏期门禁（上一封存 target_period 必须等于当前参考期，否则不沿用）——kill3/kill2 已有按 target_period 查结算的实现。
  - **修复 2（fix(stats) 1f66a68）**：`scripts/framework_hitrate_cumulative.py` 的 `_joint_expected()` 把 shadow records 的 ref 键当 target 映射，导致每期期望错位一期、最新 42/41 期回落旧常量；改为按 `record['target_period']` 映射后 killer 2295/2295、framework 2290/2290 期全部解析到逐期期望。
  - **真周期证据**：ref 20261101031（02:35 开奖后）日志顺序为 `TAIL_HOLD3` → `JOINT_FORM_OPTIMIZED`（修复前相反），framework 封存形态 `双双单双/小小大大`、killer `双双双双/小小小小` 均等于当次日志 opt；累计报告重跑后 7 行正常、as_of 20261101030。
  - **回滚**：`cp -a /root/ops/fix-20261101-0233/backups/lib/auto_r1_killer.py /root/penta5-runner/lib/auto_r1_killer.py`（framework 同）；`cp -a /root/ops/fix-20261101-0233/backups/scripts/framework_hitrate_cumulative.py /root/penta5-runner/scripts/framework_hitrate_cumulative.py`；`cp -a /root/ops/fix-20261101-0235/backups/lib/{killer_tail_hold,framework_tail_hold,six_tail_hold}.py /root/penta5-runner/lib/`。代码回滚后不重启（每期新进程加载），下一期生效。
  - **遗留（未改，供下版决策）**：① kill3 settlements 150/320 无 rule_version、54/320 无 counted（旧口径，当前 v8 不受影响，累计报告 fallback 用封存 rule_version）；② killer/framework 账本无 revision 字段，规则名靠 strategy_comparison_stats 推断；③ kill3 v8 position_q 45/59=76.27%（p=0.0004）是唯一 p<0.001 单项，但整组 p=0.80、hold3 使 46% 相邻期同码重复计数，需 ≥200 独立期复核；④ selected_six 尾码 hold 在 validator 之后改写（marginal 无偏，但 strategy_validation 描述的不是最终尾码）。

## 2026-11-01 02:38 彻底清理冷码家族（cold family）

用户 02:38 指令「彻底清理」。在 02:08 已退役 cold_framework（冷码七项 AI 框架）之后，把剩余所有冷码相关
面板块、派生任务、守护进程、代码、数据与 cold50/cold50_fixed 休眠残留全部清掉；**证据归档与 git 历史保留**，
其它框架（killer/framework/selected_six/kill1-3/he3/stat50/位置/显著性/真实命中率等）不动。

- **停服务/任务**：systemd `penta5-position-cold.service`（scripts/position_cold_daemon.py，enabled+active）stop + disable +
  删除 unit + daemon-reload（现 inactive/无 unit/无进程）；cron 冷码回测（10 */6，cold_backtest.py）删除并
  `scripts/crontab_tool.sh install`（31→30 行，diff 一致）；`/etc/logrotate.d/penta5` 删除 cold_backtest/cold_hit_stats 两条日志行。
- **面板移除**：COLD_OBSERVATION（冷码观察 · 近30期每项第1名）、COLD_HIT_STATS（冷码杀号战绩）、
  POSITION_COLD（位置冷码）三块移除（一次性剥除 + `model_retirement.COLD_MARKERS` 每次重建兜底剥除）；
  panel_live_refresh names/hook 与 cold50/cold50_fixed 选择器、attack_editor 的 cold50 攻击语定义
  （版本升 v10-clean-cold-family）一并清理。
- **代码删除**：commit **b0319a5** git rm 10 文件：lib/cold_observation.py、cold_observation_view.py、
  cold_hit_stats_view.py、position_cold_view.py、cold50_observation.py；scripts/cold_backtest.py、cold_hit_stats.py、
  position_cold_daemon.py、position_cold_report.py、kill_three_pick.py。通用面板标记解析抽到新
  `lib/panel_markers.py`（ai_control_view / runtime_observability_view 继续使用，行为差分 11 组用例一致）。
- **共享模块**：`/root/run_stats.py`（repo 外，备份 `/root/ops/cold-family-20261031183554/run_stats.py`）删除
  position_cold/cold_hit_stats/cold_observation 三个 import、cold_observation 阶段与面板链两个调用；
  runtime_observability(_view)、panel_delivery_timing、operational_availability 删除 cold50 家族死代码
  （-227/+14 行，固定 clock 旧/新模块 collect() 对 killer/framework/selected6/he3 等价）；
  5 个非冻结 view（kill_two/kill_one/hitrate_baseline/real_hitrate/model_signi）删除已失效的
  COLD_HIT_STATS_END/POSITION_COLD_END 回退锚点。**冻结文件 lib/kill_three_view.py 按冻结纪律未动**
  （其中 2 处死锚点字符串仅在标记缺失时才尝试、当前永不命中）；model_retirement 的 COLD_MARKERS 保留作退役兜底。
- **数据归档**（不入 git）：`/root/archive/retired-20261101-cold-family/cold-family-data.tar.gz`
  （19 文件：cold_backtest*、cold_hit_stats*、cold_observation.json、position_cold_report.*、kill_three_pick.json、
  cold_he3_repair_audit.jsonl、lib/data/cold50* 采集残留；sha256 3d1b84de…，原 ~100KB→12KB），同目录 README 记录回滚；
  改前备份：`/root/ops/cold-family-20261101-023634`（10 被删文件）、`/root/ops/cold-family-20261101-024106`（7 共享文件）。
- **验证**：panel.html 三块标记与「冷码观察/冷码杀号战绩/位置冷码」标题均为 0，KILL_ONE/KILL_TWO/KILL_THREE/
  MODEL_SIGNI/REAL_HIT 等块各 1；全仓 `python3 -m compileall lib scripts` 通过；import smoke 通过；
  panel 变换链 dry-run 幂等且 0 冷码 token；hitrate/cumulative 重生成 rows=7（kill1 20 期 15 中 75.00% vs 期望 75.38%）；
  runtime_observability cron 自 02:41 起每分钟 success、panel-sync 无新报错。
- **回滚**：git revert b0319a5（+ 若需一并回滚 7ce1814/a950546/79fb363 见前两节）；代码回拷
  `/root/ops/cold-family-20261101-023634/` 与 `/root/ops/cold-family-20261031183554/`；数据解档
  `tar -xzf /root/archive/retired-20261101-cold-family/cold-family-data.tar.gz -C /root/penta5-runner/`；
  systemd：unit 文件在备份目录，拷回后 daemon-reload + enable --now；crontab/logrotate 行按备份恢复后 install。


## 2026-11-01 03:05 收尾：面板影子脚注清零
复核 02:08 退役结果时发现 `lib/tail_stability_shadow.py` 的只读影子脚注仍列「冷码七项（22:10，pair-hold3）」，
`_collect()` 排序元组也仍含该键（已无数据，不渲染行）。已删除两处键/字样并同步注释；重新生成
TAIL_STABILITY_SHADOW 区块后 panel.html 中「冷码七项」「冷码七项框架」「COLD_FRAMEWORK」均为 0。
改前备份：/root/ops/cold-retire-final-20261101-030538/（panel.html、tail_stability_shadow.py、README、
import_from_state.js 及 SHA256SUMS.before）。
`api/import_from_state.js` 的 cold_framework_json / cold_framework_success 是 RDS 落库列，已无生产方
（预测对象不再有 coldFramework 字段，写入恒为 NULL），不展示、不进统计，随 schema 保留。

## 2026-11-01 03:53 按用户要求保留/恢复冷码统计模块

用户 03:53 指令「相关统计模块留着」：02:38 的「彻底清理」应只针对 cold_framework（AI 冷码七项框架），
**冷码观察 / 冷码杀号战绩 / 位置冷码 / 冷码回测（position_cold 守护进程）这些统计模块要保留**。据此恢复，
cold_framework 及其面板区块仍保持退役。

- **恢复代码（commit 5c62a34）**：`git checkout b0319a5^ -- ` 恢复 lib/cold_observation.py、
  cold_observation_view.py、cold_hit_stats_view.py、position_cold_view.py；scripts/cold_backtest.py、
  cold_hit_stats.py、position_cold_daemon.py、position_cold_report.py、kill_three_pick.py（10 文件原样）；
  lib/model_retirement.py 恢复为只剥除 COLD_FRAMEWORK（不再剥除三个统计块）。
- **恢复运行（commit 18c1055 + repo 外）**：systemd `penta5-position-cold.service` enable --now
  （active，position_cold_daemon 每期重算 position_cold_report 与 cold_hit_stats，实测 04:01:13
  trigger=new-period=20261101048 elapsed=12.33s）；`/root/run_stats.py`（repo 外，备份
  `/root/ops/cold-family-20261031183554/run_stats.py`）恢复 position_cold_view / cold_hit_stats_view /
  cold_observation_view 三个 import、cold_observation 阶段与面板链两个 apply 调用（保留并行窗口 kill2 退役改动）；
  `/etc/logrotate.d/penta5` 恢复 cold_backtest/cold_hit_stats 两行；ops/crontab.txt 恢复冷码回测 10 */6 行并
  install（32 jobs，diff 一致）；panel_live_refresh names 恢复三块名；数据由
  `/root/archive/retired-20261101-cold-family/cold-family-data.tar.gz` 解档恢复。
- **保持清理（未恢复）**：cold_framework 全部（见 7ce1814/a950546/79fb363 与 02:38 清理节）、
  cold50/cold50_fixed 休眠残留、attack_editor 的 cold50 攻击语定义、runtime_observability / panel_delivery_timing /
  operational_availability 中 cold 家族死代码、5 个视图的 COLD_* 回退锚点字符串（不影响功能）。
- **验证**：panel.html COLD_OBSERVATION / COLD_HIT_STATS / POSITION_COLD 三块各 1（服务器每轮 run_stats 重建后仍在）；
  cold_observation.json sample_count=30 stats_error=false；cold_hit_stats.json / position_cold_report.json 由
  position_cold_daemon 按新期号刷新；py_compile + import smoke 通过；REFRESH_SCRIPT 三块名各 1 且 install 幂等。
- **回滚**：`git revert 18c1055 5c62a34`；run_stats 回拷 `/root/ops/cold-family-20261031183554/run_stats.py`；
  systemctl disable --now + 删 unit（备份在 `/root/ops/cold-family-20261031183554/`）；crontab/logrotate 按备份回退；
  数据无需处理（已解档回 data/）。


## 2026-11-01 03:45 彻底退役 kill2_tail（AI 二位置杀码 + 一合尾 + 二数合/三数合）

用户发图指令「把这个模型彻底清理了」。图中卡片 = 面板区块「🤖 AI 二位置杀码 + 一合尾 + 二数合/三数合建议」=
kill2_tail（现行 RULE_VERSION `kill2-tail-v2-three-select-two-counted`，v3.5 五项影子 `kill2-tail-v3-five-item-2pos-tail-2pattern` 同批退役），
按 cold_framework / cold_fixed 退役同规格执行；其余框架（killer / framework / selected_six / he3 / kill1 / kill3 / stat50 等）不动。

- **关停运行**：`ops/crontab.txt` 删除 `run_kill2_tail.py` 与 `run_kill2_v35_shadow.py` 两行（34→32 行），
  `scripts/crontab_tool.sh install` 后 `diff <(crontab -l) ops/crontab.txt` 无差异；`/etc/logrotate.d/penta5` 删除
  `data/kill2_tail_cron.log`、`data/kill2_v35_shadow_cron.log` 两行（103→101 行，`logrotate -d` 干跑通过）；systemd 无 kill2 unit、无 kill2 进程。
  `/root/run_stats.py`（repo 外）去掉 `kill_two_view` import 与 2 处 `apply_kill_two_block` 调用链；该文件当前 sha256 `d37c72a9…`，
  系并行窗口恢复冷码统计时保留 kill2 退役（本批未回退冷码恢复）。
- **面板**：`lib/panel_live_refresh.py` names 去 `KILL_TWO`；`lib/attack_editor_view.py` 删除 kill2 攻击语定义并升版本
  `all-attack-editor-v11-retire-kill2`；`panel.html` 一次性剥除 `<!-- KILL_TWO_START -->…<!-- KILL_TWO_END -->` 区块
  （重新生成后 `KILL_TWO`/「AI 二位置杀码」计数 0，KILL_THREE/KILL_ONE 各 1）。
  `lib/kill_one_view.py` 面板脚注去掉 kill1 区块里对 kill2 的引用与 KILL_TWO_END 插入锚。
- **派生统计逐项减一**：hitrate_baseline rows **7→6**、framework_hitrate_cumulative rows **7→6**（as_of 20261101048）、
  tail_hold_review 框架 **6→5**、model_usage 日志源 **7→6**（`data/model_usage/state.json` 位点同步裁剪；历史 ledger 不删）、
  ops_health 检查项 **29→28**（去 kill2_tail；FAIL 项与撤除前相同：coverage / kill3_stability）、evidence_backup `JSON_DIRS` **5→4**。
  `data/hitrate_baseline.json`、`framework_hitrate_cumulative.json`、`tail_hold_review.json`、`hitrate_guard.json` 已重生成，其余框架行逐行相同。
- **面板上下文（冻结受控）**：`lib/panel_context.py` 删除 `_kill2_self`、`kill2_stats/kill2_previous` 入参与 `kill2_self` 键；
  A/B 证明：kill1/kill3 固定 fixture 下新版 `build()+prompt_text()` snapshot sha256 与旧版逐字节相同（kill3 `4108cc37…`、kill1 `326a9146…`），
  kill3 RULE_VERSION / 选码口径未变。`ops/kill3-freeze.json` 已按门禁更新 hash（commit `6238ba0`）。
- **代码删除**：commit `0f2df82` git rm `lib/kill2_tail.py`、`lib/kill_two_view.py`、`scripts/run_kill2_tail.py`、`scripts/run_kill2_v35_shadow.py`
  （全仓 .py 业务引用清零，仅 attack_editor 版本串 `v11-retire-kill2` 与历史台账保留该字样）。
- **数据归档**：`/root/archive/retired-20261101-kill2/kill2-data.tar.gz`（8 条目：`data/kill2_tail/{seals,settlements}.json`、
  `kill2_tail_cron.log(.1)`、`kill2_v35_shadow.json`、`kill2_v35_shadow_cron.log`、`.kill2_tail_job.lock`；
  sha256 `739742b69600e82dbe7f0ea5fbdf8204ee78026b5772e9188c41d42131bc3f34`），原文件已删除；同目录 `README.md` 记录回滚步骤。
  改前备份 `/root/ops/kill2-retire-20261101-034721/`（panel_context.py、run_stats.py、run_stats.rollback.py、panel.before.html、
  before_hitrate.json、before_cumulative.json、ops_health.json、ab_behavior.json、crontab.txt、penta5.logrotate 等）。
- **并行窗口**：03:59 另一 DSH 窗口按用户要求恢复冷码统计模块（commit `5c62a34`），已保留 kill2 退役、未恢复 kill2；本批不涉及冷码恢复。
- **验证**：撤除后真实期 ref `20261101049`（04:05 开奖后）kill3 / kill1 均照常 **published**
  （kill3 04:06:14、kill1 04:05:59，target 20261101050）；`panel.html` KILL_TWO/「AI 二位置杀码」=0、KILL_THREE/KILL_ONE=1；
  `ops_health` 检查项 29→28 且 FAIL 项不变；manifest status=no-drift；`data/` 下仅历史 ledger 与 kill1/kill3 不可变封存快照内还留有 kill2 字样（历史记录，不回写）。
- **commit**：`0f2df82`（退役主体）、`6238ba0`（kill3 冻结 hash）、本批（台账）。
- **回滚**：`git revert 6238ba0 0f2df82 <本批>`；回拷 `/root/run_stats.py`（用 `/root/ops/kill2-retire-20261101-034721/run_stats.rollback.py`）、
  `/etc/logrotate.d/penta5` 加回两条日志行；`crontab` 加回两行后 `scripts/crontab_tool.sh install`；
  `tar -xzf /root/archive/retired-20261101-kill2/kill2-data.tar.gz -C /root/penta5-runner/`；面板与派生 JSON 由下一轮 run_stats / 各路脚本重生成。
