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

- **补充（04:08 复验）**：并行窗口恢复冷码 logrotate 条目时把 `data/kill2_tail_cron.log`、`data/kill2_v35_shadow_cron.log` 两行一并带回
  （`/etc/logrotate.d/penta5` 行 72/73），已再次删除并复验 0 行、`logrotate -d` 通过；该文件其余冷码条目保留。
  另：并行窗口正在开发新框架 `kill2p_tail`（二位置 + 一合尾 + 两反向形态，WIP 未提交），其新文件注释里提到「kill2 已退役」属说明性文字，不是功能引用；
  该窗口的 kill3 v9 / panel_context 在途改动会临时令 ops_health `kill3_freeze` WARN，与本退役无关。

## 2026-11-01 04:10 kill3 升 v9「3 位置展示 / 2 个计入 + 保留尾码 hold3」（用户指令恢复 3 选 2）

用户 2026-11-01 03:39 指令「帮我恢复回前面那一版」，04:0x 明确「恢复希望巅峰那个模型，从三个位置选择俩个」，
并按澄清选择「保留尾码 hold3，只把计入改成 3 选 2」。执行：RULE_VERSION 升 **kill3-tail-v9-two-counted-hold3**
（3 个被杀位置全部展示，AI 用 display_only 指定 1 个仅展示，另外 2 个计入整组与攻击语；整组 = 2 计入位置 + 1 合尾 + 2 反向形态 = 5 项；
位置 2 注/期；尾码 hold3-until-miss 不变；旧 v1–v8 封存原样保留、不计入本版统计，新版从 0 累计）。

- 改动文件（commit **2b0e841**）：lib/kill3_tail.py、lib/kill_three_view.py（提示词 / 校验 / display_only 计入 / 封存字段 / 面板文案 / 统计口径）。
- 备份与回滚：/root/ops/kill3-ai-v9-040704/（6 文件 + seals/settlements 快照 + ROLLBACK.md）。
- 精确基线：5 项 61.91%（沿用 v7 同口径登记值；用 74 条 v8 已发布封存做 10^4 逐条枚举复核均值 62.08%，差 0.17pp 属配置抽样误差）。
- 合成用例：/tmp/kill3v9/test_kill3_v9_repo.py 36 项 PASS（display_only 合法/缺失/越界三分支、counted 恰为另外 2 个、攻击语 2x9码+2x10码、sandbox 结算 display_only 失手不影响整组、5 项渲染文案、旧封存兼容）。
- 真实周期证据：ref 20261101050 -> 20261101051 于 04:10:43 published（display_only=g、counted=[q,b]）；
  ref 20261101051 -> 20261101052 于 04:15:59 published（display_only=s、counted=[q,b]）；
  首个 v9 结算 target 20261101051（实际 6835）2 个计入位置 + 合尾 + 两形态全中、group_hit=True，
  面板显示「整组命中率（5 项全中：2 位置 + 合尾 + 2 形态）100.00%（1/1 期）· 位置杀码（2 注/期）2/2」。
- 冻结守卫：ops/kill3-freeze.json 已把 rule_version 改为 v9、重置冻结戳（frozen_at 04:10:43、freeze_ref_period 20261101050、commit 2b0e841）
  并重算受控文件 hash；panel_context.py 的 kill2p 在途改动由并行窗口 A/B 登记（其 commit 后更新该 sha）。
- 回滚：cp -f /root/ops/kill3-ai-v9-040704/kill3_tail.py lib/、kill_three_view.py lib/、hitrate_baseline.py lib/、
  framework_hitrate_cumulative.py scripts/、kill3-freeze.json ops/，再 python3 -m py_compile；无需重启服务（cron 每轮新解释器加载，面板每轮重建）。
- 注意：hitrate_baseline.py / framework_hitrate_cumulative.py 中的 v9 基线登记（5, 0.6191）已写入线上并在本轮统计生效，
  但这两个文件同时被并行窗口（kill2p 新框架）改动，其提交会一并带上本登记。

## 2026-11-01 04:15 新框架 kill2p：AI 二位置杀码 + 一合尾 + 两反向形态（5 项，独立封存/统计）

用户 2026-11-01 03:46 指令「给我弄一个排除模型，结合几十期数据，从四个位置选择两个位置排除，还有一个合尾」，
经 ask_user_question 钉死口径：**新建独立框架**、整组 **5 项 = 2 位置 + 1 合尾 + 2 反向形态**、历史窗口 **近 50 期**、
合尾 **五选一 + 杀 1 尾 + hold3-until-miss**、位置/杀码 **不做连用限制**。

- **口径（写死）**：DeepSeek 从 千/百/十/个 四位置中选恰好 2 个、各排除 1 码（位置 2 注/期；其余 2 位不参与定位项、攻击语保留 10 码）；
  5 种合尾选 1 种排除 1 个和尾数字（hold3-until-miss，与 kill1/kill3 同口径）；
  两项四位反向形态 reverse_parity（单双）/reverse_size（大小），被杀 2 位取杀码形态反面、其余 2 位 AI 自选；
  整组命中 = 2 位置 + 1 合尾 + 2 形态共 5 项同时命中；无位置/杀码连用限制（用户明确「完全由模型选」）；只用前 4 球；北京时间 05:00 清零。
- **RULE_VERSION**：kill2p-tail-v1-two-positions-one-tail-two-forms；独立封存/结算 data/kill2p_tail/{seals,settlements}.json；
  独立 AI 请求（PENTA5_MODEL_KILL2P > PENTA5_MODEL_KILL3 > PENTA5_MODEL_KILLER > PENTA5_DEEPSEEK_MODEL）；
  独立面板区块 kill-two-p-ai（KILL_TWO_P_START/END、names KILL_TWO_P）；独立攻击语 kill2p-attack-*（编辑器 all-attack-editor-v12-kill2p）。
- **整组随机基线**：10^4 开奖空间 x 80,000 均匀配置精确枚举 = **0.620302 -> 0.6203（62.03%）**；
  单项相乘 0.9x0.9x0.9x0.9375x0.9375 = 0.6407 偏乐观，已否定。枚举方法用 kill1 3 项 0.75375 与 kill3 5 项口径交叉验证。
- **模块测试**：服务器 /tmp/kill2p_module_test.py **41 PASS / 0 FAIL**（normalize 恰好 2 位置/形态反面校验/build_attack/结算 5 项/today_stats/hold3 复用与满 3 期）。
- **A/B 行为保持**：lib/panel_context.py 仅新增可选 kill2p_stats/kill2p_previous 入参与 kill2p_self 键；新旧模块固定 fixture 下
  kill3 snapshot sha **0fda1290…**、kill1 **0383c3ac…**、noargs **3b33018a…** 逐字节相同；带 kill2p 入参仅新增 kill2p_self。
  8ef648f 的 kill3 v9 冻结戳已把本改动 hash **36fa7154…** 登记进 ops/kill3-freeze.json files。
- **12 处挂接点**（grep 计数见验收）：/root/run_stats.py（import + 2 处 update_panel 链）、panel_context、hitrate_baseline、
  framework_hitrate_cumulative、ops_health（check_kill2p + cron 日志）、model_usage、evidence_backup、attack_editor、panel_live_refresh、
  tail_hold_review、ops/crontab.txt（每 5 分钟 sleep 40、flock /var/lock/penta5-kill2pt.lock）、/etc/logrotate.d/penta5。
- **首期真实证据**：ref **20261101051** -> target **20261101052**，published **2026-11-01T04:15:51+08:00**（deepseek-flash 8.2s，
  prompt 3502 tok / reasoning 1194）；封存 positions {q:3, b:6}、tail 千百个 杀1、单双 双单双单、大小 大小大小；
  攻击语 `千124567890百123457890十1234567890个1234567890四定千百个合234567890除双单双单除大小大小`；
  面板 kill-two-p-ai data-as-of 与最新封存一致、攻击语 textarea 与 build_attack(seals[-1]) 逐字节一致。
- **首期结算**：target 20261101052 开奖 [0,1,8,5]：2 位置命中（千3->0、百6->1）、合尾命中（千百个 0+1+5=6≠1）、
  大小命中（实际「小小大大」≠封存「大小大小」）、单双未中（实际「双单双单」= 封存），**整组 group_hit=False、verified=True（4/5 单项命中）**。
- **第二期**：ref 20261101052 -> target 20261101053，published 04:20:53（9.96s）；尾码按 hold3 沿用上期（上期命中、第 1 期），
  位置/形态沿用（无连用限制），攻击语与第一期相同——符合「完全由模型选」口径。
- **派生统计**：framework_hitrate_cumulative 04:22 生成已含 kill2p_tail 行（5 项/0.6203）；hitrate_baseline 下一轮 hitrate_guard 起含 kill2p_tail 行；
  ops_health kill2p_tail **OK ref=20261101051 published_at=04:15:51**。
- **改动文件**：新增 lib/kill2p_tail.py、lib/kill_two_p_view.py、scripts/run_kill2p_tail.py；改 lib/panel_context.py、lib/attack_editor_view.py、
  lib/panel_live_refresh.py、lib/hitrate_baseline.py、scripts/framework_hitrate_cumulative.py、scripts/ops_health_check.py、lib/model_usage.py、
  lib/evidence_backup.py、scripts/tail_hold_review.py、ops/crontab.txt、ops/kill3-freeze.json、ops/README.md；repo 外 /root/run_stats.py
  （备份 /root/ops/kill2p-framework-20261101-040624/run_stats.py）、/etc/logrotate.d/penta5（备份同批）。
- **commit**：063c701（核心三件套 + panel_context）、91771f0（派生统计/监控/攻击语挂接）、本批（cron + logrotate + 冻结登记 + README）。
- **回滚**：git revert 本批 91771f0 063c701；/root/run_stats.py 回拷 /root/ops/kill2p-framework-20261101-040624/run_stats.py；
  crontab 删除 kill2p 行后 bash scripts/crontab_tool.sh install；/etc/logrotate.d/penta5 删除 kill2p 行（回拷备份）；
  data/kill2p_tail/ 历史封存/结算保留（回滚后不再写入）；面板下一轮重建自动移除 KILL_TWO_P 块。
- **成本提示**：每期 1 次 DeepSeek 请求（288 次/日）；04:05 余额 31.22 元，余额守护会在低余额时自动停 AI 总闸。

## 2026-11-01 04:41 kill2p 升 v2：扩 AI 输入（冷码统计 + 其它 AI 框架实值），选码与口径不变

用户 04:41 指令：对 kill2p（AI 二位置杀码 + 一合尾 + 两反向形态）「扩 AI 输入（把面板全量上下文喂进 prompt）」。
核查发现 kill2p v1 已内建面板上下文（frameworks / panel_hitrate_10p / panel_cumulative / model_tiers / kill2p_self），
故本次为在该上下文上再扩两段；用户确认**只扩输入、不改选码/计入口径**。

- **新增输入（commit 7f7b45e）**：
  - `cold_stats`：冷码观察近 30 期各位置冷码（top1 数字/次数/遗漏）+ 五种合尾冷码 + 单双/大小形态冷码；
    位置冷码近 30/20 期 Top-N（window/rank/line）；冷码杀号战绩 windows（Top1/2/3 的 rate/baseline/连中连败）。
  - `other_ai_self`：kill3（v9）与 kill1（v1）各自最近已发布封存（ref/target/rule/positions/tail/形态）+ 近 10 期
    已核验整组成绩（n/hits/rate/latest_target）。
  - 实现：`lib/panel_context.py` 新增可选 `extra_panel_input=False`（默认 False，kill3/kill1 行为逐字节不变），
    富上下文独立上限 `MAX_JSON_CHARS_RICH=12000`（普通路径仍 9000）；`lib/kill2p_tail.py` RULE_VERSION →
    `kill2p-tail-v2-panel-context-rich`；提示词版本与上下文段标题同步；`scripts/run_kill2p_tail.py` 传
    `extra_panel_input=True` 并打印 sha/bytes；hitrate_baseline / framework_hitrate_cumulative 登记 v2 基线 (5, 0.6203)。
- **A/B 证明（旧调用逐字节不变）**：固定 fixture 下 kill3 `72cc24ca…`、kill1 `feb6b4c8…`、kill2p v1 模式
  `61f31de0…` 与改前完全相同；rich 模式 payload 9430B ≤ 12000（离线）/ 实测 9672B，prompt 11296B。
- **首期真实封存（v2 生效）**：ref **20261101057** → target 20261101058，published **2026-11-01T04:45:53+08:00**，
  rule_version `kill2p-tail-v2-panel-context-rich`，`panel_context_sha256=63def709664bd6d1…`，
  panel_context 键含 `cold_stats`/`other_ai_self`（cron 日志：kill2p panel context sha=63def709664bd6d1 bytes=9672；
  prompt_tokens 4983 / completion_tokens 1655 / 10.36s / deepseek-flash）。旧 6 条 v1 封存 records sha256 未变
  （`0c7f28ae…`）。面板 panel.html 已出现 `kill2p-tail-v2-panel-context-rich`（口径追溯行）。
- **衍生统计**：hitrate/cumulative 已登记 v2 行；v2 首期结算（target 20261101058 开奖后）起自动出现 kill2p 行。
- **连续两期生效与首期结算（04:52 复验）**：第二期 ref **20261101058** → target 20261101059，published 04:50:5x，
  rule v2、panel_context_sha256=c7ebc9625bcf88cb…（同样含 cold_stats/other_ai_self）；v2 首期 target 20261101058 结算
  group_hit=True（verified，n=1）；hitrate/cumulative 现网已出现 kill2p v2 行（n=1/hits=1/rate=1.0，expected 0.6203）。
- **冻结守卫**：lib/panel_context.py 变更按 kill3 冻结门禁更新 hash（commit **dfdb5a7**，hash_updates 记录 A/B 证据）。
- **验证脚本**：/tmp/test_kill2p_v2.py（A/B、rich 内容、模块版本、封存字段、旧封存不变、无面板兜底）全部 PASS；
  黄金基线 /root/ops/kill2p-v2-044339/golden-v1.json、golden-v2.json。
- **回滚**：`git revert 7f7b45e dfdb5a7 <本提交>`；回拷 /root/ops/kill2p-v2-044339/ 下 5 个 .py 与 kill3-freeze.json；
  下一期起按 v1 口径封存，v2 已产生的封存保留（统计按 rule_version 分组，互不混算）。


## 2026-11-01 04:33 主面板隐藏「整组命中率 vs 随机期望」大表 + 独立统计页 /hitrate（598000f）

用户 04:33 指令「把统一给我隐藏了。要另外设置把这些拉到一起」，截图对象是主面板
「整组命中率 vs 随机期望（累计·版本锁定）」区块；ask_user_question 钉死口径为「这一整块累计表」：
**把整块从主面板隐藏**，另做独立统计页把所有框架命中率集中展示。

- **独立页**：新增 `lib/hitrate_page_view.py`，直接复用 `hitrate_baseline_view.render_block()`
  （累计表 + 滚动漂移监控 + 口径脚注，内容与原面板区块逐字一致），原子写
  `/root/penta5-runner/hitrate.html`；页面带「← 返回面板」「逐日明细看板」导航与数据截至/生成时间。
- **主面板只留入口**：`lib/hitrate_baseline_view.py` 新增 `render_entry()` / `apply_hitrate_entry_block()`，
  面板内只剩一行 `🗂 整组命中率 vs 随机期望（累计·版本锁定）已移到独立统计页 → 打开命中率统计页`
  （section id=`hitrate-base-entry`、`data-hitrate-entry="1"`）；保留 `HITRATE_BASE_START/END` 标记与
  `<!-- HITRATE_BASE_END -->` 锚点，`lib/tail_stability_shadow.py` 的插入逻辑与 `panel_markers` 校验不受影响。
- **生成链**：`scripts/framework_hitrate_cumulative.py` 在写 `data/framework_hitrate_cumulative.json` 后调用
  `hitrate_page_view.write_page()`；页面异常只打印 `hitrate page write deferred`，不影响 JSON 产物。
  cron `7,22,37,52 * * * *`（每 15 分钟）自动重写页面；页面与 JSON 同时刷新。
- **HTTP 路由**：`penta5_backend_runner.js` 新增 `isHitratePath()` / `sendHitrateHtml()`，路由 `/hitrate` 与
  `/hitrate.html`；未登录跳登录页并回跳 `/hitrate`（`safeLoginNextPath`），viewer 角色允许访问
  （`isViewerAllowedRequest`），404 endpoints 列表补 `GET /hitrate`。
- **repo 外改动**：`/root/run_stats.py` 两处 `update_panel` 链由 `apply_hitrate_baseline_block` 改为
  `apply_hitrate_entry_block`（import 同步改）；备份 `/root/ops/hitrate-page-20261101-044355/`
  （panel.html、penta5_backend_runner.js、hitrate_baseline_view.py、framework_hitrate_cumulative.py、run_stats.py + BACKUP.sha256）。
- **验证（真周期）**：面板重建后 `hitrate-base-entry=1`、`滚动漂移监控=0`、`HITRATE_BASE_START/END=1/1`、
  `TAIL_STABILITY_SHADOW` 标记仍在；`py_compile` 全 PASS、`node --check penta5_backend_runner.js` PASS；
  `/hitrate` 未登录 200（返回登录页）、admin 与 viewer 均 200；curl 服务端返回与磁盘 `hitrate.html`
  sha256 完全相同；cron 04:52 日志含 `framework_hitrate_cumulative written ... as_of=20261101058` +
  `hitrate page written: /root/penta5-runner/hitrate.html`，页面「累计报告生成于」与 JSON `generated_at` 同秒；
  `pm2 restart penta5-runner`（restart 284→285）后 `/health`、`/panel`、`/accuracy` 均 200；
  `hitrate.html` 提交后已 `git update-index --skip-worktree`（运行时每 15 分钟覆盖不再污染 git status）。
- **提交**：`598000f`（lib/hitrate_page_view.py 新增、hitrate_baseline_view.py、framework_hitrate_cumulative.py、
  penta5_backend_runner.js、hitrate.html 种子），本段台账单独一笔 docs 提交。
- **回滚**：`git revert 598000f` + 回拷 `/root/ops/hitrate-page-20261101-044355/run_stats.py` 到 `/root/run_stats.py`
  + `pm2 restart penta5-runner`；回滚后 run_stats 恢复旧链，面板下一轮自动重建回大表。
  如需彻底移除独立页：再删 `hitrate.html` 并 `git update-index --no-skip-worktree hitrate.html`（路由可保留为 404 兜底前先改回）。

## 2026-11-01 05:37 模型档位余额守卫（充值后自动全量归位 Pro）

用户 05:35：「我缴费了 pro模型要自动全量归位」。核查 provider 余额仍为 15.84 CNY（充值尚未到账），
先把「到账后自动归位」做成机制：

- **scripts/model_tier_guard.py**（每 10 分钟 cron，flock /var/lock/penta5-modeltier.lock）：
  - balance ≥ `pro_on_balance`(50) → 把 `PENTA5_MODEL_KILL1/KILL3/KILL2P` 全量切 `deepseek-v4-pro`（缺键自动补齐）
  - balance < `flash_below_balance`(15) → 全量切 `deepseek-flash`
  - 阈值之间保持现状（不回摆）；状态/日志 `data/model_tier_guard.json` / `data/model_tier_guard.log`
  - 写前备份 .env 到 `/root/ops/model-tier-guard/<ts>/.env.bak`，原子替换 0600；阈值/目标可在
    `data/model_tier_guard.json` 调整（targets 可扩到 KILLER/FRAMEWORK/HE3）
- 部署时实测：余额 15.84 → 首次运行 `hold-mid`、未改 .env（sha 31678823… 不变）；dry-run 验证
  50→switch-pro、5→switch-flash 逻辑正确；cron/logrotate 已装（crontab_tool diff 一致）。
- 风险提示：三框架全 Pro 实测约 ¥27–36/小时（且 Pro 偶发格式校验失败重试、耗时 33–74s 接近发布窗口），
  余额 ≥50 只能跑约 1.5–2 小时；若希望跑一天需充值到数百元量级，或把 targets 缩到单个框架。
- 回滚：git revert 本提交 + 回拷 `/root/ops/model-tier-guard/crontab.txt.bak` 后 `scripts/crontab_tool.sh install`；
  logrotate 回拷 `logrotate-penta5.bak`；`.env` 从 `/root/ops/model-tier-guard/<ts>/.env.bak` 或
  `/root/ops/model-pro-050545/.env.before-pro` 恢复。


## 2026-11-01 面板新增两区块：三数共现避开3组核验 + AI 模型调用推荐（6704846）

**需求**：①把 /accuracy「今日过滤与参考项」里的「三数共现避开3组」单项调到主面板显示；
②新增「AI 模型调用推荐」区块（按任务建议调用 flash / pro，只读不切换）。

- **三数共现块**：`lib/three_avoid2_stats.py` 从 `data/state.json` 已校验预测按后端
  `filterDailyDisplayPredictions` 同口径结算（evaluated/truthEligible/immediate 过渡/
  开奖分钟一致/先于目标生成/前4球合法/按 targetPeriod 去重），命中口径＝该组 3 个数字
  未在 4 球开奖号中全部出现（避开成功），基线 97.96%（10^4 精确枚举，与 /accuracy
  `EXACT_BASELINES.threeDigitAvoid2` 同值）；写 `data/three_avoid2_daily.json`
  （本业务日 + 上一业务日）+ `data/three_avoid2_history.json`（按日单调 max(total)，
  预测滚动窗口 400 期滚掉后仍保留已封盘日）。CLI：`scripts/three_avoid2_daily.py --now … --dry`。
  回放核验：`--now '2026-11-01 04:52:00'` 得 284/287=98.95%，与用户截图一致；
  实时 `/state` `counters.threeDigitAvoid2` 与派生 JSON 逐字段 MATCH。
- **模型调用推荐块**：`lib/model_call_rec.py` 读 `framework_hitrate_cumulative.json`
  的 `model_split`（按封存 requested_model 分档）+ `model_usage/summary.json`（余额/燃耗/
  延迟/错误）+ `ai_balance_guard.json` + `.env`（按各任务真实回退链解析现行档），规则写死：
  「两档累计样本均≥200 且替代档高≥2pp、双侧 z 检验 p<0.05 才建议换档，否则维持现档/观察」；
  写 `data/model_call_rec.json`；**只读，不改 .env、不重启任务**。
- **视图/挂载**：`lib/three_avoid2_view.py`（THREE_AVOID2_START/END）、
  `lib/model_call_rec_view.py`（MODEL_CALL_REC_START/END）自包含 section + 内联 style，
  异常原样返回；`lib/panel_live_refresh.py` 的 names 列表加入两个标记纳入 8s 实时刷新；
  repo 外 `/root/run_stats.py` 两处 `update_panel` 收尾链最外层挂
  `apply_model_call_rec_block(apply_three_avoid2_block(...))`（import 同步加），
  备份 `/root/ops/panel-two-blocks-20261101/`。
- **验证（真周期）**：面板重建后两区块各 1、既有块标记全部 1/1、
  `data-as-of` 066→067 跨轮推进；`py_compile` 全 PASS。
- **回滚**：`git revert 6704846` + 回拷 `/root/ops/panel-two-blocks-20261101/run_stats.py.bak-before-avoid2-modelrec`
  到 `/root/run_stats.py`（面板下一轮自动重建即移除两区块）；数据文件
  `data/three_avoid2_*.json` / `data/model_call_rec.json` 未跟踪，可留可删。
- **注意**：`.env` 由 `scripts/model_tier_guard.py`（cron 每 10 分钟）按余额自动全量切
  flash/pro 并可能增删 PENTA5_MODEL_* 行；推荐块按各任务回退链解析，missing 时回退
  KILLER→DEEPSEEK_MODEL，与各 task 脚本的真实回退一致。

## 2026-11-01 三数共现块升级三组合并（commit 待补号，见上）

- 面板「三数共现」块 v2：三组号码（避开1/2/3组）同屏显示（下一期待核验 + 最新一期逐组 ✓/✗）；
  核验表改为「三组合并（1+2+3 全避开）」口径：同一期三组全部避开成功才记命中，
  合并基线 = 每期按该期三组号码 10^4 精确枚举（1 - P(至少一组共现)）按期平均。
  实测：上一业务日合并 273/288=94.79% vs 基线 94.06%（+0.73pp 贴合随机）。
  历史文件 data/three_avoid2_history.json 升级为 merged + single 双口径并存。

## 2026-11-01 三数共现参考加入 AI 模型 panel_context（3bc709a）

- `lib/panel_context.py` 新增 `_three_avoid2()`：三组号码（避开1/2/3组当前值 + 最新一期逐组
  成功/失败与合并结论）+ 本/上一业务日合并核验率与 10^4 精确枚举基线，数据源
  `data/three_avoid2_daily.json`（面板核验块同源，只读），缺失时值为 null。
- 生效：kill1/kill2p/kill3 提示词（封存 `panel_context`/`panel_context_sha256`）、six/he3
  输入文档、killer/framework 归档侧快照。killer/framework 的**模型提示词**是独立策略
  （`cross_model_advice=disabled`、不混用其它模型口径），按设计不注入三数共现参考。
- 冻结守卫：`ops/kill3-freeze.json` 更新 `lib/panel_context.py` sha256 并追加 hash_updates
  记录；A/B：同 fixture 除新增键外逐字节相同、数据缺失=null、kill3 RULE_VERSION 未变。
- 验证：ref 20261101075 的 kill3/kill1/kill2p/six/he3 封存均含 three_avoid2 段；
  ops_health kill3_freeze=OK、manifest_drift 已按 --if-drift 重建基线（overall=WARN 仅余
  coverage/model_usage/data_tmp 业务告警）。

## 2026-11-01 位置杀码 holdK-until-miss 多档只读影子对照（1af215a）

- 目标：位置杀码（kill1 单位置 / kill2p 二位置 / kill3 三位置 2 计入）的「换码」影子对照，
  候选 holdK-until-miss（命中沿用、最多连用 K 期、失手或满 K 期换码），多档 hold2/3/5/不设上限。
- 离线回放（未改现网）：kill1 现行 93.10%→hold3 93.10%（换码 19→8）；kill2p 96.88%（12→6）；
  kill3 88.66%→hold3 89.35%（454→184）、直至失手 90.18%（65）。稳定化不提高命中率（数学上限），
  只降换码频率；kill3 现行略低于 90% 基线受 max_consecutive=3 强制换码拖累。
- 新增 lib/position_kill_shadow.py（只读影子，回放/前瞻双段，多档）、
  scripts/run_position_kill_shadow.py（cron 入口）；只写 data/position_kill_shadow.json 与
  panel.html POSITION_KILL_SHADOW 区块，不改任何封存/统计/攻击语。
- lib/panel_live_refresh.py names 白名单 +POSITION_KILL_SHADOW、版本 v20→v21；
  ops/crontab.txt 新增影子 cron（每 5 分钟 sleep 55、flock /var/lock/penta5-poskill-shadow.lock，
  36→37 jobs）。
- 验证：手工首跑 forward=0（正常）；cron 自动跑 generated_at 06:58→07:00:56、forward 0→1；
  面板区块 START/END 各 1、数据行 3 框架；panel.html 白名单已刷入 POSITION_KILL_SHADOW + v21。
- 复核：预登记判据（前瞻每框架 forward 注数 n≥50、命中率 95%CI 内不显著变差、换码明显下降）
  由 schedule 2026-11-01 21:05 触发定点复核，达标才提转正方案，现网一律不动。
- 备份：/root/ops/position-kill-shadow-20261101-065738/（panel_live_refresh.py、crontab.txt、新文件）。
- 回滚：git revert 1af215a + scripts/crontab_tool.sh install（恢复 crontab.txt 基线 36 行）；
  数据文件 data/position_kill_shadow.json 未跟踪可留可删。

## 2026-11-01 重新上线 kill2_tail（AI 二位置杀码 + 一合尾 + 二数合/三数合，7 项独立框架）

- 背景：kill2_tail 曾于 2f7a8b0 上线、0f2df82 退役；本次按用户已确认口径重建（沿用 v1 名
  `kill2-tail-v1-two-positions-he2he3`），并更正整组精确枚举基线。
- 口径（用户已确认，Lead 转述，确认通道=用户已选/文字口径）：
  - 命名 kill2 → `lib/kill2_tail.py`、`lib/kill_two_view.py`、面板 `section id=kill-two-ai`、
    `apply_kill_two_block`、`RULE_VERSION=kill2-tail-v1-two-positions-he2he3`、`data/kill2_tail/`、
    cron 锁 `/var/lock/penta5-kill2t.lock`、日志 `data/kill2_tail_cron.log`。
  - 整组 7 项 = **2 位置杀码（全计入，无 display_only）** + 1 合尾 + 二数合 + 三数合 + 2 反向形态。
  - 形态：`reverse_parity`（单双）+ `reverse_size`（大小）；被杀位取杀码形态反面、其余位自选（与 kill3 同）。
  - 尾码：`hold3-until-miss`（与 kill3 同）。连用限制：位置杀码最多连用 3 期、中间未选该位置不清零（`MAX_CONSECUTIVE_PERIODS=3`）。
  - 二数合 he2_keep：从 02468 留 4 个不同数字，任取两位 6 组和值个位任一落入保留集即命中。
  - 三数合 he3_keep：从 0123456789 留 5 个不同数字（2奇3偶或3奇2偶），任取三位 4 组和值个位任一落入保留集即命中。
- 精确枚举整组随机期望 = **0.5708（7 项，10^4 开奖空间均匀配置精确枚举，非相乘）**。单项相乘约 0.5864 偏高。
  交叉验证（全复现到 6 位小数）：kill2p 5 项 0.620303（现网 0.6203 ✓）、he2 留4码 0.9808 ✓、he3 留5码 0.9375 ✓。
  **更正**：旧 kill2（2f7a8b0）曾登记 0.5726（抄自 selected_six v7 的 minimax 优化形态），kill2 实为
  「被杀位取反、其余自选」口径，应为 0.5708。
- 三件套：lib/kill2_tail.py（1206 行，grep -c kill3=0）、lib/kill_two_view.py（549 行）、
  scripts/run_kill2_tail.py（163 行）；kill_two_view.py 的 FALLBACK_BASELINE 已改 (7,0.5708)、57.08%。
- 12 处挂接点：run_stats（import 1 + 调用 2，/root 仓库外）、panel_context（kill2_self 7）、
  hitrate_baseline（7）、framework_hitrate_cumulative（10）、ops_health_check（16）、model_usage（2）、
  evidence_backup（1）、attack_editor_view（1）、panel_live_refresh（1）、tail_hold_review（5）、
  crontab（1）、logrotate（1，/etc 仓库外）。
- 模块测试：/tmp/kill2_module_test.py → RESULT {"pass":28,"fail":0}（reverse/normalize/he2-he3 校验/
  settle group_hit True+False/attack/位置连用/尾码 hold3 全过）。
- 共享模块 A/B（panel_context.py）：old=ops/kill2-20261101-082622/panel_context.py.bak vs new，固定 fixture
  下 build() 输出逐字节相同——kill3 7637ced1…、kill1 01fc5228…、kill2p 889e8fa2…、noargs 0973b419…；
  仅带 kill2 入参时新增 kill2_self，kill3/kill1/kill2p 口径未变。
- 冻结守卫：ops/kill3-freeze.json 更新 lib/panel_context.py sha256
  00982a02…→3e0df93e…，hash_updates 追加一条（commit 记为 pending-kill2）。
- crontab：ops/crontab.txt 在 kill2p 行后加同构行（sleep 45、flock /var/lock/penta5-kill2t.lock），
  scripts/crontab_tool.sh install 后 diff 无差异；logrotate -d 已识别 data/kill2_tail_cron.log。
- 备份：/root/ops/kill2-20261101-082622/（panel_context.py.bak、run_stats.py.bak、logrotate.penta5.bak）。
- 上线验证（真实期号封存/结算）由 Lead 下一轮统筹核验；本批做到「部署就绪 + crontab 已装 + 离线渲染先验 +
  模块测试 fail:0 + A/B sha256 等值 + 12 挂接点 grep 全≥1 + 三次 commit」。

## 2026-11-01 位置杀码 kill4 独立框架上线（ba991a2 / a8e49d3）

- 命名与四项口径（ask_user_question 通道确认，2026-11-01）：kill4 = 3 位置杀码 + 1 合尾 + 二数合 + 三数合 + 单双 + 大小 2 反向形态 = 8 项；尾码 hold3-until-miss；位置不设连用限制（无 position_restriction）。
- RULE_VERSION=kill4-tail-v1-three-positions-he2he3；独立封存 data/kill4_tail/。
- 整组精确枚举（10^4 开奖空间 × 均匀配置，10^8 蒙特卡洛固定种子，非相乘）：0.5038（kill2 7 项对照 0.5708）。
- 三件套：lib/kill4_tail.py、lib/kill_four_view.py、scripts/run_kill4_tail.py；模块测试 20 PASS / 0 FAIL。
- 12 挂接点：/root/run_stats.py（仓库外）、panel_context（新增 kill4_self）、hitrate_baseline、framework_hitrate_cumulative、ops_health_check、model_usage、evidence_backup、attack_editor_view、panel_live_refresh、tail_hold_review、ops/crontab.txt、/etc/logrotate.d/penta5。
- A/B 证明：panel_context.py 改动后 kill3/kill2 固定 fixture 输出 sha256 逐字节不变（kill3=13b62e58c526961ffc45e58b68c8b03e0be058e3f64b5bd0b5ce4ddcd9370e2d、kill2=93b5be2e0fc94a62bd3ac1d1cd02454bae6c486b7d96ed6677d8461494b61e09）。
- 冻结守卫：ops/kill3-freeze.json 的 panel_context hash 3e0df93e... → f184f60f...（hash_updates 追加第 12 条）。
- cron：每 5 分钟 sleep 50、flock /var/lock/penta5-kill4t.lock；日志 data/kill4_tail_cron.log。
- 回滚：git revert a8e49d3 ba991a2 + scripts/crontab_tool.sh install（恢复 crontab.txt 基线）。

## 2026-11-01 位置杀码影子对照中间复核（09:30，n≥30 中间观察档）

- 判据从 n≥50 放宽为中间结论档 n≥30（复核脚本阈值同步 50→30）。
- 样本足够（n≥30）：kill3（24 期 / 48 注）、kill2p（24 期 / 48 注）。
- 样本不足（继续观察）：kill4（3 期 / 9 注）、kill2（9 期 / 18 注）、kill1（22 期 / 22 注）。
- kill3：hold2/hold3 命中率 95.83% vs 现行 93.75%（+2.08pp，95%CI 重叠、不显著），换码降 7-9 次；
  hold5/until-miss 91.67%（-2.08pp）但换码降 14-16 次。方向正面。
- kill2p：hold2/hold5/until-miss 命中率同现行 89.58%，换码降 2-7 次；hold3 87.50%（-2.08pp）。方向正面。
- 结论：n≥30 置信区间仍宽（约 ±8pp），暂不转正；最终口径等 n≥50（约 12:00）再定。现网封存/统计/攻击语未动。

## 2026-11-01 kill3 v10：关掉两套「3 期」限制，全部交给模型决定（用户指令）

- 用户原话：「AI 三位置杀码 + 一合尾…把这个模型 3 轮换码关了，全部交给模型决定」；经确认范围 = 两套都关。
- RULE_VERSION：kill3-tail-v9-two-counted-hold3 → kill3-tail-v10-two-counted-free；prompt 版 v9 → v10。
  - 尾码：删除【合尾稳定化规则】与 apply_tail_policy 沿用，模型尾码每期直接封存（新封存不再写 tail_policy）。
  - 位置/弃位：删除【位置与弃位使用限制】与 validate_position_choice 强制换码，位置杀码/放弃位置可连续使用。
  - 面板脚注、口径归档标注（旧口径 v1–v8 → v1–v9）、hitrate_baseline 与 framework_hitrate_cumulative 各加 v10 基线 (5, 0.6191)。
- 命中口径不变：整组仍 = 2 计入位置 + 1 合尾 + 2 反向形态共 5 项 / 2 注·期；精确基线 61.91% 不变。
- 旧 v1–v9 封存只读保留、不重算、不补写；新封存 rule_version=v10，无 tail_policy / position_restriction。
- 备份：/root/ops/kill3-v10-free-100349/（5 个源文件 + seals/settlements 快照）。

## 2026-11-01 kill5 独立框架上线（e8f3708 / 2b050e9 / 本提交）

- 命名与四项口径（ask_user_question 通道确认，2026-11-01 10:15）：kill5 = 3 位置杀码 + 1 合尾 + 二数合 + 三数合 + 单双 + 大小 2 反向形态 = 8 项；尾码模型自主（无 hold3-until-miss）；位置不设连用限制（无 position_restriction）。
- RULE_VERSION=kill5-tail-v1-three-positions-he2he3；独立封存 data/kill5_tail/；克隆自 kill4 三件套，仅去尾码 hold3。
- 整组期望 0.5038：与 kill4 同构（8项、整组命中定义一致），10^4 开奖空间 × 均匀配置、10^8 蒙特卡洛固定种子，非单项相乘；尾码策略（hold3 vs 自由）不改变随机期望。
- 三件套：lib/kill5_tail.py、lib/kill_five_view.py、scripts/run_kill5_tail.py；模块测试 30 PASS / 0 FAIL。
- 12 挂接点：/root/run_stats.py（仓库外）、panel_context（新增 kill5_self）、hitrate_baseline、framework_hitrate_cumulative、ops_health_check、model_usage、evidence_backup、attack_editor_view、panel_live_refresh、tail_hold_review、ops/crontab.txt、/etc/logrotate.d/penta5。
- A/B 证明：panel_context.py 改动前 .bak sha256=f184f60f9d4b2bdf22b4e2c0e9370eedf6ba4444f3c35e630097f8ae4cfed46c、改动后 df3305ab78e5d72d7228cb05459b7978925f3301b695d2a0711a0a4d114e6359；固定 fixture 下 kill3+k4 上下文 old/new sha256 逐字节相同 12c5667a1fad476232c29f6fcf98a73e67ee0ec2742bc48fc6dca76272b0d8a6（kill3/kill4 口径未变，kill5_self 只增不改）。
- 冻结守卫：ops/kill3-freeze.json 的 panel_context.py hash f184f60f → df3305ab（hash_updates 第 16 条）。
- cron：每 5 分钟 sleep 50、flock /var/lock/penta5-kill5t.lock；日志 data/kill5_tail_cron.log。
- 日志名五处一致：model_usage / ops_health_check / ops/crontab.txt / logrotate / README 全用 data/kill5_tail_cron.log；旧名 kill5_cron.log 残留 0。


## 2026-11-01 位置杀码 kill6 独立框架上线（同 kill4 口径）

- 命名与四项口径（ask_user_question 通道确认，2026-11-01）：kill6 = 3 位置杀码 + 1 合尾 + 二数合 + 三数合 + 单双 + 大小 2 反向形态 = 8 项；尾码 hold3-until-miss；位置不设连用限制（与 kill4 完全同口径）。
- RULE_VERSION=kill6-tail-v1-three-positions-he2he3；独立封存 data/kill6_tail/。
- 整组期望（与 kill4 同口径）：0.5038（固定种子 20261101、10^8 样本蒙特卡洛，非相乘；复算 10^7=0.503552 与 kill4 一致）。
- 三件套：lib/kill6_tail.py、lib/kill_six_view.py、scripts/run_kill6_tail.py；模块测试 20 PASS / 0 FAIL。
- 12 挂接点：/root/run_stats.py（仓库外）、panel_context（新增 kill6_self）、hitrate_baseline、framework_hitrate_cumulative、ops_health_check、model_usage、evidence_backup、attack_editor_view、panel_live_refresh、tail_hold_review、ops/crontab.txt、/etc/logrotate.d/penta5；日志名 kill6_tail_cron.log 五处一致、旧名残留 0。
- A/B 证明：panel_context.py 改动后 kill3/kill2/kill4 固定 fixture 输出 sha256 逐字节不变（kill3=3a78269daf06f6a886270f763a06e479ac7a342866d6ef6584a2a4dc765e1f81、kill2=e9fe5d95c5e15a19abd54d72e4b5a58f37a86872d21efd85f8923536aa26faed、kill4=1b772a30d629814ce4619cfd7d920544c5f03c198f1e77b7c809c2b55e5ff4ed）。
- 冻结守卫：ops/kill3-freeze.json panel_context hash df3305ab… → 2f9d6011…（hash_updates 第 17 条）。
- cron：每 5 分钟 sleep 55、flock /var/lock/penta5-kill6t.lock；日志 data/kill6_tail_cron.log。
- 回滚：git revert（三次 commit）+ scripts/crontab_tool.sh install。

## 2026-11-01 位置杀码 kill7 独立框架上线（同 kill5 口径）

- 命名与四项口径（ask_user_question 通道确认，2026-11-01）：kill7 = 3 位置杀码 + 1 合尾 + 二数合 + 三数合 + 单双 + 大小 2 反向形态 = 8 项；尾码模型自主（无 hold3）；位置不设连用限制（与 kill5 完全同口径）。
- RULE_VERSION=kill7-tail-v1-three-positions-he2he3；独立封存 data/kill7_tail/。
- 整组期望（与 kill5 同口径）：0.5038（固定种子 20261101、10^8 样本蒙特卡洛，非相乘）。
- 三件套：lib/kill7_tail.py、lib/kill_seven_view.py、scripts/run_kill7_tail.py；模块测试 20 PASS / 0 FAIL。
- 12 挂接点：/root/run_stats.py（仓库外）、panel_context（新增 kill7_self）、hitrate_baseline、framework_hitrate_cumulative、ops_health_check、model_usage、evidence_backup、attack_editor_view、panel_live_refresh、tail_hold_review、ops/crontab.txt、/etc/logrotate.d/penta5；日志名 kill7_tail_cron.log 五处一致。
- A/B 证明：panel_context.py 改动后 kill3/kill2/kill4/kill6 固定 fixture 输出 sha256 逐字节不变（kill3=9224d43d401c88e5f9ce5b4d4b134eca072f24e1807cf78d54de0a1f78bfc6e5、kill2=f06ff1978c32e768f05ba534c9cfb6d0992f85ad28480f596ea6f0a63be5b6c4、kill4=fb588a10e26fb6219038817c6eaed966a2c8ff1458071fb875a2fa24be2e335a、kill6=2457369afa459f4895294a77f3c76a42945440fc4fd8069358e5fb37c8c12549）。
- 冻结守卫：ops/kill3-freeze.json panel_context hash 2f9d6011… → bc6062c3…（hash_updates 第 18 条）。
- cron：每 5 分钟 sleep 60、flock /var/lock/penta5-kill7t.lock；日志 data/kill7_tail_cron.log。
- 回滚：git revert（三次 commit）+ scripts/crontab_tool.sh install。

## 2026-11-01 退役重复框架 kill6 / kill7

- 原因：kill6 与 kill4 完全同口径（8 项 + hold3 + 不限制）、kill7 与 kill5 完全同口径（8 项 + 尾码模型自主 + 不限制），是重复副本，用户确认清理。
- 清理范围：kill6/kill7 框架本体（三件套）+ 12 处派生统计挂接点 + cron/logrotate + data 目录。
- 顺带修复：kill6/kill7 挂接时 clone 脚本 next_marker 错位，导致 kill5 相关函数在 hitrate_baseline/cumulative/ops_health/tail_review 里重复定义（各 2 个 def）；退役时一并去重（保留第一个）。
- A/B 证明：panel_context 删除 kill6_self/kill7_self 后，剩余框架 kill3/kill2/kill4/kill5 输出逐字节不变（kill3=188a759b…、kill2=148208bc…、kill4=dc5a3170…、kill5=bb7ba74c…）。
- 冻结守卫：panel_context hash bc6062c3… → 4d174e9f…（hash_updates 第 19 条）。
- 数据归档：/root/archive/retired-20261101-kill67/kill67-data.tar.gz（sha256=1d98b2f0bb8dcd7eeb69abe47255e5d4815a582079c05263e3bd9993b50ed75b）。
- 备份：/root/ops/kill67-retire-20261101-105329/（panel_context.py.bak、crontab.txt.bak、run_stats.py.bak）。
- 回滚：git revert（三次 commit）+ scripts/crontab_tool.sh install + tar xzf 解档数据。
- 补清（2026-11-01 11:31）：退役提交漏了三处残留，已一并清理——(1) panel.html 仍留 KILL_SIX/KILL_SEVEN 幽灵卡片区块（kill6/kill7 写卡任务已删、无人再覆盖；按 START/END 标记整体移除 12928 字节，复核残留 0，其余 KILL_ONE..KILL_FIVE 标记不变）；(2) scripts/model_tier_guard.py targets 仍列 KILL6（改为 KILL1/KILL2/KILL2P/KILL3/KILL4/KILL5）；(3) .env 残留 PENTA5_MODEL_KILL6（已删，备份 .env.before-kill6-removal）。另清 6 个 kill6/kill7 残留 .pyc，manifest 经 refresh_source_manifest.py --if-drift 重建（435 files）。

## 2026-11-01 退役重复框架 kill4（hold3 副本，保留 kill5 尾码自主）

- 原因：kill4 与 kill5 完全同口径（8 项：3 位置 + 1 合尾 + 二数合 + 三数合 + 单双 + 大小，位置不设连用限制），仅尾码策略不同（kill4 hold3-until-miss / kill5 尾码模型自主）；用户确认只留一套，选保留 kill5。
- 清理范围：kill4 三件套 + 12 处派生统计挂接点 + cron/logrotate + data 目录 + 位置杀码影子 kill4 条目 + model_tier_guard targets + .env PENTA5_MODEL_KILL4 + panel.html KILL_FOUR 幽灵卡片。
- 顺带修复 2 个遗留 bug：(1) scripts/tail_hold_review.py 的 FRAMEWORKS 元组在 kill6/kill7 退役（9914711）时被整体误删，导致 build() NameError，本次重建 8 条目（kill3/kill1/kill2p/kill2/kill5/framework/six/killer）；(2) lib/attack_editor_view.py 的 JS definitions 数组缺逗号（node --check 报 Unexpected token），本次补逗号。
- A/B 证明：panel_context 删 _kill4_self + kill4_stats/kill4_previous 参数后，剩余框架 kill3/kill1/kill2p/kill2/kill5 输出逐字节不变（reference=20261101140 target=20261101141，snapshot 9837B 逐字节相等）。
- 冻结守卫：panel_context hash 4d174e9f… → eb3446c5…（hash_updates 第 20 条）。
- 数据归档：/root/archive/retired-20261101-kill4/kill4-data.tar.gz（sha256=9d20d039b11b1be1c7824a3ced16ec92f2d331a42068441ad89db0666b6061bf）。
- 备份：/root/ops/kill4-family-20261101-113740/（14 个文件：crontab/panel_context/attack_editor/hitrate_baseline/evidence_backup/model_usage/panel_live_refresh/cumulative/ops_health/tail_hold_review/kill3-freeze/panel.html/run_stats/logrotate）。
- 回滚：git revert（三次 commit）+ scripts/crontab_tool.sh install + tar xzf 解档数据。
- 补清（2026-11-01 11:51，本窗口）：清 3 个 kill4 残留 .pyc（lib/__pycache__/kill4_tail、lib/__pycache__/kill_four_view、scripts/__pycache__/run_kill4_tail）与 data/.kill4_tail_job.lock 遗锁；手动重跑 scripts/framework_hitrate_cumulative.py 刷新 data/framework_hitrate_cumulative.json（rows 10→9）与 hitrate.html（两者 kill4 残留均 0）；manifest refresh_source_manifest.py --if-drift 返回 no-drift。

## 2026-11-01 12:00 位置杀码影子对照最终复核（n≥50 注档）

- 预登记最终口径：前瞻注数 n≥50（kill1 需 50 期、kill2/kill2p/kill3 各 2 注/期需 25 期）。复核脚本阈值由 n≥30（中间档）改为 n≥50（最终档）。
- 复核结果（只读 data/position_kill_shadow.json；命中率 Wilson 95%CI，换码↓=现行换码次数 − 策略换码次数）：
  - **kill3**：96 注（48 期），现行 90.62% [83.1, 95.0]；hold2 89.58%(换码 39→29)、hold3 89.58%(→21)、hold5 88.54%(→15)、until-miss 87.50%(→11) —— **四档全部达标**。
  - **kill2p**：96 注（48 期），现行 88.54% [80.6, 93.5]；hold2 87.50%(26→20)、hold3 87.50%(→16)、hold5 87.50%(→13)、until-miss 86.46%(→12) —— **四档全部达标**。
  - **kill2**：66 注（33 期），现行 81.82% [70.9, 89.3]；hold2 81.82%(33→22)、hold3 87.88%(→23，+6.06pp)、hold5 83.33%(→13)、until-miss 84.85%(→10) —— **四档全部达标**。
  - **kill1**：46 注（46 期）< 50，**未达 n≥50 门槛**；hold2/hold3 命中率 91.30% 持平、换码 8→5 —— 方向正面但样本不足，待补 4 期。
- 说明：kill4 已退役并移出影子 collector（2026-11-01 11:40），kill7 未纳入；kill1 的 forward 注数按 1 注/期计。kill3 forward 样本含 20261101141 开奖前的连续记录（当时 AI 因余额停用，缺口由影子 backfill 连续期号补齐）。
- 建议：达标三框架（kill3/kill2p/kill2）统一选 **hold3**（预登记示例档：命中率在 95%CI 内不显著变差、换码次数下降约 30–46%）；kill1 等 n≥50 后再补看。转正需用户确认后另行实施，影子对照本身不改现网封存/统计/攻击语。

## 2026-11-01 12:03 kill3 位置杀码尾码 hold3 转正（v10 free → v11 hold3）

- 依据：位置杀码影子对照 12:00 最终复核（n≥50 注档）kill3 96 注达标 —— hold3 命中率 89.58%（现行 90.62%，95%CI [81.9,94.2] 与现行 [83.1,95.0] 重叠、不显著变差），换码 39→21（↓46%）。
- 口径：`kill3-tail-v11-hold3-two-counted`（命中口径不变：2 个计入位置 + 1 合尾 + 2 反向形态 = 5 项 / 2 注；整组精确基线 0.6191）。
- 改动：
  - `lib/kill3_tail.py`：RULE_VERSION v10→v11；build_prompt 合尾限制 free → hold3（【合尾稳定化规则】；任务第 2 条改为「作为本期换码候选；系统按稳定化规则决定最终封存尾码」）；audit.tail_policy=hold3-until-miss、tail_hold_max=3；docstring 更新。
  - `scripts/run_kill3_tail.py`：request_prediction 后调用 `apply_tail_policy`（hold3 沿用），打印 tail_policy note。
  - `lib/hitrate_baseline.py` / `scripts/framework_hitrate_cumulative.py`：加 v11 基线映射 `(5, 0.6191)`。
  - `lib/kill_three_view.py`：旧口径文案「v1–v9」→「历史版本」。
  - `lib/position_kill_shadow.py`：影子 collector 退役 kill3 条目（保留 kill1/kill2p/kill2；kill1 46 注未达 n≥50，继续观察）。
- 模块测试：apply_tail_policy 合成用例（上期命中→沿用第 1 期；上期未命中→换码）通过；build_prompt 真实 ref=20261101144 生成含【合尾稳定化规则】/换码候选，audit.version=v11。
- 冻结守卫：`ops/kill3-freeze.json` rule_version v10→v11，kill3_tail.py / kill_three_view.py / run_kill3_tail.py 三处 hash 更新（hash_updates 第 21–23 条）。
- 回滚：`git revert`（三次 commit）。
- 说明：kill1/kill2p/kill2 早已是 hold3（本次未动）；kill3 是真正确需要从 free 转 hold3 的唯一框架。

## 2026-11-01 12:10 退役重复框架 kill2（二位置 7 项副本，保留 kill5）

- 原因：面板上「AI 二位置杀码 + 一合尾 + 二数合/三数合建议」（kill2，7 项）与「AI 三位置杀码 + 一合尾 + 二数合/三数合建议」（kill5，8 项）属同一「一合尾 + 二数合/三数合 + 两反向形态」家族，用户确认只留一套，选保留 kill5（三位置）；kill2p（两反向形态 5 项）不在本次范围。
- 清理范围：kill2 三件套（lib/kill2_tail.py / lib/kill_two_view.py / scripts/run_kill2_tail.py）+ cron/logrotate + panel.html KILL_TWO 区块 + panel_live_refresh/attack_editor/kill_five_view 锚点 + 派生统计（hitrate_baseline / framework_hitrate_cumulative / tail_hold_review / model_usage / ops_health_check / evidence_backup / position_kill_shadow / panel_context / position_kill_review）+ model_tier_guard targets + data/kill2_tail。
- 面板：/root/run_stats.py 去 kill_two_view import 与 2 处 apply_kill_two_block；panel.html 按 KILL_TWO_START/END 整体剥除（-3653 字节），KILL_TWO_P 区块原样保留。
- 派生统计：hitrate_baseline 9→8 行、framework_hitrate_cumulative 9→8 行（rows=8：killer/framework/selected_six/he3_refine/kill3_tail/kill1_tail/kill2p_tail/kill5_tail）、tail_hold_review 8→7 框架、model_usage 9→8 日志源、ops_health 去 check_kill2 项、evidence_backup JSON_DIRS 7→6、position_kill_shadow 去 kill2 条目。
- A/B 证明：panel_context 删 _kill2_self + kill2_stats/kill2_previous 后，剩余框架 kill3/kill1/kill2p/kill5 固定 fixture 输出逐字节不变（old-without-kill2_self == new，sha256 0fb59c9c21f4；new 中无 kill2_self）。
- 冻结守卫：panel_context hash eb3446c5… → 492e2b87…（hash_updates 第 24 条）。
- 数据归档：/root/archive/retired-20261101-kill2/kill2-data.tar.gz（sha256=480a4abe96d78f6cb89681862ec0477460d0d2371d435e8694afec612ab12e96，5 条目）。
- 备份：/root/ops/kill2-family-20261101-120227/（22 文件：crontab/panel.html/run_stats/panel_context/attack_editor/hitrate_baseline/evidence_backup/model_usage/panel_live_refresh/position_kill_shadow/ops_health/tail_hold_review/framework_hitrate_cumulative/position_kill_review/model_tier_guard/kill_five_view/kill_two_p_view/kill_two_view/kill2_tail/logrotate/hitrate.json/cumulative.json）。
- 回滚：git revert + scripts/crontab_tool.sh install + tar xzf 解档 + 恢复 /root/run_stats.py（备份在 /root/ops/kill2-family-20261101-120227/run_stats.py）。
- 并行窗口协同：kill2 三件套的 git rm 已被并行窗口 d94a0af（kill3 v11 转正）一并提交；本窗口补提交派生统计/面板/运行层，并已复核 kill3 v11 的 hash 更新未被覆盖。

## 2026-11-01 12:18 彻底退役 kill5（AI 三位置杀码 + 一合尾 + 二数合/三数合，8 项）

- 原因：用户面板截图指认 kill5 并要求「这个框架清理了」，经确认按彻底退役处理（停跑 + 删三件套 + 去全部挂接 + 面板区块剥除 + 数据归档），不留任何层；kill2 已于 12:10 退役，本框架为该「一合尾 + 二数合/三数合」家族最后一套。
- 运行层：crontab 删 `*/5 ... run_kill5_tail.py`（37 作业，`diff <(crontab -l) ops/crontab.txt` 干净）；/etc/logrotate.d/penta5 删 data/kill5_tail_cron.log 段（logrotate -d 无错）；.env 删 PENTA5_MODEL_KILL5；model_tier_guard.py DEFAULTS targets 与 data/model_tier_guard.json targets 均去 KILL5（现 KILL1/KILL2/KILL2P/KILL3）。
- 面板：/root/run_stats.py 去 kill5 import 与 2 处 apply_kill_five_block 调用；12:04 并行窗口 kill2 改动后面板链曾退化为 `apply_model_signi_block(apply_kill_five_block())`、早写必 TypeError，本次恢复完整 12 层链（model_call_rec→…→strip_retired_panels(page)）再去掉 kill5 层，run_stats 全阶段 success（278.5s，failed=0）；panel.html KILL_FIVE 区块（6588B）随 rebuild 清除（KILL_FIVE 计数 0，KILL_ONE/KILL_THREE/KILL_TWO_P/KILL_SHADOW 各 1）；lib/panel_live_refresh.py names 去 KILL_FIVE；lib/attack_editor_view.py 删 kill5 攻击语定义、版本 v13-retire-kill2→v14-retire-kill5；删除 lib/kill_five_view.py。
- 派生统计：hitrate_baseline 8→7 行（data/hitrate_baseline.json rows=7、kill5 0）；framework_hitrate_cumulative 8→7（rows=7、hitrate.html kill5 0）；tail_hold_review 7→6 框架；model_usage 8→7 日志源；ops_health 去 check_kill5 与 PROVIDER_402 日志项；evidence_backup JSON_DIRS 6→5；panel_context 删 _kill5_self + kill5_stats/kill5_previous 入参与 snapshot 键。
- A/B 证明：panel_context 固定 fixture（bare/kill3/all/rich）旧模块去掉 kill5_self 后与新版逐字节相同，sha256 5ad4cb6e568e3612 / 77c994150e7b7af1 / fa9f58b2b65c442c / 581892950f3894ac；新版 snapshot 无 kill5_self。
- 冻结守卫：lib/panel_context.py hash 492e2b87… → 3b1c26c7…（hash_updates 第 25 条，commit pending-kill5-retire）。
- 数据归档：/root/archive/retired-20261101-kill5/kill5-data.tar.gz（sha256 d8527a5df072ccaffcb2d4b648efa04771328e95a29e280f710ff0b1f126deaf，3 条目：seals/settlements/cron log），data/kill5_tail + kill5_tail_cron.log + .kill5_tail_job.lock + pyc 已清。
- 备份：/root/ops/kill5-family-20261101-1210/（run_stats.py / crontab / logrotate / .env / panel.html / kill3-freeze.json / model_tier_guard.json / seals / settlements / 三件套与派生脚本 .bak）。
- 提交：349e0a4（运行层+三件套删除）、d3dbde9（面板层）、62cff4c（派生统计）、本提交（冻结 hash + 本台账）。
- 回滚：git revert <本提交> 62cff4c d3dbde9 349e0a4 + scripts/crontab_tool.sh install + tar xzf 归档 + 恢复 /root/run_stats.py、/etc/logrotate.d/penta5、.env（均在 /root/ops/kill5-family-20261101-1210/）。

## 2026-11-01 12:26 彻底退役 kill2p（AI 二位置杀码 + 一合尾 + 两反向形态，5 项）

- 原因：用户发 kill2p 面板卡截图指认 kill2p 并说「这个也继续清理」，按彻底退役处理（停跑 + 删三件套 + 去全部挂接 + 面板区块剥除 + 数据归档），不留任何层；kill2 已于 12:10 退役、kill5 已于 12:18 退役，本框架为该「一合尾 + 反向形态」家族最后一套。
- 运行层：crontab 删 `*/5 ... run_kill2p_tail.py`（install 后 `crontab_tool.sh diff` = matches）；/etc/logrotate.d/penta5 删 data/kill2p_tail_cron.log 段；删除 scripts/run_kill2p_tail.py、lib/kill2p_tail.py、lib/kill_two_p_view.py。
- 面板：/root/run_stats.py 去 kill2p import 与 2 处 apply_kill_two_p_block 调用；panel.html KILL_TWO_P 区块（含 refresh names 里的 KILL_TWO_P）已剥除（KILL_TWO_P 计数 0，KILL_ONE/KILL_THREE 各 3）；lib/panel_live_refresh.py names 去 KILL_TWO_P；lib/attack_editor_view.py 删 kill2p 攻击语定义。
- 派生统计：hitrate_baseline rows=6（去 kill2p 行）、framework_hitrate_cumulative rows=6（去 kill2p 行）、tail_hold_review 去 kill2p 框架、model_usage 去 kill2p 日志源、ops_health 去 check_kill2p 与 kill2p_tail_cron.log、evidence_backup JSON_DIRS 去 kill2p_tail、position_kill_shadow 去 kill2p 条目并恢复 kill3（kill2 退役时误删 kill3，本次补回，FRAMEWORKS=kill1/kill3）、position_kill_review docstring 去 kill2p。
- A/B 证明：panel_context 删 _kill2p_self + kill2p_stats/kill2p_previous 后，kill3/kill1 固定 fixture（bare/kill3/kill1/kill3+kill1/rich）输出逐字节不变，sha256 5e8706a2…/ecfe0dfd…/57266d53…/6cd6cfe8…/a53a66f7…；新版对 kill2p 入参抛 TypeError。
- 冻结守卫：lib/panel_context.py hash 3b1c26c7… → 619e1938…（hash_updates 第 26 条，commit pending-kill2p-retire）。
- 数据归档：data/kill2p_tail（2.2M）移至 /root/ops/kill2p-family-20261101-121944/data_kill2p_tail；完整备份同目录（before_hitrate/before_cumulative/crontab/run_stats/panel.before.html/position_kill_shadow/logrotate/panel_context/kill3-freeze.json）。
- 提交：4ebe1a2（运行层+三件套删除）、4012f2e（面板层）、e8a06a8（派生统计）、本提交（冻结 hash + 本台账）。
- 回滚：git revert <本提交> e8a06a8 4012f2e 4ebe1a2 + scripts/crontab_tool.sh install + 把归档移回 data/kill2p_tail + 恢复 /root/run_stats.py 与 /etc/logrotate.d/penta5（均在 /root/ops/kill2p-family-20261101-121944/）。
- 补完：.env 删 PENTA5_MODEL_KILL2P（备份同目录 .env.bak）；scripts/model_tier_guard.py DEFAULTS targets 与 data/model_tier_guard.json targets 去 KILL2P（顺带清掉已退役的 KILL2，现 targets=KILL1/KILL3）。更正 e8a06a8 提交消息里的行数：hitrate_baseline 7→6 行、tail_hold_review 6→5 框架（不是 8→7/7→6）。

- 复核补正（独立复核，本提交）：
  - 数据归档：`/root/archive/retired-20261101-kill2p/kill2p-data.tar.gz`（sha256 `5f51311753e642c44120320147afb6580b8d1f2376d340383959af6846280dbf`，3 条目：data/kill2p_tail/{seals,settlements}.json + data/kill2p_tail_cron.log，`sha256sum -c` OK）；data/kill2p_tail_cron.log 与 .kill2p_tail_job.lock 已从 data/ 删除（data/ 内 kill2p 残留 0）。
  - 攻击语版本串 v14-retire-kill5 → **v15-retire-kill2p**（2 处，内联 JS `node --check` 通过，py_compile 通过）；panel.html 在下一次 run_stats 重建时携带新版本串。
  - 复核：crontab 36 作业且与 ops/crontab.txt `diff` 干净；`logrotate -d` 无错且无 kill2p 路径；.env 无 PENTA5_MODEL_KILL2P（备份 /root/ops/kill2p-family-20261101-121944/.env.bak）；model_tier_guard.py DEFAULTS 与 data/model_tier_guard.json targets=KILL1/KILL3；data/hitrate_baseline.json 与 data/framework_hitrate_cumulative.json rows=6（killer/framework/selected_six/he3_refine/kill3_tail/kill1_tail）；panel.html KILL_TWO_P 计数 0（KILL_ONE/KILL_THREE/KILL_SHADOW 区块标记各 1）；lib/panel_live_refresh.py 无 KILL_TWO_P；data/ops_health.json 12:27 overall=warn 无 FAIL 且 kill2p 检查项消失；source manifest 重建为 /root/backups/auto-baseline-20261101-123135/verification-manifest.json（423 files），二次 `--if-drift` 返回 no-drift。
  - 回滚补充：`tar xzf /root/archive/retired-20261101-kill2p/kill2p-data.tar.gz -C /root/penta5-runner/` 可恢复 data/kill2p_tail 与 cron log；版本串随本提交 revert 回退。
## 2026-11-01 12:50 合尾稳定化 hold_max 多档只读影子上线（5 框架 × hold2/hold3/hold5/不设上限）

- 原因：hold3-until-miss 已在 AI三位置、固定八码、七项独立v7、killer、AI单位置（kill1）转正，但「自动沿用最多几期」一直只用过 3；本次按需部署只读影子对照四档，不改任何封存/正式统计/攻击语。
- 实现：新增 lib/tail_hold_max_shadow.py（540 行）+ scripts/run_tail_hold_max_shadow.py；覆盖 kill1_tail、kill3_tail、七项独立v7、固定八码、killer 五个合尾框架，按已封存记录 + 真实开奖确定性回放 hold2 / hold3(现网) / hold5 / hold-until-miss 四档；输出 backfill+forward 的命中率、换码次数、沿用期数，并附 hold3 复现封存一致性校验。
- 回放口径：窗口自各框架首个 tail_hold3/tail_policy 元数据期起（kill1 20261101014、kill3 20261031255、six 20261031266、framework 20261031265、killer 20261031268）；窗口内 kill3 有 18 期无 hold 元数据的期按模型/封存计；连续性判据改为「上一封存 target == 当前 reference」，跨日界（如 six 20261031288→20261101001）不再误判。
- 面板/运行层：面板块 TAIL_HOLD_MAX_SHADOW 插在 POSITION_KILL_SHADOW 之后（markers 各 1）；lib/panel_live_refresh.py names 注册该块，version quiet-panel-v21-live-sections → quiet-panel-v22-live-sections；crontab 新增 */5 sleep45 flock /var/lock/penta5-tail-holdmax.lock timeout180 作业（36→37 作业，install 后 diff=matches）；/etc/logrotate.d/penta5 增 data/tail_hold_max_shadow_cron.log（logrotate -d 无错）。
- 12:50 首轮快照（cron 12:50:55，forward 各 1；hold3 复现封存全 100%：kill1 114/114、kill3 149/149、six 153/153、framework 156/156、killer 153/153）：
  - framework 156 期/312 注：现行=hold3 90.38%（换码 29）；hold2 90.38%（30）、hold5 89.74%（25）、不设上限 89.42%（20）。
  - killer 153 期/459 注：现行 89.98%（70）；hold2 90.63%（86）、hold5 90.20%（51）、不设上限 90.85%（40）。
  - 七项独立v7 153 期：现行 89.54%（46）；hold2 90.85%（58）、hold5 91.50%（29）、不设上限 91.50%（16）。
  - AI三位置 kill3 167 期：现行 87.43%（38）；hold2 88.02%（45）、hold5 88.62%（33）、不设上限 85.63%（31）。
  - AI单位置 kill1 114 期：现行 86.84%（27）；hold2 88.60%（30）、hold5 88.60%（25）、不设上限 90.35%（9）。
- 结论：四档命中率差异均在样本噪声内（n=114–167；单尾杀 1 码数学上限恒 90%）；hold3 与现行逐期一致证明复现正确；hold_max 只改变换码频率（不设上限换码最少），不提高命中率。按预登记口径等前瞻 n≥50 后复核再决定是否调整档位。
- 验证：py_compile；12:50:55 真实 cron 周期 status ok、cron 日志非空、五框架 forward 各 1；外网 nginx:3001 登录取面与磁盘 panel.html 逐字节一致（sha256 cb7c164441e4370de692f3fd307e95365d0f713384823f68cd1a67d40819ffd3）。
- 备份：/root/ops/tail-hold-max-shadow-20261101-124648/（panel_live_refresh.py.bak、crontab.txt.bak、logrotate-penta5.bak）。
- 提交：36edcf3（影子模块+runner+面板注册+cron 基线）、本提交（台账）。
- 回滚：git revert <本提交> 36edcf3 + scripts/crontab_tool.sh install + 恢复 /root/ops/tail-hold-max-shadow-20261101-124648/ 下 panel_live_refresh.py、crontab.txt、logrotate-penta5；并删除 lib/tail_hold_max_shadow.py、scripts/run_tail_hold_max_shadow.py、data/tail_hold_max_shadow.json 与 data/tail_hold_max_shadow_cron.log。

## 2026-11-01 13:07 AI 单位置杀码（kill1）v2 定位口径上线：热码 top3 + 上期重码/邻码（rule kill1-tail-v2-hot-repeat-one-form）

- 依据：用户质疑「AI 单位置杀码」与「AI 三位置杀码」定位依据同源——两框架提示词几乎相同、都盯近 50 期冷码。取证：kill1 近 30 期杀码 30/30 落在冷码 top3、25/30 为全位置最冷；85 个共同目标期里 kill1 位置 ∈ kill3 计入位置 74%、同位置同码 61%，组合不分散。用户拍板「照你的意思来部署到服务落地运行」= kill1 改非冷码口径、kill3 保持冷码。
- 口径：v2 起 kill1 定位依据改为「该位置近 50 期热码 top3（出现次数多、当前遗漏短优先）+ 上期该位置重码 + 上期邻码（±1，0↔9 循环）」，提示词明文禁止以上期冷码 / 长遗漏码作为定位依据；其余不变（1 位置杀 1 码 + 5 合尾 hold3-until-miss + 1 反向形态，前 4 球，05:00 清零）。kill3 口径不动（kill3-tail-v11-hold3-two-counted）。
- 改动（5 文件，+63/-11）：lib/kill1_tail.py（RULE_VERSION → `kill1-tail-v2-hot-repeat-one-form`；新增 `rank_digits_hot` / `_hot_line`；`build_prompt` 重写定位段并新增 audit `position_basis=hot-repeat-neighbor` / `hot_ranking` / `latest_row`，版本串 `kill1-tail-prompt-v2-hot-repeat`；docstring 记录 v1 归档口径）；lib/kill_one_view.py（`_foot_html` 增 v2 定位依据说明）；scripts/run_kill1_tail.py（published 打印加 `rule=`）；lib/hitrate_baseline.py 与 scripts/framework_hitrate_cumulative.py（各新增 v2 注册项 `(3, 0.7538)`，cum_kill1 默认 rule 改 v2；v1 注册行保留供历史记录分组）。
- 期望基线核验（10^4 全开奖空间 numpy 枚举，draws shape (10000,4)）：固定配置 P(pos)=0.90、P(tail)=0.90、P(form)=0.9375、P(form|pos)=0.930556 → P(group)=0.9×0.9×0.930556=0.75375；均匀配置抽样 4000 组均值 0.759277（sd 0.006813）。结论：命中结构与 v1 完全一致，注册常量 (3, 0.7538) 不变、面板口径行继续显示 75.38%；单位置杀码单项期望恒 90%，本改动不改变单框架命中率期望，目标是与 kill3 去冗余（历史 88 个共同期热码代理回放：同位置同码 53.4% → 0%），不得对外宣称命中率提升。
- 模块测试：py_compile 5 文件 OK；`build_prompt` 合成历史断言热码段 / 上期开奖行 / 重码邻码段齐全、无「各位置冷热」、audit 字段齐全、`latest_row.digits == window[-1]`；`rank_digits_hot([0,0,0,9,9,8,7,6,5,4])[0] == (0,3,7)`；`normalize_response` 对非法形态（被杀位未取反）仍拒绝；`apply_tail_policy` 正常；`kill_one_view.render_block()` 含 v2 口径 / 75.38% / v2 脚注。
- 上线验证（真实周期）：
  - 首期 v2：ref 20261101157 → target 20261101158，13:07:44.718 发布（AI 127.06s / deepseek-v4-pro / prompt 4165 / completion 11804 / reasoning 11735），封存 `个位 杀8 · 千百个合尾 杀8 · 大小反向形态 大大小小`，tail_policy 换码（上期未命中）。行为证明：ref 157 开奖 [1,4,8,8]，个位上期 8 为重码、个位热码 top3=[1,9,3]，候选集 {1,3,7,8,9}，杀码 8 属候选且非冷码；同期 kill3 计入位置 q:6/s:5，无同位置同码重叠。
  - 结算：ref157→158 为 v2 首期 `group_hit=True / verified=True`（target 158 开奖 [3,4,0,3]：个位 3≠8、q+b+g=3+4+3=10 尾≠8、实际 小小小小≠大大小小，三项全中）；settlements 累计 n=118。
  - 面板：panel.html KILL_ONE 标记各 1、v2 rule 1 处、v1 rule 0 处、`定位依据（v2 起）` 1 处、`整组精确基线 75.38%` 1 处；口径行「口径 kill1-tail-v2-hot-repeat-one-form · 自 ref 20261101157 起（本版 1 期） · 整组精确基线 75.38%（3 项）」；kill3 区块同期正常（v11 基线 61.91%）。
  - 第二期 v2：ref 20261101158 → target 20261101159，13:11:53 发布（AI 75.09s / deepseek-v4-pro），封存 千位 杀4，reason 记「千位4属热码邻码」。
- 备份：/root/ops/kill1-v2-20261101-130030/（6 个 .bak 备份文件 + data/kill1_tail/{seals,settlements}.json 快照 + sha256.before.txt + apply_kill1_v2.py，脚本 sha256 6c3eaf5e89e11b0be481f2d5e0b04a89188147c05ab661bda2f88e56452bbb69）。
- 提交：7d856e7 feat(kill1): v2 定位口径（5 files, +63/-11）；本提交（台账）。
- 回滚：`git revert <本提交> 7d856e7` 即回退代码与注册项；历史封存无需改动（v1 记录保留原 rule_version、统计并存），面板 / 统计下次刷新自动回 v1 口径。
- 冻结守卫：kill1 无 freeze guard（ops/kill3-freeze.json 仅覆盖 kill3），本次未改 kill3 相关 hash。

## 2026-11-01 16:08 AI 单位置杀码（kill1）v3 位置杀码稳定化上线：hold3-until-miss（rule kill1-tail-v3-position-hold3）

- 依据：位置杀码稳定化影子定点复核（scripts/position_kill_review.py）——kill1 forward 90 期/90 注 现行 88.89% → hold3 92.22%（+3.33pp）、换码 35→14；13:05 正式复核表 kill1 58 期/58 注 93.10%，hold2/hold3 93.10%、换码 5，三判据（n≥50 注、95%CI 不显著变差、换码下降）全过。kill3-tail-v11-hold3-two-counted 已是 hold3，无需改。用户 m00662「好，余额守卫不需要」确认执行转正方案、不上余额守卫。
- 口径（v3 起）：位置杀码按 hold3-until-miss 稳定化——某位置杀码命中后沿用同位置同杀码，最多自动沿用 3 期；失手或满 3 期后由模型重选（重选可与上期同码）。AI 选码与 prompt 完全不动，封存前做本地确定性后处理；封存新增 `position_policy` 追溯字段（model_kill/sealed_kill/reused_previous/run/note/previous）。整组随机期望不变 `(3, 0.7538)`；稳定化只改变换码频率与命中延续，不改变单位置杀 1 码随机基线 90%。
- 改动（5 文件，+101/-3）：lib/kill1_tail.py（RULE_VERSION → `kill1-tail-v3-position-hold3`；新增 POSITION_POLICY/POSITION_HOLD_MAX、_position_hold_replay/position_hold_state/apply_position_policy；create_seal 写 position_policy；docstring 记 v3 口径）；scripts/run_kill1_tail.py（请求后先 apply_position_policy 再 apply_tail_policy，打印 position policy note）；lib/hitrate_baseline.py 与 scripts/framework_hitrate_cumulative.py（各新增 v3 注册项 `(3, 0.7538)`，保留 v1/v2）；lib/kill_one_view.py（脚注：位置与杀码不做连用限制 → v3 位置杀码稳定化说明）。
- 单元测试：8 项全 PASS（无历史新选 / 命中沿用 run1→2 / 满 3 期换码 / 失手换码 / 单双形态字重算 / 大小形态字重算 / 同码形态不变 / v3 基线注册）；py_compile 6 文件 OK；真实封存数据集成测试（apply_position_policy + apply_tail_policy + create_seal）通过。
- 上线验证（真实周期）：首期 v3 ref 20261101193 → target 20261101194，16:08:08 发布（deepseek-v4-pro，AI 150.2s，prompt 4177 tokens），封存 `十位 杀4 · 千十个合尾 杀4 · 大小反向形态 小大大大`，rule_version=kill1-tail-v3-position-hold3、position_policy note=「新选（同位无沿用状态）」run=1、tail_policy「沿用上期尾码（第2期，上期命中）」；形态一致性校验 form[2]=='大'=reverse(4) 通过。旧封存 150 条不减（累计 151）。ops_health kill1_tail OK ref=20261101193；面板口径行「口径 kill1-tail-v3-position-hold3 · 自 ref 20261101193 起（本版 1 期） · 整组精确基线 75.38%（3 项）」。
- 影子退役：lib/position_kill_shadow.py FRAMEWORKS 清空（kill1 已转正、kill3 已是 hold3），面板块改为「位置杀码稳定化已全部转正」标注；data/position_kill_shadow.json frameworks={}；影子 cron 保留（每 5 分钟只重渲染该标注，不再采集）。
- 备份：/root/ops/kill1-hold3-160057/（6 个 .bak + data/position_kill_shadow.json 快照）。
- 提交：6b1bccd feat(kill1): v3 位置杀码 hold3-until-miss 稳定化（5 files）；a2ecc52 chore(shadow): kill1/kill3 转正后从位置杀码影子采集器退役（1 file）；本提交（台账）。
- 回滚：`git revert a2ecc52 6b1bccd <本提交>`；kill1 历史封存无需改动（v3 记录保留、旧 v1/v2 统计并存），面板 / 统计下次刷新自动回 v2 口径。
- 冻结守卫：ops/kill3-freeze.json 仅覆盖 kill3_tail/kill_three_view/panel_context/run_kill3_tail，本次均未改动，无需更新 hash_updates。

## 2026-11-01 16:43 AI 双反向冷形态排除（kill8）框架上线（rule kill8-tail-v1-cold-forms）
- 依据：用户要求建立独立框架——「位置排除数字由用户手动完成，模型排除 2 个反向冷形态」；kill8 为全新编号（不撞退役名 kill2/kill4/kill5/kill6/kill7）。
- 口径：整组 2 项 = 单双反向冷形态 + 大小反向冷形态；命中 = 目标期实际四位单双形态 ≠ 封存单双形态 且 实际四位大小形态 ≠ 封存大小形态（2 项全中才命中）；无合尾、无连用限制（每期按冷热/遗漏重选，不沿用上期）；位置排除数字由用户手动完成、不进框架计算；手动触发（非 cron），每期跑一次 = 结算上一期 + 封存下一期 + 刷面板。
- 冷形态定位依据：近 50 期出现次数少、当前遗漏长（16 种四位单双 × 16 种四位大小形态冷排序，各选最冷 1 种）。
- 改动（4 文件，+1152）：lib/kill8_tail.py（695 行，新：RULE_VERSION=kill8-tail-v1-cold-forms、FORM_KINDS=(parity,size)、form_space 16 种、rank_forms 冷排序、pattern_of 复刻 kill1、normalize_response 校验 parity_form/size_form、settle_pending 判 parity/size hit、build_attack='除<parity>除<size>'）；lib/kill_eight_view.py（312 行，新：KILL_EIGHT START/END 标记、锚点 KILL_ONE_END、FALLBACK_BASELINE=(2,0.8789)）；scripts/run_kill8_tail.py（108 行，新：手动触发版，无 wait_for_source）；lib/hitrate_baseline.py（+37：RULE_BASELINE 登记 kill8=(2,0.8789)、LABELS、_kill8_rows 过滤 parity 字段、build 里 extend）。
- 整组精确基线：均匀配置 10^4 全枚举平均期望 (15/16)^2 = 87.89%（2 项）；单项基线各 93.75%。
- 单元测试：模块测试全部 PASS（RULE_VERSION/FORM_KINDS/form_space 16 种/pattern_of 单双与大小/rank_forms 冷排序/build_attack/normalize_response 严格校验）。
- 上线验证（真实周期）：首期 ref 20261101200 → target 20261101201，16:42:33 发布（deepseek-flash，AI 1.0s，prompt 3327 tokens），封存「单双反向冷形态 单单单双 · 大小反向冷形态 小大小大」，rule_version=kill8-tail-v1-cold-forms；16:45:12 开奖 ['7','3','8','2'] → parity 单单双双（≠单单单双 命中）、size 大小大小（≠小大小大 命中）、整组 group_hit=true、verified=true。面板 KILL_EIGHT 区块插入 KILL_ONE_END 之后（KILL_EIGHT_START/END 各 1、kill-eight-ai 1）。hitrate_baseline rows 6→7（kill8 行 n=1 样本不足）。
- 备份：/root/ops/kill8-deploy/hitrate_baseline.py.bak.20261101163456（11771 字节）。
- 提交：03b291d feat(kill8): 双反向冷形态排除框架上线（4 files，+1152）；本提交（台账）。
- 回滚：`git revert 03b291d <本提交>`；data/kill8_tail/ 目录删除即可（新框架无历史封存）。


## 2026-11-01 16:47 joint_form 转正门禁 live 阈值收紧（0.0→0.5pp，防噪声级误转正）

- 依据：全框架命中率审计发现 killer 是唯一被判「偏离随机基线」(p=0.021) 的框架，根因是模型单项弱项（k2/k3/k4/he_all≈88.9% vs 90% 基线）9 项合取累积，非代码 bug；但转正门禁 MIN_LIVE_DELTA_PP=0.0 让「live 增益≥0 即通过」，killer 靠 backfill 形态层 +2.8pp（选择偏差：992 对形态选最大）转正，而 live 增益仅 +0.30pp（噪声级），backfill 增益 live 无兑现。用户拍板「先收紧 joint_form 转正门禁 live 阈值」。
- 口径：转正门禁 live 增益阈值 0.0→0.5pp（live delta 须 ≥0.5pp 才判 ready）；backfill 2.0pp、burn_in 72 期、strong 288 期、min_strong 1.5pp 均不变。改后 killer live_delta=0.296pp<0.5 → ready=false（此前 0.299pp≥0 误判 ready=true）；framework 仍 ready=false（-0.295pp）。killer 已转正的 enabled=true 状态不受影响（门禁从不回退已 enabled 项）。
- 改动（1 文件，+1/-1）：scripts/joint_form_promotion_gate.py:22 MIN_LIVE_DELTA_PP 0.0→0.5（附注释说明 backfill 形态层增益选择偏差 live 无兑现）。
- 验证：py_compile OK；手动跑 python3 scripts/joint_form_promotion_gate.py → killer ready=false(0.296<0.5)、framework ready=false、status=not-met；门禁为 cron 脚本（每 10 分钟 :04/:14/:24/:34/:44/:54 由 penta5-joint-guard 跑），无需重启，下次 cron 自动生效；refresh_source_manifest.py --if-drift 返回 no-drift；ops_health manifest_drift OK（auto-baseline-20261101-164708）。
- 提交：feab5b0 fix(gate): joint_form 转正门禁 live 阈值 0.0→0.5pp 收紧噪声级误转正；本提交（台账）。
- 回滚：`git revert <本提交> feab5b0` 即回退阈值到 0.0。
## 2026-11-01 17:05 合尾 hold_max 多档影子定点复核（预登记口径 n≥50）

- 结论：维持现网 hold3 不变（四档无显著差异；hold_max 只改换码频率、不提高命中率）。
- 数据（data/tail_hold_max_shadow.json，generated_at 17:00:57，start_period 20261101154；hold3 复现封存 backfill 仍 100%：kill1 159/159、kill3 196/196、six 202/202、framework 206/206、killer 203/203）。
- 前瞻 n：kill1 47、kill3 50、six 52、framework 52、killer 52（kill1 差 3 期到 50，其余达标）。
- 配对检验（McNemar，hold3 vs 各档，item 层）全 p≥0.25 无显著：kill1 vs hold5 p=0.250、six vs hold2 p=0.500、killer vs hold2/hold5/不设上限 p=0.727/0.581/0.754，其余 p=1.0；各档 Wilson 95%CI 高度重叠。
- 换码频率（前瞻段 ch 次数）：不设上限最少（kill1 3、six 4、killer 15），hold2 最多（six 21、killer 30）；命中率无系统性差异。
- 单尾杀 1 码数学上限仍 90%；本窗口 six hold2 94.2%（n=52）等 >90% 属小样本上浮。
- 报告：/root/ops/tail-hold-review/review-holdmax-20261101-1705.md。
- 待补：kill1 前瞻 n=47，17:30 复确认 n≥50 后关闭本次复核。
- 提交：本提交（本台账）。
## 2026-11-01 17:15 killer 智能深排策略回退 conditional-transition-v2 → v1 边际先验（消除杀0偏置 + 合取 -2.3pp）

- 依据：全框架命中率审计唯一偏离框架 killer 的根因定位——按 strategy_id 拆 data/r1_stats.json verified_evaluations（9 项）命中率：no-strategy n=925 joint=0.4086、conditional-transition-v1 n=94 joint=0.4255、conditional-transition-v2 n=1343 joint=0.3872（显著低于 10^4 精确枚举随机基线 0.4162/0.4188）。杀 0 占比 100% 由 v2 造成（v2 k1 .573/k2 .527/k3 .463/k4 .491、he_all 一度 68%；v1 ≈0%、no-strategy ≈均匀）。机制：lib/strategy_features.py:182 _transition_v2 用均匀先验 (count+1)/(n+10)，条件状态稀疏(n 小)时所有数字同分≈0.1，模型按「同分允许任意合法同分项」默认选 0；v1(_transition :88) 用边际先验 (count+1)/(50+10) 平滑不塌缩。v2 约 10-23 起作为未提交活编辑上线，10-30 才被 e6fd41d 大基线提交收录。用户拍板「回退到 v1 边际先验（推荐）」。
- 口径：killer 策略 conditional-transition-v2 → conditional-transition-v1（parameters prior_strength=10 / prior_laplace_alpha=1 / minimum_conditional_n=5 / maximum_transitions=49，即边际先验平滑，不塌缩）。9 项结构与精确随机基线 (9, 0.4162) 均不变；framework 保持 marginal-frequency-v2（p=0.776 健康）不动。v1 是 LEGACY 策略走 legacy 渲染分支（audit 无 request_policy），校验回退到基础 validate_killer_response（v1 本就无策略专属校验）。
- 改动（4 文件，+9/-7）：lib/ai_model_context.py（L27 POLICY_STRATEGIES killer→conditional-transition-v1；L249 build_model_context 默认策略 strategy_id or supported[0]→or POLICY_STRATEGIES[kind]；L456 prepare_model_request 守卫 in POLICY_STRATEGIES.values()→in (KILLER_STRATEGY, FRAMEWORK_STRATEGY)，避免 v1 legacy 分支被误判为「v2 body 冒充 legacy」而触发 missing-saved-independent-model-input-policy）；lib/hitrate_baseline.py 与 scripts/framework_hitrate_cumulative.py（各新增 RULE_BASELINE 注册项 ('killer','conditional-transition-v1'):(9,0.4162)，cum_killer 回退默认 rule 改 v1，保留 v2 注册行供历史分组）；lib/strategy_comparison.py（STRATEGIES killer→LEGACY_KILLER_STRATEGY=v1、LEGACY_STRATEGIES killer→KILLER_STRATEGY=v2，当前策略映射同步回退，v2 归 legacy 供 old_policy 计数）。
- 模块测试（真实快照 load_snapshot，ref 20261101206）：build_model_context 默认 strategy_id=conditional-transition-v1；features 非塌缩（q 位 smoothed_descriptive_share [0.037,0.139,0.102,0.046,0.157,0.093,0.120,0.194,0.093,0.019]、fallback=conditional-with-prior）；append_model_context + prepare_model_request 全链路 round-trip 无 raise、audit 无 request_policy；classic_killer_validator 返回基础 validate_killer_response（非 v2 _closure）。py_compile 3 文件 OK。
- 上线验证：killer 为 cron 脚本（每 5 分钟 :25 秒 python3 scripts/run_ai_ready_job.py killer，每次全新进程 import lib/ai_model_context.py，无需重启）；下一次 killer 运行（17:10:59）已产出 v1 封存：target=20261101207 strategy_id=conditional-transition-v1、kills={k1:[9],k2:[9],k3:[1],k4:[4]}（无杀0），对比前 3 期 v2 均含 [0]。
- 备份：/root/ops/20261101-170845/ai_model_context.py.bak、/root/ops/20261101-171133/{hitrate_baseline.py.bak,framework_hitrate_cumulative.py.bak}、/root/ops/20261101-171812/strategy_comparison.py.bak。
- 提交：58f63bc fix(killer): 回退 conditional-transition-v2→v1 边际先验，消除杀0偏置与合取 -2.3pp；b8cce56 fix(killer): strategy_comparison 当前策略映射同步 killer→v1；本提交（台账）。
- 回滚：git revert <本提交> b8cce56 58f63bc；历史封存无需改动（v2 记录保留原 strategy_id、统计并存），面板/统计下次刷新自动回 v2 口径。
## 2026-11-01 17:30 合尾 hold_max 复核收尾（kill1 n≥50）

- kill1 前瞻 n 已到 51；hold5 98.0%（50/51）vs hold3 92.2%（47/51）McNemar p=0.250（不显著），其余档 p=1.0。
- 最终结论：五框架均维持 hold3，本次预登记复核关闭；影子继续每 5 分钟只读运行（不改封存/统计/攻击语）。
- 报告：/root/ops/tail-hold-review/review-holdmax-20261101-1705.md。
- 提交：本提交（本台账）。

## 2026-11-01 17:32 AI 双反向冷形态排除（kill8）反向口径修正（rule kill8-tail-v2-reverse-cold-forms）
- 依据：用户指出 v1「独立选最冷形态再排除」没体现「反向」；确认「反向」= 形态在用户排除数字的位置取排除数字的相反单双/大小（排除双写单、排除大写小），未排除位置模型冷热自选；位置排除数字进框架作为反向基准（不再是「不进框架计算」）。
- 口径：整组 2 项不变（单双 + 大小反向冷形态，2 项全中命中）；约束位置必须取排除数字反面（例 q=2 双→单、s=4 双→单、g=6 双→单；大小 q=2 小→大、s=4 小→大、g=6 大→小），未排除位置（如百位）模型冷热自选；某位置排除数字方向混杂（单双混杂或大小混杂）时该位置不约束。命中 = 目标期实际四位形态 ≠ 封存形态。整组精确基线仍 (15/16)^2=87.89%（反向约束不改变随机基线的数学期望）。
- 数据流：前端 localStorage(kill8_position_exclusions_v1) → 启动键 POST {exclusions:{q,b,s,g}} → kill8_run_routes.js 写 data/kill8_position_exclusions.json → run_kill8_tail.py load_position_exclusions → build_prompt 反向约束段落 → 模型选形态 → normalize_response 校验约束位置取反面。
- 改动（5 文件，+119/-16）：lib/kill8_tail.py（RULE_VERSION v1→v2-reverse-cold-forms；新增 load_position_exclusions/reverse_constraints/_constraint_text；build_prompt 加反向约束段落；normalize_response 校验约束位置取反面；create_seal 记录 position_exclusions）；scripts/run_kill8_tail.py（读 exclusions 传给 build_prompt/request_prediction）；kill8_run_routes.js（POST 接收 exclusions 写文件）；lib/kill_eight_view.py（启动键 POST 带 exclusions；文案改「反方向」）；lib/hitrate_baseline.py（登记 v2 rule）。
- 模块测试：reverse_constraints 方向正确性全 PASS（双→单、单→双、小→大、大→小、混杂不约束、空不约束）；build_prompt 约束段落正确；normalize_response 约束校验正确。
- 上线验证（真实周期）：POST /api/kill8-run 带 {exclusions:{q:2,s:4,g:6}} → 写文件成功 → ref 20261101210 → target 20261101211，17:31:23 发布（deepseek-flash，AI 9.1s，prompt 3439 tokens），封存「单双反向冷形态 单单单单 · 大小反向冷形态 大大大小」，rule_version=kill8-tail-v2-reverse-cold-forms、position_exclusions={q:2,b:'',s:4,g:6}；反向正确（单双千/十/个=单、大小千/十=大 个=小，百位冷选）。
- 备份：/root/ops/kill8-deploy/*.bak.20261101172411（5 个文件）。
- 提交：3c6619e feat(kill8): 反向冷形态口径修正——位置排除数字决定形态反方向（5 files）；本提交（台账）。
- 回滚：git revert <本提交> 3c6619e；历史 v1 封存保留（rule_version 不变、统计并存），面板/统计下次刷新自动回 v1 口径。

## 2026-11-01 17:36 kill8 模型升级 deepseek-flash → deepseek-v4-pro
- 依据：用户要求 kill8 的 AI 模型调用 pro（"ia模型调用pro吧"）。
- 口径：仅模型档位升级，反向冷形态口径（整组 2 项、基线 87.89%、RULE_VERSION=kill8-tail-v2-reverse-cold-forms）不变。
- 改动（0 文件，仅运行时配置）：.env 新增 PENTA5_MODEL_KILL8=deepseek-v4-pro（此前 .env 无此变量，kill8 resolve_model fallback 到 PENTA5_MODEL_KILLER=deepseek-flash）。.env 在 .gitignore 不 commit；get_secret 每次热读 .env，无需重启 pm2。
- 上线验证（真实周期）：ref 20261101211 → target 20261101212，17:36:43 发布，requested_model=deepseek-v4-pro、returned_model=deepseek-v4-pro、elapsed=34.4s（<90s timeout 无超时风险；kill1/kill3 的 pro 耗时 43-153s，kill8 prompt 简单故更快）、completion_tokens=2984、reasoning_tokens=2947；封存「单双反向冷形态 单单单单 · 大小反向冷形态 大大大小」，rule_version=kill8-tail-v2-reverse-cold-forms、model=deepseek-v4-pro、model_label=DeepSeek-V4-Pro。
- 备份：/root/ops/kill8-deploy/.env.bak.*。
- 提交：本提交（本台账）。
- 回滚：删 .env 里的 PENTA5_MODEL_KILL8 即回退 flash（无需重启 pm2，下次触发生效）。
## 2026-11-01 18:15 evidence_backup 断更修复上线（链式刷新 + 3GiB 上限 + 870s 墙预算）

- 依据：ops_health WARN evidence_backup（verified_at=2026-10-31T05:13:31 断更 36h）。cron 9 5 * * * 跑 scripts/run_evidence_backup.py，11-01 05:09 失败 matching-source-manifest-unavailable。
- 根因（四个叠加）：① manifest drift——备份要求 source manifest sha256 与 live bytes 逐字节一致（lib/evidence_backup.py:331 newest_source_manifest→:226 _source_entries→:247 hash 不符 raise），04:55→05:09 窗口内另一窗口提交致 drift；② bundle 超上限——业务封存 JSON 增长（data/ ~1.34GiB + RDS system_snapshots）使 raw bundle 超 2GiB → backup-too-large（:477）；③ 超时——2GiB+ bundle 压缩+isolated-restore 验证超旧 480s 墙预算，被 cron SIGTERM 静默杀（日志空）；④ 活跃编辑竞态（仅业务时段）——newest_source_manifest(:338) 与 create_bundle(:452) 两次 _source_entries 之间 source 文件被另一窗口改动 → source-manifest-hash-mismatch。
- 修复（commit 0211e28，3 文件 +11/-9）：
  - ops/crontab.txt:19 evidence 备份 cron 前置链 refresh_source_manifest.py --if-drift &&，backup timeout 480→900。
  - lib/evidence_backup.py:27 MAX_TOTAL 2GiB→3GiB（磁盘检查需 2*MAX_TOTAL+1GiB=7GiB，free ~23GiB OK）。
  - scripts/run_evidence_backup.py:81 _budget(480)→_budget(870)、:84 >=470→>=860、:107 >455→>850（内部 SIGALRM 先于 cron 900s SIGTERM，打印 clean status）。
- 验证（手动重跑成功）：latest-verified.json verified_at=2026-11-01T18:14:43、archive=evidence-20261101-180915-60572994.tar.gz、size=323,927,874（324MB gz）、source_count=430、system_snapshots=13296、attempt=1；py_compile OK；crontab install+diff 通过。
- 长期待决策：seal/stat JSON 无界增长（10-31 249MB gz → 11-01 324MB gz），需 retention；活跃编辑竞态 hash-mismatch 仅业务时段触发，05:09 排程规避。
- 提交：0211e28 fix(evidence-backup)（本台账）。
- 回滚：git revert 0211e28；或 crontab 用 /root/ops/fix-20261101-1731/backups/ops/crontab.txt。


## 2026-11-01 18:00 kill8 启动键免密（viewer 登录即可触发）
- 依据：用户反馈"每次输入数字都要输入密码，不行"，要求简单按键——输入排除数字后点一下直接调模型。
- 口径：启动键不再要求 admin，登录面板（viewer 或 admin）即可触发；前端去掉管理员密码表单。
- 改动（2 文件）：kill8_run_routes.js（POST 去掉 admin 403 检查，viewer 也可触发；GET can_run 改为 true）；lib/kill_eight_view.py（_run_html 删除 k8-run-admin 表单 + auth() 函数，run() 直接 POST 用登录 cookie；401 提示"请先登录面板"）。
- 验证：viewer 身份 POST /api/kill8-run → 200 {ok:true,started:true}（脚本成功 spawn）；panel.html k8-run-btn=1、k8-run-admin=0。
- 备份：/root/ops/kill8-deploy/kill8_run_routes.js.bak.20261101175439、kill_eight_view.py.bak.20261101175439。
- 提交：f83cdc2 feat(kill8): 启动键免密——viewer 登录即可触发，去掉每次输管理员密码；本提交（台账）。
- 回滚：git revert <本提交> f83cdc2。

## 2026-11-01 19:25 seal/hit_stats 滚动归档上线（按月冷分片 + 主文件留近 7 天窗口）

- 依据：用户采纳「凌晨5点清零不清 seal，合理分配」——seal 是逐期预测历史账本（审计/回放/统计/evidence_backup 证据全靠它），不能清零只能滚动归档。data/ 4 个封存/结算 JSON 无界增长（selected_six_seals 533MB、selected_six_hit_stats 268MB、he3 各 106/104MB），json.loads 全量读内存紧张。
- 方案：按月滚动归档——lib/seal_archive.py 统一 loader（read_hot 只读主文件近期窗口 / read_full 冷分片+主文件透明合并 / archive 独立归档 / write_full_separated 写方分离），冷分片 data/archive/<stem>_<YYYYMM>.json.gz；主文件留近 7 天（keep_days=7），更老期号按月压 gz。json 级重写安全（seal 校验用 canonical json sha256，lib/selected_six_model.py:133 _digest sort_keys=True，与文件空格/key 顺序无关）。
- 口径：seals（he3_refine_seals/selected_six_seals）写方 run_once 改 read_hot（只读近期 append），读方 select_current/panel/stats 改 read_full；独立 cron archive() 每天移出超期期号。hit_stats（he3_refine_hit_stats/selected_six_hit_stats）读方 previous/seals 改 read_full（避免老 verified 重 pending），写方 atomic_json 改 write_full_separated（写时分离老 verified 进冷分片）。
- 改动（8 文件 +287/-12）：lib/seal_archive.py（新增 188 行）+ scripts/archive_seals.py（新增 CLI）+ lib/he3_refine.py / he3_refine_view.py / he3_refine_stats.py（he3 试点）+ lib/selected_six_model.py / selected_six_stats.py / selected_six_view.py（selected_six 接入）+ ops/crontab.txt（新增 03:30 archive cron）。
- 验证（真实归档 + 端到端）：he3_refine_seals 112MB→82.9MB（归档 1572 期→1.9MB gz）、he3_refine_hit_stats 104MB→76MB（分离 784 期→1.67MB gz）、selected_six_seals 533MB→437MB（归档 866 期→7.3MB gz）、selected_six_hit_stats 268MB→219MB（分离→3.6MB gz）；read_full 合并=归档前全量；归档后 run_selected_six 正常发布 20261101231、refresh 统计 hits=89/total=157 overdue=0 conflict=0；面板 _latest_retained 正常返回最新 published；cron 手动 --apply 复跑 archived=0 nothing-to-archive（空分片不重写主文件）。
- 备份：/root/ops/seal-archive-backup-20261101-190037/（4 个数据 JSON + 6 个代码文件 + crontab.txt）。
- 提交：668c03b（he3 试点）、08ed39f（selected_six 接入）、873de33（挂 cron + 空分片保护）；本提交（台账）。
- 回滚：git revert 873de33 08ed39f 668c03b；删除 data/archive/*.json.gz 并从备份目录恢复 4 个数据 JSON（冷分片合并回主文件）。

## 2026-11-01 22:20 kill3 位置杀码 + 合尾定位依据改热冷并列（v11 → v12）

- 依据：用户反馈「AI 三位置杀码 + 一合尾建议 这个框架排除码怎么都是取冷码框架，面板其他数据读取不到吗」——位置杀码与合尾都只取近 50 期冷码 top3，面板横向战绩（panel_context 的 killer/framework/six/selected_six/he3/stat50/cold_stats/model_tiers/three_avoid2/kill3_self）读得到但无逐位号码统计。用户选择（问卷 deploy_target）改口径：位置与合尾都改热冷并列，热码+冷码+重码+邻码都展示、模型自主选、不写「禁止用冷码」，升 v12。
- 口径：RULE_VERSION kill3-tail-v11-hold3-two-counted → kill3-tail-v12-hot-cold-hold3-two-counted；位置段每位置并列展示 热码 top3 + 冷码 top3，新增【上期开奖前四位（参考期 X）】与【上期重码/邻码候选（±1，0↔9 循环）】两段；合尾候选从冷码序改为热码序（新增 rank_digits_hot）；任务第 1 条加「定位依据从该位置的近 50 期热码 top3、冷码 top3、上期重码、上期邻码（±1）中自主选择，不限定只用冷码」；结构不变（2 计入位置 + 1 合尾 + 2 反向形态 = 5 项 / 2 注），整组精确基线沿用 0.6191（改的是 prompt 内容不是结构，10^4 枚举不重注册）。
- 改动（3 文件 +54/-6）：lib/kill3_tail.py（RULE_VERSION v12；新增 rank_digits_hot / _hot_line；build_prompt 位置段热冷并列 + latest/neighbor；tail 循环 rank_digits_hot；audit 加 position_basis=hot-cold-repeat-neighbor / hot_ranking / cold_ranking / latest_row）；lib/hitrate_baseline.py、scripts/framework_hitrate_cumulative.py（各加 v12 行 (5, 0.6191)，旧条目 v4/v5/v6/v7/v8/v9/v10/v11 全部保留）。
- 模块测试：py_compile OK；build_prompt 自测（50 期合成 window）——热码/冷码/上期开奖前四位/重码邻码段落齐全、audit version/position_basis/hot_ranking/cold_ranking/latest_row 正确、hot_ranking top 计数=max、cold_ranking top 计数=min（不写死命中哪一位）；check_kill3_freeze 复验 status=ok（v12 冻结 0/100 期重启）。
- 备份：/root/ops/kill3-v12-20261101-221443/（改动前 3 文件 cp -a）。
- 冻结守卫：ops/kill3-freeze.json rule_version v11→v12、files[lib/kill3_tail.py] hash 更新、frozen_at / freeze_ref_period(20261101268) / frozen_commit 重置、hash_updates 追加第 27 条。
- 提交：5d27f31 feat(kill3)（3 files）；本提交（台账 + freeze guard）。
- 回滚：git revert <本提交> 5d27f31；历史 v11 封存保留（rule_version 不变、统计并存），面板/统计下次刷新自动回 v11 口径。
- 待真实周期验证：seal v12 published + 按 rule_version 分组新旧并存（v11 与 v12 同期出现）。

## 2026-11-02 00:35 kill9 独立框架上线（四形态排除 + 二数合 + 三数合，整组 6 项）

- 依据：用户 m00001「给我弄一个框架，结合面板所有框架排除形态，给我四个形态排除。加二数合三数合。弄一个独立恐惧」（恐惧=独立封存）；m00072 补充「面板形态的，用 ia 模型筛选 4 个形态。下一局排除」。命名与四项口径经文字兜底确认（两次问卷超时，按技能 2b 文字列默认，用户回「开工」确认）。
- 命名：kill9（退役史查无 kill9，无撞名）。
- 四项口径：整组 6 项 = 4 形态 + 二数合 + 三数合（无位置杀码、无五选一合尾）；四个形态 = 单双(parity)+大小(size)+质合(prime)+中边(middle)，均按千百十个顺序每位置二值输出 4 字符（质合=质{1,2,3,5,7}/合{0,4,6,8,9}，中边=中{3,4,5,6}/边{0,1,2,7,8,9}）；二数合/三数合 = 保留集口径（同退役 kill2：he2 从 02468 留 4 码、he3 从 0-9 留 5 码 2奇3偶或3奇2偶）；沿用策略 = 每期模型完全自主重选（不 hold 不轮换、无位置排除输入，纯 AI）。
- RULE_VERSION：`kill9-tail-v1-four-forms-he2he3`。
- 命中语义：4 形态 = 目标期实际四位形态 ≠ 封存形态（反向排除项）；he2 = 6 组 pair(前4球任取2位)和值个位任一落入保留集；he3 = 4 组 triple(前4球任取3位)和值个位任一落入保留集；group = 6 项全中。settle entry 用 `prime` 字段（非 kill3 的 `counted`）。
- 整组随机期望 0.71085114（71.085114%）：10^4 开奖空间 × 均匀配置精确枚举（config 空间 he2=5、he3=200，非蒙特卡洛）；单项 P(4形态全中)=(15/16)^4=0.77247620、P(he2)=0.98080000、P(he3)=0.93750000(=15/16)；group=(15/16)^4 × E[P(he2|draw)P(he3|draw)]，按 draw 闭式条件概率求和。反证禁止相乘：朴素 0.9375^4×0.9808×0.9375=0.71033 ≠ 0.71085（he2/he3 与同 draw 相关）。Monte Carlo 交叉(N=10^6,seed=20261101) group=0.711107 噪声内一致。项目 SINGLE_BASELINES 已含 '四位反向形态'0.9375/'二数合留4码'0.9808/'三数合留5码'0.9375，与本次单项完全一致。
- 触发：cron `*/5` sleep45 错峰（kill3=25/kill1=35），锁 /var/lock/penta5-kill9t.lock，日志 data/kill9_tail_cron.log，独立 data/kill9_tail/ 封存+结算。
- 文件清单（14 文件）：三件套 lib/kill9_tail.py、lib/kill_nine_view.py、scripts/run_kill9_tail.py；挂接 lib/panel_context.py、lib/hitrate_baseline.py、lib/model_usage.py、lib/evidence_backup.py、lib/attack_editor_view.py、lib/panel_live_refresh.py、scripts/framework_hitrate_cumulative.py、scripts/ops_health_check.py、ops/crontab.txt、/root/run_stats.py（仓库外）、/etc/logrotate.d/penta5（仓库外）。
- 12 挂接点 grep 计数（全≥1，#10 N/A）：run_stats.py(kill_nine)=3、panel_context(kill9)=11、hitrate_baseline=7、framework_hitrate_cumulative=9、ops_health_check=13、model_usage=1、evidence_backup=1、attack_editor_view=2、panel_live_refresh(KILL_NINE)=1、crontab.txt=1、logrotate=1；#10 tail_hold_review.py 跳过（N/A：kill9 无合尾/tail_policy/hold 策略，4 形态+he2/he3 保留集非「合尾杀」，kill8 亦不在其 FRAMEWORKS 里）。
- 日志名五处一致（model_usage/ops_health_check/crontab.txt/logrotate/README 全用 data/kill9_tail_cron.log）：grep 'kill9_cron.log' | grep -v 'kill9_tail_cron.log' = 0。
- A/B 共享模块自证：KILL9_A_B_EQUAL True（pass 6/0）。OLD lib/panel_context.py sha=619e1938d3b21e4521828f47d913653d65f0022928bfa7b43bc9b4a097eebb52 → NEW=dc8146747461fac2f8202f9a8e5cf8509ed64077dcb6e895045a8701565ac79d。证明：旧模块无 _kill9_self；model_tiers/ai_self_frames 除 kill9 外逐项相等；build() 无 kill9 参数时输出逐字节一致；旧 build() 对 kill9 参数 TypeError（纯增量）。
- kill3-freeze.json：files['lib/panel_context.py'] 619e1938…→dc814674…；hash_updates 追加 1 条（at 2026-11-02T00:29:47+08:00、commit pending-kill9、reason=纯增量 A/B 证明 kill3/kill1 输出逐字节不变），n 27→28。
- 模块测试（/tmp/kill9_module_test.py）：pass 40/0（常量/pattern_of 正反/form_space 16/pair_triple_tails/he2=5/he3=200/build_attack/normalize_response 严格/settle 命中 6 项全中+反例 parity 不中）。渲染：空数据 12/0、fake seal 非空 7/0（6 张卡+攻击语完整）。服务器 py_compile 12 .py 全 OK；logrotate -d 语法 OK；crontab install 后 diff drift=0（previous 存 /tmp/crontab.backup.20261102_003333）。
- 证据期号（真实周期）：首个 */5 触发 00:35:45 → ref=20261102007 target=20261102008，00:35:52 发布，deepseek-flash（returned=deepseek-flash，elapsed 4.23s，prompt 4850/655 tokens）。封存 单双双双双单·大小小大大小·质合合合质·中边中中中中·二数合0246·三数合01345；panel.html kill-nine-ai=20、KILL_NINE_START=1，攻击语「除双双双单除小大大小除合合合质除中中中中二数合0246三数合01345各」。
- 备份：/root/ops/kill9-20261102/（panel_context.py.bak、run_stats.py.bak、logrotate_penta5.bak、kill3-freeze.json.bak + 8 个 repo 文件，旧 sha 已录）。
- 提交：feat(kill9) 三件套+数据目录；feat(stats/monitor) 派生统计挂接（期望值 0.71085114）；chore(ops) crontab/logrotate/kill3-freeze.json/README。
- 回滚：git revert <chore> <stats> <feat>；删 crontab 的 kill9 行 + logrotate 的 kill9 段 + data/kill9_tail/；kill3-freeze.json 用 /root/ops/kill9-20261102/kill3-freeze.json.bak 回退。

## 2026-11-02 02:52 kill9 v2 改口径（形态改为 大小/单双/全同/混合，整组 6 项不变）

- 依据：用户 m00557「从面板框架排除形态，挑选三个，一个大小单双混合」/ m00563「一共四组」/ m00566「除单单双双除小大大小除单单单除大小双单」/ m00579「漏一个字，单双都可以」/ m00598「让 ia 筛选三组出来，然后在加俩个大小单双大小混合形容跑」/ m00602「大小形态排除一组，单双形态排除一组，全大全小全双全单，这四个调一个排除，大小单双混合形态，排除一个」，最终 m00611 回「对」确认。整组仍 6 项 = 4 形态 + 二数合 + 三数合，无位置杀码、无合尾。
- 口径：①大小 ②单双 ③全同（大大大大/小小小小/双双双双/单单单单 四选一）④混合（每位置在 大/小/单/双 里选 1，如 大小双单）；形态命中 = 目标期实际四位形态至少一位不满足封存形态（反向排除）；二数合/三数合沿用保留集口径（任一组合和值个位落入保留集即命中）；模型每期完全自主（无 hold、无位置输入）。v1 的 ③质合 / ④中边退役为历史字段，旧封存原样保留不重算。
- RULE_VERSION：kill9-tail-v1-four-forms-he2he3 → kill9-tail-v2-size-parity-all-mixed；FORM_KINDS=('size','parity','all','mixed')；ALL_FORM_VALUES=('大大大大','小小小小','双双双双','单单单单')；COUNTED_FORMS=4；form_space：all=4、mixed=256、size/parity=16。
- 整组随机期望（结构变化 → 重算并替换注册值）：v2 = 0.71234352（71.234352%），v1 旧值 0.71085114 在 RULE_BASELINE 保留为追踪行。算法：10^4 开奖空间 × 均匀配置精确枚举（非单项相乘）；size/parity/mixed 各 15/16（mixed 256 个配置中同一 draw 下恰好 16 个满足），全同按 draw 条件 1−n_all/4，group=(15/16)^3 × E_draw[P(all|d)·P(he2|d)·P(he3|d)]；he2 配置空间 5、he3=200。自证：同一脚本复算 v1=0.7108511353，与上一节已注册 0.71085114 一致（算法同源）；v2−v1=+0.0014923828。脚本 kill9-staging/_enum_kill9_v2.py。
- 文件清单（7 改动）：lib/kill9_tail.py（925 行，4 形态 + v1/v2 双分支 settle）、lib/kill_nine_view.py（338 行，标签 大小/单双/全同/混合）、scripts/run_kill9_tail.py（日志行改 v2 字段）、lib/panel_context.py（_kill9_self / _ai_self_frame 字段 size/parity/all/mixed）、lib/hitrate_baseline.py（v2 基线行，v1 行保留）、scripts/framework_hitrate_cumulative.py（v2 基线行，累计按当前 rule）、ops/kill3-freeze.json、ops/README.md。
- 向后兼容：settle_pending 按 seal.rule_version 分支——v1 封存仍用 pattern_of 出 prime/middle，v2 用 form_hit 出 size/parity/all/mixed。实证：02:55:45 由 v2 代码结算 v1 封存 target=20261102035 → verified=True / group_hit=True，prime/middle 字段完好；v2 结算 target=20261102036 时 prime/middle=null、size/parity/all/mixed 有值（all.actual/mixed.actual 格式 '小大小小/单单双双'）。
- 验证：本机 py_compile 全 OK → sftp 上传回读 sha256 逐文件一致 → 服务器 py_compile 6/6 OK。模块测试 71/0（form_space all=4/mixed=256、rank_forms、normalize 严格校验、settle v2 正反 + v1 兼容分支）；渲染空数据 12/0、fake seal 7/0（6 卡 + 四标签 + 攻击语完整）；A/B v0→v2 6/0（KILL9_A_B_EQUAL True，旧模块无 _kill9_self、无 kill9 参数 build 逐字节一致）；A/B v1→v2 15/0（除 kill9 字段外 build 逐字节一致、kill1/kill3 帧完全一致、不传 kill9 不加键、v2 today 无 prime/middle）；派生挂接 8/0（hb/fhc 两处 RULE_BASELINE 均有 v2 行、build 行 rule 属已知版本）。
- A/B sha：lib/panel_context.py v1=dc8146747461fac2f8202f9a8e5cf8509ed64077dcb6e895045a8701565ac79d → v2=cd3494f74dcdc431880d390d5095ae68350625a0d18631da68631ba2b7ed1196（v0 参照 619e1938… 见上一节）。其余 v2 sha：kill9_tail a4bd1e01…、kill_nine_view 6676d238…、hitrate_baseline 6309997c…、framework_hitrate_cumulative 932c1c72…、run_kill9_tail c06e4c22…。
- 冻结守卫：files['lib/panel_context.py'] dc814674…→cd3494f7…；hash_updates 追加 1 条（commit pending-kill9-v2、reason=kill9 v2 形态字段重命名，A/B 证明 kill3/kill1 输出逐字节不变），n 28→29。check_kill3_freeze(ctx={'root':…,'now':…}) → status ok、changed_files=[]、v12 冻结 48/100 期。
- 证据期号（真实周期，cron */5 sleep45）：02:55:45 ref=20261102035 target=20261102036 首个 v2 封存 published 02:56:23 —— 大小 大小小大 · 单双 单单双双 · 全同 双双双双 · 混合 大大小双 · 二数合 0246 · 三数合 01256，攻击语「除大小小大除单单双双除双双双双除大大小双二数合0246三数合01256各」；03:00:45 第 2 期 ref=20261102036 target=20261102037（大小 大小小大 · 单双 双单双双 · 全同 双双双双 · 混合 大大小双 · 二数合 0268 · 三数合 01256），并结算 target=20261102036 → verified=True、group_hit=False（parity 单单双双 与开奖同为 单单双双，该形态未排除到，属正常失手）。seals 25 / settlements 24。
- 面板：panel.html KILL_NINE_START/END 各 1、kill-nine-ai data-as-of=20261102036、class="k9-card" ×6、全同 ×5 / 混合 ×5 / 质合 0、攻击语按新值精确存在。
- 派生统计：首 v2 结算后 hitrate_baseline / framework_hitrate_cumulative 的 kill9 行 = rule kill9-tail-v2-size-parity-all-mixed、n=1、hits=0、expected 0.71234352（need_n_2pp 3968，verdict 样本不足）。过渡期 1 期（最新封存仍为 v1 的 02:51~02:56）面板只渲染 4 张卡（全同/混合字段缺）、kill9 行暂缺（「当前规则版本」语义，与 kill3 v11→v12 同），v2 封存后自动恢复 6 张，无需回退逻辑。
- 备份：/root/ops/kill9-20261102-v2/（kill9_tail / kill_nine_view / panel_context / hitrate_baseline / framework_hitrate_cumulative 的 .v1.bak + ops/kill3-freeze.json.bak-kill9v2，旧 sha 已录）。
- 提交：feat(kill9) 三件套 + panel_context + kill3-freeze；feat(stats) 两处基线行；chore(ops) 本台账。
- 回滚：git revert <chore> <stats> <feat>；或 cp /root/ops/kill9-20261102-v2/*.v1.bak 回原文件名 + 恢复 ops/kill3-freeze.json.bak-kill9v2，再 py_compile 复验。
## 2026-11-02 03:45 kill9 v3 改口径（3 个形态从面板候选池挑选，整组 5 项）

- 依据：用户 m01009「我的意思变动的话是结合网页所有这期排除的形态全部集中起来，从这些形态中让ia，挑选三个形态排除」；m01030 回「好」确认我给的默认口径（3 形态 + 二数合 + 三数合 = 5 项；池 = kill1/kill3/kill8 本期已发布四位反向形态去重；kill8 手动启动可能过期，纳入但标注来源期/stale；AI 必须从池中挑、不得自造；命中口径不变；v1/v2 封存原样保留）。
- 口径：整组 5 项 = 3 形态（从候选池挑 3 个互不相同的四位形态）+ 二数合 + 三数合；形态类型仍只有大小(size)/单双(parity)；形态命中 = 目标期实际四位形态 != 封存形态（反向排除）；he2/he3 沿用保留集口径；无位置杀码/合尾/连用限制，每期模型自主重选。
- RULE_VERSION：kill9-tail-v2-size-parity-all-mixed → kill9-tail-v3-pool-three-forms-he2he3；FORM_KINDS=('size','parity')；COUNTED_FORMS=3；seal 新字段 forms / form_kinds / form_pool / pool_sha256（form_pool 每项含 form/kind/sources/source_labels/ref_period/target_period/published_at/stale）。
- 候选池实现：POOL_FRAMEWORKS=(kill1 form / kill3 reverse_parity+reverse_size / kill8 parity_form+size_form)，只取各目录 seals.json 中 publication_state=='published' 的最后一条；按 form 去重、同形态合并 sources；stale = 来源 target_period != 本期目标期；normalize_response 强制 forms 恰好 3 个、互不相同、⊆池（池 <3 时要求先选完池内全部才允许补足同类型）。
- 整组随机期望（结构变化 → 重算并注册）：0.75385763（75.385763%），v2 值 0.71234352、v1 值 0.71085114 保留为历史追踪行。算法（10^4 开奖空间精确枚举，非单项相乘）：E = E_pool × E_draw_he；E_draw_he=(1/10^4)Σ_draw[h2(d)·h3(d)]=0.920224（he2 5 候选、he3 200 候选，与 v2 同源）；E_pool 按池生成模型精确算：kill1 类型 1/2 均匀、kill3/kill8 各 parity+size 各 1 个且 16 均匀，去重后均匀挑 3（(2,3)(3,2)=0.81953125、(1,2)(2,1)=0.8203125、U=2 补位=0.8203125）→ E_pool=859005/1048576=0.8192110061645508；乘积=0.7538576289367676 → 注册 0.75385763。自证：同脚本复算 v2=0.7123435180664063，与已注册 0.71234352 一致（方法同源）。脚本 kill9-staging/_enum_kill9_v3.py。
- 文件清单（6 改）：lib/kill9_tail.py（池辅助/form_kind/build_form_pool/pool_forms/pool_sha256、build_prompt 候选池段、normalize 强制 ⊆池、create_seal forms、settle_pending 三路分支（v3/v2/v1）、today_stats forms、build_attack 3 形态）、lib/kill_nine_view.py（三形态标题/3 卡带来源/5 项统计/结算 forms 行）、scripts/run_kill9_tail.py（构建 pool 并传入 prompt 与 normalize，日志打印 pool 与 3 形态）、lib/panel_context.py（_kill9_self previous/stats 字段、_ai_self_frame kill9_tail 字段）、lib/hitrate_baseline.py、scripts/framework_hitrate_cumulative.py（各加 v3 基线行 (5, 0.75385763)，v1/v2 行保留；LABELS 改三形态；kill9 行过滤接受 forms）；ops/kill3-freeze.json、ops/README.md。
- 12 挂接点无新增（沿用 v2 的独立框架挂接）：cron/log 名/锁/data 目录/run_stats/ops_health/model_usage/evidence_backup/attack_editor_view/panel_live_refresh 均未改；tail_hold_review 仍 N/A。
- 验证：本机 py_compile 全 OK → sftp 上传后服务器 sha256 逐文件一致（kill9_tail 7536dfec…、kill_nine_view 3d581665…、run_kill9_tail 64705d56…、panel_context e7b29662…、hitrate_baseline 8d4122cc…、framework_hitrate_cumulative 7211a63a…）→ 服务器 py_compile 6/6 OK；上传后 file 检查发现三件套被本机 Windows patch 脚本写成 CRLF，已 CRLF→LF 规范后重传（最终 sha 即左列；git diff 收敛为 336/133/27 行真实改动）。模块测试 109/0（池去重/合并/stale/sha、normalize 强制 ⊆池 正反、settle v3 正反 + v2/v1 兼容、today_stats forms/legacy）；空渲染 12/0；fake seal 渲染 15/0（5 卡=3 形态+he2+he3、来源/过期标注、攻击语 3 个除、5 项统计、精确基线 75.39%）；派生挂接 12/0（hb/fhc v3 行注册、build 可产出）；A/B 6/0（KILL9_A_B_EQUAL True；旧 lib/panel_context.py sha=619e1938d3b21e4521828f47d913653d65f0022928bfa7b43bc9b4a097eebb52 → 新 e7b2966216dcf4405e9a784b25f008341247b52575ad11a4cbe866eb089e0e27；无 kill9 参数 build 逐字节一致、除 kill9 外 tiers/frames 不变）。
- 证据期号（真实周期，cron */5 sleep45）：首个 v3 封存 03:35:45 触发 ref=20261102043 target=20261102044，published_at=2026-11-02T03:35:54.674783+08:00，deepseek-flash（elapsed 4.61s，prompt 4675 / completion 750）；封存 forms=[大大小小(size), 小大小大(size), 单双双单(parity)]，二数合 0246、三数合 01235，攻击语「除大大小小除小大小大除单双双单二数合0246三数合01235各」；form_pool 5 项全部 stale（kill1/kill3 来源 target=20261102043、kill8 来源 target=20261102027，均早于本期 044），面板逐卡标注「来源 kill1/kill3、来源期 …」。第二个 v3 封存 03:40:45 ref=20261102044 target=20261102045（单双单双 / 双单单单 / 小大小大，二数合 0246、三数合 01235）；第三个 v3 封存 03:45:45 ref=20261102045 target=20261102046（小大小大 / 双单双双 / 大大小小，二数合 0246、三数合 02457）。
- 首个 v3 结算：target=20261102044 verified=True、group_hit=True；settlement['forms']=[{大大小小 actual 小小小小 hit true},{小大小大 actual 小小小小 hit true},{单双双单 actual 双双双单 hit true}]，he2/he3 均 hit。v2 旧封存 target=20261102043 由 v3 代码结算（v2 分支）：size/parity/all/mixed 字段完好、verified=True、group_hit=False，证明三路兼容；第二个 v3 结算 target=20261102045 verified=True、group_hit=True（03:45:48）。
- 派生统计：hitrate_baseline / framework_hitrate_cumulative 的 kill9 行已切到 rule=kill9-tail-v3-pool-three-forms-he2he3、items=5、expected=0.75385763、n=1 / hits=1、first/last period=20261102044、coverage_24h=0.861111，verdict=样本不足(n<200)；target 045 结算后下次派生刷新 n=2 / hits=2。
- 面板：panel.html KILL_NINE_START/END 各 1；k9-card 出现 6 次（5 张卡 + CSS 1）；data-as-of="20261102045"（03:46 核验）；标题「AI 三形态排除 + 二数合 + 三数合建议」；3 张形态卡带来源/来源期；统计行「整组命中率（5 项全中）100.00%（1/1 期）」+ 形态1/2/3 各自基线 93.75% + he2 98.08% + he3 93.75%；版本行「整组精确基线 75.39%（5 项）」；攻击语按新值精确 1 处；「全同/混合/质合」残留 0。
- 冻结守卫：files['lib/panel_context.py'] cd3494f7…→e7b29662…；hash_updates 追加 1 条（commit pending-kill9-v3），n 29→30。
- 备份：/root/ops/kill9-20261102-v3/（6 个 .v2 + kill3-freeze.json.bak-kill9v3）。
- 运维复验：scripts/refresh_source_manifest.py --if-drift 写出 /root/backups/auto-baseline-20261102-034426/verification-manifest.json（434 files）；ops_health overall=WARN，仅 kill3_stability/coverage/model_usage 历史告警，kill3_freeze、manifest_drift、crontab_drift、kill9_tail 均 OK。
- 提交：feat(kill9) 366a711（三件套 + panel_context + kill3-freeze）；feat(stats) e98fa00（两处基线行）；chore(ops) 本台账（正文修正后 amend）。
- 回滚：git revert <chore> <stats> <feat>；或 cp /root/ops/kill9-20261102-v3/*.v2 回原文件名 + 恢复 ops/kill3-freeze.json.bak-kill9v3 后 py_compile 复验；v1/v2 封存与结算原样保留不受影响。

## 2026-11-02 04:12 kill9 v4 加 1 个全同形态（4 选 1），整组 6 项

- 依据：用户 m01624「在加，大大大大，小小小小，单单单单，双双双双，这四个形态。每一期叫模型从四组中排除一组」→ 口径：在 v3（3 池选形态 + 二数合 + 三数合 = 5 项）基础上加 1 个形态排除项，选择空间恰为 4 个全同形态（大大大大/小小小小/双双双双/单单单单），每期模型从 4 个里挑 1 个（不得与 3 个池选形态重复），整组 6 项 = 3 池选形态 + 1 全同形态 + 二数合 + 三数合；命中口径不变（形态命中 = 目标期实际四位形态 != 封存形态）；he2/he3 沿用保留集口径；v1/v2/v3 封存与结算原样保留。
- RULE_VERSION：kill9-tail-v3-pool-three-forms-he2he3 → kill9-tail-v4-pool-three-plus-all-form-he2he3；COUNTED_FORMS=3（池选数）、ALL_FORM_PICK=1、TOTAL_FORMS=4；新增历史常量 V1_RULE/V2_RULE/V3_RULE，settle 显式版本分支 is_v1/is_v4/is_v3/else-v2（不再用「有 forms 即 v3」的模糊判定）；封存新增 all_form，normalize 强制 all_form ∈ 4 个全同形态且不得与 forms 重复；build_attack 第 4 个「除」取 all_form。
- 整组随机期望（结构变化 → 重算并注册）：0.70822528（精确 371314016703/524288000000 = 0.7082252821025848），v3 0.75385763 / v2 0.71234352 / v1 0.71085114 保留为历史追踪行。算法（10^4 开奖空间精确枚举，非单项相乘）：E = E_pool3 × E_draw_all_he；E_pool3 沿用 v3 已注册池选 3 形态项 859005/1048576 = 0.8192110061645508（同一池生成模型）；E_draw_all_he = (1/10^4)Σ_draw[(1 - n_all(d)/4)·h2(d)·h3(d)] = 2161303/2500000 = 0.8645212，其中 n_all(d) = 该开奖同时满足的全同形态个数（大>=5/小<5 逐位全满足、双=偶/单=奇 逐位全满足；四个全同形态互斥 → 随机开奖 P(满足)=1/4，单项基线 75.00%），h2/h3 与 v3 同源（he2 5 候选、he3 200 候选）。自证：同脚本复算 v3 = 0.7538576289367676（注册 0.75385763）、复算 v2 = (15/16)^3 × E_draw_all_he = 0.7123435180664063（注册 0.71234352）。脚本 kill9-staging/_enum_kill9_v4.py、_calc_v4.py。
- 文件清单（7 改，6 源码 + 冻结表）：lib/kill9_tail.py（TOTAL_FORMS/all_form、prompt 增全同形态段与 JSON all_form、normalize 校验、create_seal all_form、settle v4 分支 4 项（第 4 项 kind='all'，actual=size/parity 组合）、today_stats TOTAL_FORMS、build_attack 第 4 个除）、lib/kill_nine_view.py（四形态标题、4 张形态卡（形态4=全同）、6 项统计、形态4 随机基线 75.00%（其余 93.75%）、精确基线 70.82%、scope/foot/attack 文案）、scripts/run_kill9_tail.py（published 行打印形态4/all）、lib/panel_context.py（_kill9_self previous + _ai_self_frame kill9_tail 字段加 all_form）、lib/hitrate_baseline.py 与 scripts/framework_hitrate_cumulative.py（各加 v4 行 (6, 0.70822528)，v1/v2/v3 行保留；LABELS 四形态（6 项）；fhc cum_kill9 默认 v4）、ops/kill3-freeze.json。
- 12 挂接点无新增（沿用 v2/v3 的独立框架挂接）：cron/log 名/锁/data 目录/run_stats/ops_health/model_usage/evidence_backup/attack_editor_view/panel_live_refresh 均未改；tail_hold_review 仍 N/A。
- 验证：本机 py_compile 全 OK → sftp 上传后服务器 sha256 与本机逐一致：kill9_tail 7e51f65a…（50384B）、kill_nine_view 8306e773…（21113B）、run_kill9_tail 6e29f252…（6055B）、panel_context 79031796…（18641B）、hitrate_baseline b777f5c0…（15165B）、framework_hitrate_cumulative ad2ee0e3…（47345B）、kill3-freeze 9fb6decf…；file 检查 UTF-8 非 CRLF；服务器 py_compile 6/6 OK。模块测试 124/0（池辅助/常量/形态判定/攻击语 v4 4 个除与 v3 回落/normalize all_form 正反与重复校验/settle v4 正反 + v3/v2/v1 兼容/today_stats 4 形态与 legacy）；空渲染 12/0；fake seal 渲染 16/0（6 卡 = 4 形态 + he2 + he3、形态4 全同卡、四形态标题、攻击语 4 个除、scope/统计 6 项、形态4 基线 75.00%、精确基线 70.82%）；派生挂接 14/0（hb/fhc v4 行 (6,0.70822528) 注册）；A/B 6/0（KILL9_A_B_EQUAL True，OLD_SHA 619e1938d3b21e4521828f47d913653d65f0022928bfa7b43bc9b4a097eebb52 / NEW_SHA 79031796e329ded522aff655e7a6d6cc064654ba67c3e4d64df4cad10b2eba38；无 kill9 入参 build 逐字节一致、除 kill9 外 tiers/frames 不变）。注：module 首跑 3 个 v4 断言写错（实际 [2,4,6,8] 的 size 形态是「小小大大」而非「小小小小」；反例 all_form 应取实际满足的「双双双双」才不命中），修测试期望后 124/0，代码未改。
- 真实周期证据：① 兼容：v3 封存 target=20261102048 由 v4 代码结算（v3 分支）verified=True、group_hit=True、forms 3 项。② 首个 v4 封存：cron 04:00:45 触发 ref=20261102048 target=20261102049，published_at=2026-11-02T04:00:57.871296+08:00（deepseek-flash），forms=[小大大大(size), 双单双双(parity), 大大小小(size)]、all_form=双双双双，form_pool 5 项（pool_sha256 b31773c6fe96…），二数合 0246、三数合 24579，攻击语「除小大大大除双单双双除大大小小除双双双双二数合0246三数合24579各」。③ 首个 v4 结算：target=20261102049 verified=True、group_hit=True；settlement['forms'] 4 项 = [小大大大 actual 大小小小 hit true, 双单双双 actual 单单双双 hit true, 大大小小 actual 大小小小 hit true, 双双双双 kind=all actual 大小小小/单单双双 hit true]，he2 actual 123789 hit、he3 actual 0389 hit。④ 第二/第三个 v4 封存：ref=20261102049 target=20261102050（04:05:51，双双单双/双单单单/小大小小 + all_form 大大大大，二数合 0246、三数合 24579）、ref=20261102050 target=20261102051（04:10:57，大大小小/小大小大/双单双双 + all_form 大大大大，二数合 0268、三数合 24579）。
- 派生统计：首个 v4 结算后 hitrate_baseline kill9_tail 行 rule=kill9-tail-v4-pool-three-plus-all-form-he2he3、items=6、expected=0.70822528、n=1/hits=1；framework_hitrate_cumulative 同 rule、items=6、n=1/hits=1、first/last=20261102049、coverage_24h=1.0。
- 面板：data-as-of="20261102050"（04:12 核验）；KILL_NINE_START/END 各 1；k9-card 7 次（6 张卡 + CSS 1）；标题「AI 四形态排除（池选三 + 全同一）+ 二数合 + 三数合建议」；4 张形态卡（形态4 全同）；统计行「整组命中率（6 项全中）」+ 形态1/2/3 基线 93.75% + 形态4 基线 75.00% + he2 98.08% + he3 93.75%；版本行「整组精确基线 70.82%（6 项）」；攻击语与 ref=050 封存一致（4 个除：大大小小/小大小大/双单双双/大大大大，二数合0268三数合24579）；kill9 块内「三形态」「5 项」残留 0、「75.39」0。
- 冻结守卫：files['lib/panel_context.py'] e7b2966216dcf4405e9a784b25f008341247b52575ad11a4cbe866eb089e0e27 → 79031796e329ded522aff655e7a6d6cc064654ba67c3e4d64df4cad10b2eba38；hash_updates 追加 1 条（commit pending-kill9-v4），n 30→31。
- 备份：/root/ops/kill9-20261102-v4/（6 个 .py 为 v3 源文件，kill3-freeze.json.bak-kill9v4 = HEAD 原版）。
- 提交：feat(kill9) 087fcb2（三件套 + panel_context + kill3-freeze）；feat(stats) 4b4ac68（两处基线行）；chore(ops) 本台账。
- 回滚：git revert 本 chore → 4b4ac68 → 087fcb2；或 cp /root/ops/kill9-20261102-v4/{kill9_tail.py,kill_nine_view.py,run_kill9_tail.py,panel_context.py,hitrate_baseline.py,framework_hitrate_cumulative.py} 回对应路径 + cp kill3-freeze.json.bak-kill9v4 ops/kill3-freeze.json 后 py_compile 复验；v1/v2/v3 封存与结算原样保留不受影响。

## 2026-11-04 00:23 kill3 三数共现避开 3 组计入整组（v12 → v13，5 项 → 8 项）

- 依据：用户 m00003「帮我把三数共现3组，加到这个框架，攻击语是除对数579-349-089」（附图：KILL_THREE 面板块 5 项 69.12% / 三数共现避开 1/2/3 组块 579/468/089）。问卷三项确认：① 整组项数 8 项（三组各算 1 项）；② 攻击语组序按 1/2/3 组标签（579-089-349），非字面示例顺序；③ 升 v13 替换现网 v12，旧封存原样保留。
- 口径：RULE_VERSION `kill3-tail-v12-hot-cold-hold3-two-counted` → `kill3-tail-v13-hot-cold-hold3-two-counted-three-avoid`；整组 = 2 个计入位置 + 1 个合尾 + 2 项反向形态 + 三数共现避开 3 组 = 8 项同时命中。三数组由后端 extraKills 按目标期给定（`threeDigitAvoid`/`threeDigitHit`/`threeDigitAvoid2` = 避开1/2/3 组，与 lib/three_avoid2_stats.py 同源同序），模型不选、不写进 prompt 的自主选择段（作为固定段给出）；命中 = 该组 3 个数字未全部出现在 4 球开奖号中。攻击语在四定合尾与形态之后追加「除对数<1组>-<2组>-<3组>」，与 algorithms/framework/four-ding-framework/engine.js:906 同格式（面板/校验正则 `除对数\d{3}-\d{3}-\d{3}` 命中）。位置/合尾策略不变（3 位置展示、2 计入、display_only 不攻击、hold3-until-miss）。
- 整组随机期望（结构变化 → 重算并注册）：0.5863（0.586250）。算法 = 逐 v12 封存配置按其当期 3 组三数共现在 10^4 开奖空间精确枚举 8 项整组期望，再按可用 109 期（v12 seals ∩ state.json predictions 三数组）平均；明细 /root/ops/kill3-v13-enumeration.json，脚本 /tmp/enum8.py。自证：同法复算 5 项期望 0.621943（已注册 0.6191、v4/v5 95 期 0.6207，同量级）；observed vs expected 同 n：exp5 0.621943 / obs5 60.55%（66/109）、exp8 0.586250 / obs8 57.80%（63/109）。
- 文件清单（5 源码 + 2 运维文件）：lib/kill3_tail.py（+144/-14：docstring 8 项、RULE_VERSION、THREE_AVOID_KEYS/LABELS/ITEMS、`_group_code`/`resolve_three_avoid`/`group_avoid_hit`、build_attack 追加除对数段、build_prompt three_avoid 固定段 + 任务 8 项、settle_pending three_avoid 逐组命中、today_stats 三数共现战绩）；lib/kill_three_view.py（+38：3 张「三数共现 避开N组」卡、8 项整组统计 + 三数共现合并/逐组一行、结算行三数共现命中、脚注口径）；scripts/run_kill3_tail.py（+25/-：`_resolve_three_avoid(target, deadline)` 等待三数组落盘、build_prompt/create_seal 传入、published 日志打印）；lib/hitrate_baseline.py 与 scripts/framework_hitrate_cumulative.py（各加 v13 行 `(8, 0.5863)`，v4–v12 全保留）；ops/kill3-freeze.json、ops/kill3-stability-plan.md。
- 真机首期实测缺陷与修复：首轮（00:25:50）三数组尚未落盘（主预测管线实测开奖后 +63s 写入 state.json，本任务 +38s 启动），原 20s 固定等待在 +58s 超时 → SystemExit 漏发该期；改为按 deadline（目标期槽位 − CLOSE_SECONDS）倒推等待（reserve 75s、上限 150s），commit 4225b30。修复后 cron 自动路径连续 3 期正常（见证据期号）。
- 验证：改后 py_compile 5/5 OK；模块测试 40/40（RULE_VERSION/常量、resolve 现网+混合+缺失、group_avoid_hit 正反与重复数字、攻击语含除对数且正则命中、legacy 无该段、prompt 段序/8 项/audit、settle 8 项正反（4419 全中 True；5789 三组共现 False；旧封存空三家恒 True）、today_stats 合并/逐组、view 统计/结算行/渲染现网 v12 封存不炸、脚注 8 项）。面板实测：KILL_THREE 块 9 张卡 = 3 位置 + 合尾 + 2 形态 + 3 组卡；统计行「整组命中率（8 项全中：2 位置 + 合尾 + 2 形态 + 三数共现 3 组）」+「三数共现避开 3 组（单项基线97.96%）100.00%（2/2 期；逐组 100%）」；版本行「口径 kill3-tail-v13… · 自 ref 20261104005 起 · 整组精确基线 58.63%（8 项）」；攻击语 textarea 与封存一致（含 `除对数579-268-089`），data-as-of=20261104007。
- 证据期号（真实周期，cron */5 sleep25）：① ref=20261104005 → target=20261104006，published_at 2026-11-04T00:28:04.246179+08:00（deepseek-flash，elapsed 20.5s，prompt 4574 / completion 3553），三数组 579/268/089，攻击语「千123456790百123456890十1234567890个1234567890四定千百个合123456789除对数579-268-089除单双双双除小小小大」；② ref=20261104006 → target=20261104007，00:31:36.883999，攻击语 …除对数579-268-089除单双双单除小小大小；③ ref=20261104007 → target=20261104008，00:36:52.885554。首期结算 target=20261104006（actual 1/9/2/2）verified=True、group_hit=True、three_avoid 三组 hit 全 True；第二期 target=20261104007（actual 5/6/4/2）verified=True、group_hit=True、三组 hit 全 True；today_stats total=2 / group_hits=2 / 三数共现合并 2/2。
- 派生统计：data/hitrate_baseline.json kill3_tail 行 rule=kill3-tail-v13-hot-cold-hold3-two-counted-three-avoid、items=8、expected=0.5863、n=1/hits=1；data/framework_hitrate_cumulative.json 同 rule、items=8、expected=0.5863、n=2/hits=2、first/last=20261104006/20261104007、as_of=20261104007、verdict=样本不足(n<200)。旧 v12 行由派生脚本按「最后一期封存 rule」自动切换，历史 v4–v12 行保留追踪。
- 冻结守卫：ops/kill3-freeze.json rule_version→v13、files 三个变更文件新 hash（kill3_tail 3c1eeba3…、kill_three_view e4757632…、run_kill3_tail 0261afc2…；panel_context 79031796… 未变）、frozen_at 2026-11-04T00:23:17 / freeze_ref_period 20261104005 / frozen_commit ccec6f2 重置、hash_updates 追加 4 条（n=31→35）；ops/kill3-stability-plan.md 记入用户指令例外（v13）。
- 运维复验：scripts/refresh_source_manifest.py --if-drift 写出 /root/backups/auto-baseline-20261104-003845/verification-manifest.json（434 files）；ops_health overall 仅历史告警，kill3_freeze=OK（freeze v13 · 进度 3/100 期 · 冻结中）、manifest_drift=OK、crontab_drift=OK；kill3_stability=WARN 属预期（24h version_changes=1 = v12→v13 切换 + 历史 missing_slots/coverage 缺口）。
- 备份：/root/ops/kill3-ai-001937.bak（改动前 6 源文件 + freeze json + seals/settlements 快照）。
- 提交：feat(kill3) ccec6f2（三件套）→ feat(stats) 660cfd1（两处基线行）→ fix(kill3) 4225b30（三数组等待窗口）→ chore(ops) 384aac0（冻结守卫 + 稳定方案）→ chore(ops) 本台账。
- 回滚：git revert 本台账 → 384aac0 → 4225b30 → 660cfd1 → ccec6f2；或 cp /root/ops/kill3-ai-001937.bak 内 6 个 .py 回原路径 + 恢复 ops/kill3-freeze.json + py_compile 复验；v13 封存/结算只读保留（新代码对无 three_avoid 的旧封存仍按 5 项口径结算，v1–v12 封存不受影响）。面板/统计下次刷新自动回 v12 口径。
- 备注：攻击语组序按问卷确认取 1/2/3 组标签序（579-089-349 语义），与用户字面示例「579-349-089」（1 组/3 组/2 组）不同，仅 `'-'.join` 顺序一行可换；实盘组值由后端每期给定（当前连续为 579/268/089）。

## 2026-11-04 01:24 面板自动刷新永久失效修复（kill1/kill9/kill8 区块补 </section>，刷新名单 v24）

- 依据：用户 m00001「数字不跳动，要按f5，才会刷新」（确认目标为线上 penta5 面板）。
- 根因：lib/kill_one_view.py / lib/kill_nine_view.py / lib/kill_eight_view.py 的 render 返回值各 2 处漏写 `</section>`（`_foot_html(), END))`），面板 HTML 里 KILL_ONE/KILL_NINE/KILL_EIGHT 三段各只有 1 个 `<section>`、0 个 `</section>`；浏览器解析后 kill-one 的 section 未闭合，其后整页内容被嵌套进该 section，区块 START 评论在 section 外、END 评论在 section 内 → 浏览器里两者 parentNode 不同 → panel_live_refresh 的 ranges() 每次 throw `unbalanced-marker:KILL_ONE`（names 顺序首个失败）→ 8s 整页自动刷新永久失效，只能按 F5。与「数字不跳动」症状完全一致。
- 修法：三个 view 的每处 `_foot_html(), END))` → `_foot_html(), '</section>' + END))`；lib/panel_live_refresh.py 版本 `quiet-panel-v23-live-sections` → `quiet-panel-v24-live-sections`，names 在 `'TAIL_HOLD_MAX_SHADOW'` 后追加 `'MODEL_USAGE','JOINT_FORM_SHADOW','HITRATE_BASE'`（这三块页面存在但原先不在刷新名单，其中模型运行卡片每分钟更新余额/Token 却永不自动刷新）。**KILL_EIGHT 故意不加名单**：块内有用户手填的 k8-ex-q/b/s/g 排除码输入框与 k8-combined-attack textarea，morph 会用服务端值覆盖手动输入；kill8 仅在手动 run 后变化，且 run 完成脚本自动 location.reload。
- 面板现状：34 个 marker 区块 section/div/style/textarea 全配平；面板底部脚本为 v24；刷新 names 含 MODEL_USAGE/JOINT_FORM_SHADOW/HITRATE_BASE；git status 仅 4 个 lib 文件（panel.html 为 skip-worktree 生成物，不入库）。
- 验证（无头 Chrome，/tmp/panelsnaps 可复现环境）：① 修复前每次 refresh 都失败，instrument 后 title 捕获 `unbalanced-marker:KILL_ONE`；② 修复后用两次真实重生成快照 A6/B6（01:26:18、01:26:22，均 v24 且平衡）跑 8s 自动刷新：dump 中 STAT50_COMPARE_STATS 由「82期」自动变「83期」、统计更新时间 01:21:39→01:25:59、PENTA5_STATS_PANEL 校验时间 01:26:08→01:26:16，且无 penta5-live-notice 错误条；③ 另一组合成快照（A3/B3syn）验证新增名单的 RUNTIME_OBSERVABILITY 与 MODEL_USAGE 也会随刷新更新。④ 三个模板 render_block 离线复验 section 1/1 闭合。
- 中途现象：01:25:02 面板曾被一个 01:20:5x 启动的旧代码 run_stats 进程覆盖回 v23（该进程导入的旧模板），下一轮新进程（01:25:58 启动）已自动写回 v24；01:26:18/01:26:22 两次真实写入均为 v24 且区块平衡，说明无残留旧代码进程。
- 运维复验：`scripts/refresh_source_manifest.py --root /root/penta5-runner --backup-base /root/backups --if-drift` 写出 auto-baseline-20261104-012437（434 files）；ops_health overall WARN（manifest_drift 已恢复 OK，剩历史 warn：kill3_stability/data_tmp/coverage）。
- 备份：/root/backups/panel-refresh-fix-20261104-012013/（4 个 .py + 修复前 panel.html）。
- 提交：fix(panel) bb882e5；chore(ops) 本台账。
- 回滚：git revert 本台账 → bb882e5，或 cp /root/backups/panel-refresh-fix-20261104-012013/ 内 4 个 .py 回原路径后 py_compile，等一次 run_stats/面板重生成即恢复旧脚本（注意：旧模板会让自动刷新再次失效，仅作紧急回退）。
- 注意：已打开的旧页面 DOM 已是不平衡结构，无法自愈，需手动 F5 一次加载 v24 脚本；之后数字每 8s 自动跳动。

## 2026-11-05 03:15 四手连败改跨天累计（kill1/kill9/kill8/kill3 四卡统一）

- 依据：用户 m00469「今日连败，4手统计，主要连败四手都算一次，一直叠加，下次连败四手，就是俩次」；两轮问卷确认：范围保持「当日业务日 05:00 起」不动、输赢判定仍为全部 8 项全中、计数不重叠（4/8/12 各 1 次），把「四手连败」改为跨天累计并四张框架卡一起改。
- 口径：新增 `four_loss_cycles_total`——各框架按当前 RULE_VERSION 的全部已核验结算记录（跨天、不随 05:00 清零）按 target_period 排序，连续 4 期整组未全中记 1 次、8 期记 2 次、赢一次清零；旧版本（非当前 RULE_VERSION）记录不计入（与整组命中率「旧口径不计入」一致）。原「今日」字段 `four_loss_cycles` 保留向后兼容；`lib/panel_context.py` 的 _pick 白名单未改（不改变 AI 面板上下文）。
- 现值（e359450）：KILL_ONE **0**（v3 · n=287）、KILL_NINE **2**（v4 · n=180）、KILL_EIGHT **0**（v2 · n=34）、KILL_THREE **0**（v13 · n=61）。kill3 的 4 连败块只出现在历史版本里（v1–v13 共 8 块，如 20261101049/1130/1150/1183、20261102200），v13 自 20261104005 起未再出现，故为 0。
- 修法：四个 tail 模块抽 `_streak_stats(rows)` 并在 today_stats 返回 `four_loss_cycles_total`（跨天、按 RULE_VERSION 过滤）；四个 view 标签改「累计四手连败 <b>N</b> 次（自本版本上线起，跨天不重叠计数）」、脚注补口径说明；panel.html 用 `panel_io.update_panel` 手术式替换四卡标签与脚注（值 0/2/0/0），不重渲染整块。
- 验证：patch 前 old_string 唯一性校验；`py_compile` COMPILE_OK；四视图 `_stats_html(today_stats())` 输出累计 0/2/0/0 且 `_foot_html()` 含新说明；panel 中「累计四手连败」标签 4 处、旧「今日四手连败」0 处；git diff 8 files、92 insertions / 52 deletions。
- 冻结守卫：改动落在 v13 冻结受控文件（kill3_tail.py / kill_three_view.py），按 ops/kill3-stability-plan.md §8 走「用户指令例外」——重算两文件 sha256 并记入 ops/kill3-freeze.json 的 hash_updates（frozen_at / frozen_commit / RULE_VERSION 均未重置），stability-plan 追加记录。
- 备份：/root/backups/four-loss-cumulative-20261105-031022/（4 tail + 4 view + 改动前 panel.html）。
- 提交：feat(panel) e359450；chore(ops) 本台账 + freeze/plan 更新。
- 运维复验：`scripts/refresh_source_manifest.py --root /root/penta5-runner --backup-base /root/backups --if-drift` 重建 baseline 后 `scripts/ops_health_check.py`：kill3_freeze 恢复 OK、manifest_drift=OK；overall 仍 WARN 只剩历史/环境告警（deepseek_balance 3.33 CNY、coverage 24h、model_usage 余额仅够约 2h 等）。
- 回滚：`git revert 本台账 → e359450`，或 cp 备份内 4 个 tail + 4 个 view 回原路径、panel.html 用备份替换后 `py_compile`；下次面板重生成自动恢复旧文案。
- 注意：panel.html 是 skip-worktree 生成物（不入库），每期由 run_stats/各任务重写时使用新视图代码；已打开的旧页面需 F5 或等面板自身刷新才会显示新标签。

## 2026-11-05 03:26 KILL_THREE 位置排除依据改热码/重码/邻码（v14，禁冷码/长遗漏）

- 依据：用户 m00595「定位排除，别死定冷码。规则对没」；问卷选择 A「对齐 KILL_ONE」：位置候选只给近 50 期热码 top3 + 上期重码 + 邻码（±1），删掉冷码段，prompt 明确禁止冷码/长遗漏为依据；位置是否参与（放弃哪一位）仍完全交给模型，不加硬约束。
- 根因（取证）：v12 把热冷并列交给模型后，AI 仍死盯冷码与大遗漏——v12+v13 共 242 期、348 个 counted 杀码中冷码 top3 占 315（90.5%），热码 top3 仅 27（7.8%）；v13 的 128 个 counted 杀码里当期最冷码 98（76.6%）、热码 rank1 仅 2；distinct (pos,digit) 仅 22 种，b=8 连用 16/17 期（20261104286、20261105007 起）、q=8 连 9 期；64 期里 48 期固定放弃个位。对照 KILL_ONE v3（prompt 本就禁冷码）冷码 rank1 仅 14/291（≈7%）、位置分布 q122/b105/g33/s31。
- 修法（只改 lib/kill3_tail.py）：`RULE_VERSION = kill3-tail-v14-hot-repeat-neighbor-hold3-two-counted-three-avoid`；position_lines 删「；冷码 {_cold_line(cold)}」只留热码；候选区标题改为「近50期各位置热码（出现次数多、当前遗漏短优先；仅历史统计，不代表未来概率）」；task1 改为「定位依据从该位置的近50期热码 top3、上期重码、上期邻码（±1）中自主选择，不得以上期冷码 / 长遗漏码作为定位依据；」；task5 改「热码、重码、遗漏只是历史特征」；prompt audit version → kill3-tail-prompt-v14-…、position_basis → hot-repeat-neighbor（cold_ranking 仅留 audit 追溯、不进 prompt）。整组构成（2 counted 位置 + 1 display_only + 合尾 + 2 反向形态 + 3 组三数共现 = 8 项）与封存/结算逻辑未变。
- 基线：结构未变，用 10^4 开奖空间精确枚举复验（43 期样本）exp8=0.583307、exp5=0.621881，与已注册 8 项 0.5863 / 5 项 0.6191 一致，不重注册；`lib/hitrate_baseline.py` 与 `scripts/framework_hitrate_cumulative.py` 为 v14 各追加一行 `(8, 0.5863)`，旧 v13 行保留。
- 验证：patch 前 old_string 唯一性校验（8 条 count==1）→ 备份 → patch → `py_compile` COMPILE-OK → /tmp/test_kill3_v14.py 11 条断言（prompt 内「冷码」仅剩禁止句 1 次、无冷码候选、热码段 4 位、audit 新字段）+ normalize_response（合法 JSON 通过、非法单双形态拒绝）ALL-TESTS-OK。真实周期：ref 20261105041 / target 20261105042 已封存 v14、publication_state=published、published_at 2026-11-05T03:26:18.650912+08:00，positions q5/b2/g6（display_only g，counted q/b，dropped s），reason「……模型原话：定位取上期重码与热码，尾沿用稳定码，形态取反面。」；panel.html KILL_THREE 区块口径行已变为「口径 kill3-tail-v14-… · 自 ref 20261105041 起（本版 1 期）」，`kill3-tail-v13` 0 处。
- 冻结守卫：重算 lib/kill3_tail.py sha256（426d014f4590724274284fae87defa26d752f28abe57d636f6138c5a69400cdc → a96151f9c3b2be6b9c72ee3c3e9835d3e3056358d9d6a8668aa542f9050eebc4），重置 ops/kill3-freeze.json：rule_version=v14、frozen_at=2026-11-05T03:26:17+08:00、frozen_commit=fb5f327、freeze_ref_period=20261105041、hash_updates +1 条、policy 追加例外；check_kill3_freeze → status=ok（v14 进度自 20261105041 起算，0/100 期）。
- 备份：/root/ops/kill3-v14-20261105-032446/（lib/kill3_tail.py、lib/kill_three_view.py、lib/hitrate_baseline.py、scripts/framework_hitrate_cumulative.py、scripts/run_kill3_tail.py、ops/kill3-freeze.json）。
- 提交：feat(kill3) fb5f327；chore(ops) 本台账 + stability-plan/freeze 更新。
- 回滚：`git revert 本台账 → fb5f327`，或 cp 备份 6 个文件回原路径后 `py_compile`；同时把 ops/kill3-freeze.json 恢复为 v13 冻结戳（rule_version/frozen_at/freeze_ref_period/frozen_commit 原值、删掉本次 hash_updates 条目、删掉 policy 例外句），下一期起旧 prompt 生效。
- 注意：v13 封存只读保留，统计按 rule_version 分段；本版只改「依据候选与措辞」，模型仍可能偶尔选到冷码（无硬校验/重试），但 prompt 已不提供冷码候选且明确禁止——若新版本累计 n≥20 后冷码占比仍 >50%，再考虑加硬校验/重试。

## 2026-11-05 04:44 KILL_THREE 位置排除恢复热码+冷码并列 + 接入位置稳定化影子（v15，是否沿用由模型决定）

- 依据：用户 m00003「怎么这个框架开什么除什么，我是要结合冷码这期排除，。还有影子数据，由模型决定采取」；问卷三问确认：cold_basis=「热码+冷码并列、模型自选」、shadow_sources=「位置杀码稳定化影子」、shadow_audit=「记录」。
- 根因（取证）：v14 把候选收窄为「热码 top3 + 上期重码 + 上期邻码」后，14 期封存共 28 个计入杀码里 **18 个（64.3%）直接等于上期同位置开奖号（repeat）**、3 个（10.7%）为 ±1 邻码，仅 7 个属其他→即用户看到的「开什么除什么」（截图那期 ref 20261105054→055 的 q2/b8/s2 正是上期 2/8/2/9 的重码）；此前的 v12/v13 是反方向：348 个杀码里冷码 top3 占 90.5%（见上一条 v14 台账的取证）。
- 修法（lib/kill3_tail.py + scripts/run_kill3_tail.py + lib/position_kill_shadow.py + 两处基线表）：`RULE_VERSION → kill3-tail-v15-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid`；位置段 `position_lines.append(f'· {label}：热码 {_hot_line(hot)}；冷码 {_cold_line(cold)}')`、标题改「近50期各位置热码 / 冷码（…均为历史统计，不代表未来概率）」；任务 1 改「定位依据可从该位置的近50期热码 top3、冷码 top3（出现次数少/遗漏长）、上期重码、上期邻码（±1）以及【位置杀码稳定化影子】对照中自主选择，鼓励结合冷码」，任务 5 补冷码/影子命中率；新增 `SHADOW_POLICIES`/`_shadow_rows()`/`_shadow_replay()`/`position_hold_shadow()`/`position_basis_flags()`，prompt 在【任务】前插入【位置杀码稳定化影子】段（现行换码 vs hold2/hold3/hold5/hold-until-miss 命中率与换码次数 + 上期各位杀码，明确「是否沿用由你自主决定，系统不做后处理」）；seal 新增 `position_shadow`/`position_basis_used`/`hold_adopted`/`hold_policy='model-decided'`；`lib/position_kill_shadow.py` 恢复 kill3 采集（FRAMEWORKS 重新启用）、空态与脚注文案改 v15 口径。
- 影子回放（只读，735 期 20261030279→20261105056 / 1571 注）：现行 89.82%（换码 806）、hold2 89.37%（491）、hold3 89.69%（354）、hold5 88.99%（280）、hold-until-miss 89.75%（146）→ 稳定化不提高命中率（差异都在噪声内），只把换码从 806 压到 146；该数据进提示词供模型自主采纳，不做确定性后处理。
- 离线验证：5 文件 `py_compile` COMPILE-OK；`build_prompt` 6 条断言全 true（含「冷码」候选行、【位置杀码稳定化影子】段且位于【任务】前、无「不得以上期冷码」、「鼓励结合冷码」）；audit `version=kill3-tail-prompt-v15-…`、`position_basis=hot-cold-repeat-neighbor-hold-shadow`、`previous_positions` 四键、`position_shadow` 存在；`position_basis_flags` 自测（沿用上期码 → hot_top3/repeat/hold_previous；换新码 → cold_top3/neighbor）。
- 线上真实周期验证：ref 20261105057→20261105058 已封存 v15（published_at 2026-11-05T04:46:35），counted q/g、b 仅展示、弃 s，positions 8/2/4，`position_basis_used` = {q: cold_top3, b: cold_top3, g: cold_top3}、`hold_adopted=[]`（模型自选新码），position_shadow 736 期/1573 注，prompt_tokens 4840（v14 同段约 4371，影子段 +469）；该期已结算：实际 5/5/6/6，位置 q/g/b 全 hit、合尾 hit、两形态 hit、整组 hit=True。ref 058→059 当期（04:50 后）`position_basis_used` q/b=cold_top3+hold_previous、s=cold_top3+neighbor、`hold_adopted=['b','q']` —— 模型首次真正采纳 hold，证明「影子数据由模型决定采取」链路已通。
- 面板：KILL_THREE 卡口径行「口径 kill3-tail-v15-… · 自 ref 20261105057 起（本版 1 期） · 整组精确基线 58.63%（8 项）」；`position-kill-shadow` 区块由 cron `*/5` 重画（04:50:56）重新出表「AI三位置(kill3) 回放 735 期/1571 注 现行 89.82% / hold2·3·5·until-miss」，旧「全部转正、不再采集」文案 0 处。
- 基线：结构未变（8 项 = 2 计入位置 + 1 合尾 + 2 反向形态 + 3 组三数共现避开；2 注），`lib/hitrate_baseline.py` 与 `scripts/framework_hitrate_cumulative.py` 为 v15 各追加同值行 `(8, 0.5863)`，不重校准。
- 冻结守卫：受控 sha 更新（lib/kill3_tail.py a96151f9…→6eac93a3…、scripts/run_kill3_tail.py 0261afc2…→7c55f6ce…；kill_three_view.py/panel_context.py 未变），重置 rule_version=v15 / frozen_at=2026-11-05T04:44:42+08:00 / frozen_commit=2c34e59 / freeze_ref_period=20261105056，hash_updates +2、policy 追加；`ops_health_check.py` → kill3_freeze OK（进度 1/100 期）。
- 备份：/root/ops/kill3-v15-044214/（lib/kill3_tail.py、lib/kill_three_view.py、lib/position_kill_shadow.py、lib/hitrate_baseline.py、scripts/run_kill3_tail.py、scripts/framework_hitrate_cumulative.py、ops/kill3-freeze.json、data/position_kill_shadow.json 的改前副本）。
- 提交：feat(kill3) `2c34e59`；chore(ops) `c181b0b`（freeze 更新）+ 本台账。manifest baseline 重建：`scripts/refresh_source_manifest.py --root /root/penta5-runner --backup-base /root/backups --if-drift` → auto-baseline-20261105-044509（434 files），ops_health manifest_drift 恢复 OK。
- 回滚：`git revert 本台账 → c181b0b → 2c34e59`，或 cp 备份内 6 个源码 + ops/kill3-freeze.json 回原路径后 `py_compile`；freeze 需恢复 v14 值（rule_version v14、frozen_at 2026-11-05T03:26:17+08:00、frozen_commit fb5f327、freeze_ref_period 20261105041、sha a96151f9…/0261afc2…、删本次 2 条 hash_updates 与 policy 段）。
- 注意：v15 只「把候选还给模型」，没有硬约束；若新版本累计 n≥20 后 repeat 占比仍高（或冷码又回到 90%），再考虑加硬校验（如禁止选上期同位开奖号 / 强制至少 1 个位置取非重码）。统计按 rule_version 分段，旧 v14 封存只读保留。

### KILL_THREE v15 部署落地（2026-11-05 04:56，`bb3a947`）

- 复核脚本入库：`scripts/kill3_v15_review.py`（只读：位置依据标签 / hold 采纳 / 分项与整组命中；路径按仓库根推导，任意 cwd 可跑）。用法 `python3 scripts/kill3_v15_review.py --rule v15`；逐期明细会按 target_period 回填 seal 上的 `position_basis_used` / `hold_adopted`。
- 运行方式：kill3 由 cron 每 5 分钟新起解释器（`*/5` flock + `timeout 220 python3 scripts/run_kill3_tail.py`），改代码/新增脚本即生效，**不需要重启任何 pm2 服务**。
- 面板：`panel.html` 每期由守护进程重写；后端 `penta5_backend_runner.js` 的 `getPanelHtmlText()` 以 `fs.statSync(panel.html).mtimeMs` 为缓存键 → 文件更新后下一次请求即返回新版，同样无需重启（实测 panel.html mtime 04:56:36，nginx 04:56:39/44 的 `/panel` 请求 200）。
- 外网入口：nginx `:3001`（限流 + proxy-guard）→ 后端 127.0.0.1:3010；最近一周的 `/var/log/nginx/access.log` 里 `/panel?_panel_refresh=…` 持续 200。
- GitHub 远端：本机 `git push` 无凭证（`fatal: could not read Username for 'https://github.com'`），344 个提交只在服务器本地 + /root/backups 证据里；要同步远端需配置 token / deploy key 后再 push。
- 校验：`py_compile` OK；`ops_health_check.py` kill3_freeze/kill3_tail/manifest_drift/panel_html 全 OK；`refresh_source_manifest.py --if-drift` → `no-drift`。

### killer 日榜「今日已验证战绩」恢复（2026-11-05 05:55，`5adea54`）

- 现象：面板 KILLER 卡（DeepSeek-V4.1-Flash 智能深度排除）四位杀码/推荐合杀/排除形态等每项都显示「暂无今日已验证数据」。
- 根因：`58f63bc`（11-01）把 killer 策略回退 v2→v1（`lib/ai_model_context.py:27` `POLICY_STRATEGIES['killer']='conditional-transition-v1'`），但 `lib/framework_hit_refresh.py` 的 killer 日榜仍用 `strategy_features.KILLER_STRATEGY`（v2 常量）过滤 → 当日 v1 封存全被当旧策略排除，summary 归零、latest_settled_period='--'，前端 JS 回退为「暂无」。结算/核验本身正常（data/r1_stats.json 当日各指标已 11 期）。
- 修复：`lib/framework_hit_refresh.py` 引入 `from ai_model_context import POLICY_STRATEGIES`，framework/killer 两处过滤统一改用当前生效策略，避免下次策略切换再漂移。
- 证据：`PYTHONPATH=lib python3 -c` 调 `refresh_killer_stats()` → biz_date=2026-11-05、latest_settled_period=20261105070、metrics k1 90.9%(10/11)、k2 81.8%、k3 100%、k4 81.8%、he_all 100%、he_q3 81.8%、he_h3 72.7%、pat1 100%、pat2 90.9%；panel.html `#r1-killer-hit-data` payload 同步为新值（stats_error=false）。framework 卡重跑 `refresh_framework_stats()` 指标不变（策略仍 v2）。
- 回滚：`git revert 5adea54`；无需重启 pm2（run_stats.py 每期结算后重跑，浏览器刷新即见）。
- 校验：py_compile OK；`refresh_source_manifest.py --if-drift` → auto-baseline-20261105-055251（435 files），manifest_drift 恢复 OK；`ops_health_check.py` overall=warn（仅既有 coverage/model_usage/kill3_stability，与本次无关）。
### GitHub 远端推送打通（2026-11-05 06:06，`7069337`）

- 背景：此前推送缺口为「本机 git push 无凭证，348 个提交只在服务器本地 + /root/backups 证据里」（见上一节）。本次补齐。
- 方式：服务器生成专用 deploy key `/root/.ssh/penta5_deploy_ed25519`（ed25519，无口令），写入 `/root/.ssh/config` 的 `Host github-penta5`（HostName github.com / User git / IdentityFile /root/.ssh/penta5_deploy_ed25519 / IdentitiesOnly yes）；用 DSH 已有 GitHub 凭证（scopes repo）经 API `POST /repos/bingrixu-commits/penta5-runner/keys` 添加为可写 deploy key（id 168809371，verified=true）；remote 由 https 改为 `git@github-penta5:bingrixu-commits/penta5-runner.git`。
- 首次 push 被拒（fetch first）：远端 main=c400c602，与本地分歧 342/5；远端 5 个提交为 secret hygiene / accuracy daily board / API lock 格式。`git merge --no-commit --no-ff origin/main` 共 7 个冲突，全部「保留本地已上线实现」：penta5_backend_runner.js（本地已含 accuracy/hitrate/attack-txt/seal 登录态，远端为重复实现）、accuracy.html（保留本地 493 行「命中率看板·独立只读」）、.gitignore / .roo / .vscode 示例、api/package-lock.json（本地 api/package.json 为 mysql2 3.23.1）；仅 .env.example 合并远端新增 4 个 RDS_* 变量。合并前备份分支 `backup/pre-push-merge-20261105`（=6ba6fbf）。
- 验证：`node --check penta5_backend_runner.js` OK；合并树相对 HEAD 仅 `.env.example | 4 ++++`（线上运行代码逐字节不变）；push 输出 `c400c60..7069337 main -> main`；local HEAD = remote main = `70693374479b8a493fdd495fab4d7ec6afb50faa`，ahead 0，工作树干净。
- 回滚：远端如需还原，用 `backup/pre-push-merge-20261105` 重建后 force push；本地可 `git reset --hard backup/pre-push-merge-20261105`。
### 杀码框架卡补「滚动100期」口径（2026-11-05 06:29，`2381fa3`）

- 背景（用户 m00002「检查命中率低的情况」）：审计结论=面板数字精确复现、不是 bug 也不是模型退化。8 项合取随机上限仅 56.3%（10^4 枚举 56.34%），14 期整组 6/14=42.86%（p=0.274）、累计 v2 1684 期 918/1684=54.51%（−1.82pp，p=0.131）；逐项仅百位名义偏低 88.06% vs 90%（p=0.008，Bonferroni 0.05/8=0.00625 下不显著）；开奖公平性：四位置 χ²=5.28/8.67/8.91/13.09（df=9）、含重复数字 48.99%（iid 期望 49.60%）→ 面板基线文案本身正确。用户 m00184 选定 A 方案：保留严格整组口径，面板补「滚动窗口」避免单日小样本被误读。
- 改动：`lib/framework_hit_stats.py` 新增 `ROLLING_WINDOW = 100` 与 `_rolling_summary(entries, window)`（取最近 100 期同策略 verified、排除冲突期、按 target_period 排序），`make_summary()` 返回 `summary['rolling'] = {window_target, window, available, metrics, overall}`，日榜/账本逻辑零改动；`lib/framework_hit_view.py` 增加 rolling 载荷校验（逐项 total 一致、window==total、total<=window_target，不合规降级 `available=False`）与展示：notice 末行「滚动口径（最近100个已验证期）：整组8项全过 xx.xx%（h/t期）」、8 个 badge 改「label 本日 x.xx%（h/t期）｜滚动100期 y.yy%（h/t期）」、整组行改「整组8项全部通过：滚动100期：xx.xx%（h/t期） ｜ 本日 xx.xx%（h/t期） ｜ 当前连中/连败/最高…」；无 rolling 载荷时保持旧文案（向后兼容）。
- 实施踩坑（复现要点）：JS 里 `rollAvailable/rollingPrefix` 最初写在 badge 循环之后（var 提升但未赋值）→ badge 不出滚动后缀，已移到 `keys` 声明之后；notice 拼接滚动行若插在相邻字面量串中间会触发隐式拼接语法错误，改为独立 `rolling_line` 变量在 `</div>` 前以 `+ rolling_line` 追加。
- 离线验证：两文件 `py_compile` OK；抽出 SCRIPT 后 `node --check` OK；`/root/ops/fw-rolling-20261105/test_rolling.py` 断言 rolling available/window=100/total=100、payload JSON 可解析（rolling.overall 56/100）、notice 含「滚动口径（最近100个已验证期）」、去掉 rolling 后降级 available=False 且不出现滚动文案、`apply_stats_block` 对现 panel 连做两次幂等且 START/END 各 1。
- 渲染验证（不只 grep 字符串）：服务器 `google-chrome --headless=new --dump-dom` 打开现 panel.html → 8 badge 全部输出「本日 …｜滚动100期 …」（q 94/100、b 86/100、he_h3 89、he_q3 89、ds 91、dx 96、he2 99、he3 98），整组行输出「滚动100期：56.00%（56/100期） ｜ 本日 50.00%（9/18期） ｜ 当前连中：1手 …」；截图与卡片预览存 `/root/ops/fw-rolling-20261105/fw_card.png`、`fw_card_preview.html`（服务器无中文字形，数字/结构可读）。
- 线上真实周期：run_stats pid 578232 的 `framework_stats` 阶段 `reference=20261105077` status=success，panel-sync 日志 06:28:58「Statistics sync refresh success … trigger=new-draw=20261105077」；该轮写出的 panel.html 中 `R1_FRAMEWORK_STATS` 块 START/END 各 1、payload `rolling.available=true / window=100 / 56 期`、notice 行 56.00% → 线上管线（sync_state_to_panel→run_stats→framework_hit_refresh）已吃到新代码，非手写面板。
- 备份/回滚：`/root/backups/fw-rolling-20261105-062251/`（lib__framework_hit_stats.py、lib__framework_hit_view.py、panel.html 改前副本；改前 `cp -a` 拷回原位即可），或 `git revert 2381fa3` 后 `py_compile`。一次性影响：改动前已打开的浏览器标签会命中 panel_live_refresh 的 `script-version` 守卫，显示「页面版本已更新，请方便时手动刷新一次。」（预期行为），手动刷新一次即稳定为新版。
- 校验：`scripts/refresh_source_manifest.py --root /root/penta5-runner --if-drift` → `no-drift`（auto-baseline-20261105-062653）；`scripts/ops_health_check.py` → 仅既有 3 条 warn（coverage / model_usage / kill3_stability），manifest_drift / panel_html / job:framework 全 OK。
- 后续真实周期（自动推进证据）：20261105078 结算后面板于 06:30:57 重写，卡片整组「滚动100期 57.00%（57/100期）｜本日 52.63%（10/19期）」，逐项 94/86/89/90/91/96/99/98，notice 行同步 57.00% —— 说明滚动窗口随每期结算自动前移，无需人工干预。
- 数据口径（供后续对照）：v2 策略最近 100 期整组 56/100=56.00%，逐项 94/86/89/89/91/96/99/98；最近 50 期 26/50=52.00%；全量 n=1685 时 918/1685=54.48%。滚动口径只换展示，不改封存/结算/命中率账本（`data/r1_framework_hit_stats.json` 未被滚动改造写回）。

### 命中率稳定性/断崖护栏上线（2026-11-05 07:00，\`aee3882\`）

- 背景（用户 m00074「部署落地，如何做到波动不大，别断崖式下滑」）：取证结论——单日 n≈22、1σ≈±10pp，日百分比不能作结论；口径切换会造成当日统计清零（kill3_tail 5 天 11 版、v14 只活 14 期）；真正可判的信号是「滚动窗口显著性 + 覆盖率 + 口径 churn」。
- 新增 \`scripts/hitrate_stability.py\`（cron \`13,28,43,58\`，flock + timeout 90）→ \`data/hitrate_stability.json\`，状态 \`data/hitrate_stability_state.json\` 记录各框架现行 rule 的 since/switch_at/n_at_switch。判定：24h 覆盖率<80% 且尾部仍漏≥3 期 → 断崖 alert；滚动100期 z≤−2.58 且 300 期 z≤−1.0 → alert；100 期 z≤−2.0 → watch；观测到口径切换且存活<24h 或<30 期 → alert，<72h 或<100 期 → watch；异常一律 exit 0（不阻塞后续任务）。
- 面板：新增 \`lib/hitrate_stability_view.py\`（section \`#hitrate-stability\`，8 列：滚动100/300、累计、精确基线、24h 覆盖、口径存活、判定），在 \`/root/run_stats.py\` 两处 update_panel 链注入（该文件不在 git 仓库内，已备份）。
- 实时刷新：\`lib/panel_live_refresh.py\` 的 names 白名单加 \`HITRATE_STABILITY\`，版本串 quiet-panel-v24→v25（不改版本号浏览器不会重载新 JS）。
- 体检：\`scripts/ops_health_check.py\` 新增 \`check_hitrate_stability\`（报告缺失/过期>45min → WARN；summary fail→FAIL、warn→WARN），随 ops_health 每 10min 巡检。
- 验证：首跑 status=warn、watch=1（selected_six 滚动100期 46.0% z=−2.28）；面板注入幂等、START/END 各 1 个、7 行齐全（killer 100 期 35.0%、framework 57.0%、he3 96.0%、kill3 68.0% n=25、kill1 74.0%、kill9 79.0%，coverage_24h 全 31.2%）；\`crontab_tool.sh diff\` → matches（36 jobs）；ops_health manifest_drift / crontab_drift 恢复 OK（baseline \`auto-baseline-20261105-070118\`）。
- 回滚：\`git revert aee3882\` + \`bash scripts/crontab_tool.sh install\`（crontab 回到不含该 job 的基线）；\`/root/run_stats.py\` 与 \`lib/panel_live_refresh.py\` 用 \`/root/backups/hitrate-stability-20261105-065755/\` 覆盖回去，面板下期重生成即恢复。
- 07:13:01 cron 自触发验证：`data/hitrate_stability_cron.log` 新增 `status=warn alerts=0 watch=2 as_of=20261105085`，逐框架行（killer w100=33.0% z=−2.28 / selected_six 46.0% z=−2.28）与 JSON 同步；面板块随下轮重写刷新到 `as_of=20261105085`（START/END 各 1 个，面板整体 data-as-of=20261105086）。

### 命中率归因 / 覆盖率语义 / 通用冻结守卫三件套上线（2026-11-05 07:50，`be58b99`+`940b3e4`+`63aab0f`+`1d98eca`）

- 背景（用户 m00226「帮我全部弄好，然后部署完善」）：前一轮只做到「命中率稳定性护栏」，仍有三件事未闭环——A) killer 与 selected_six 近 100 期命中率偏低到底是哪一项；B) 面板覆盖率 32% 是历史缺口还是仍在漏期；C) kill3 单框架冻结守卫需推广到 7 个框架。
- A 归因（`be58b99`）：新增 `scripts/hitrate_attribution.py`（近 100 期已核验封存，逐项命中率 vs「每期封存参数在 10^4 开奖空间精确枚举」的基线均值；整组用 `lib/hitrate_baseline.py` 登记值；z 为二项近似；单跑 12.6s）与 `lib/hitrate_attribution_view.py`（自包含区块 `#hitrate-attribution`，列=项目/n/命中率/精确基线/Δpp/z）。结论：killer 整组 34.0% vs 41.6%、framework 58.0% vs 55.6%、six 47.0% vs 57.3%，唯一 |z|≥1.96 且 n≥50 的关注项是 six「单双反向排除」−4.75pp（z=−1.96, n=100）；killer 为多项 2~4pp 小幅负偏累积（个位 −4.00pp 最大，z=−1.33），无单项崩塌。
- B 覆盖率语义（`940b3e4`）：32.3% 的成因是 11-04 23:10 前历史缺口仍在 24h 窗口内（window 288 期、recent 24 期漏 0），不是现在还在漏。`scripts/hitrate_stability.py` 增 `coverage_recent24`/`coverage_note`，`lib/hitrate_stability_view.py` 覆盖列拆「24h窗口覆盖」「近24期覆盖」两列并带 title 注释；`ops_health_check.py:check_coverage` 原有 recent 逻辑已能区分「历史缺口已恢复」。
- C 通用冻结守卫（`63aab0f`）：`scripts/freeze_guard.py`（7 框架 killer/framework/selected_six/he3_refine/kill3_tail/kill1_tail/kill9_tail，files+shared+rule_source），`ops/freeze-guards.json` 记录 files sha、rule_version、frozen_at、freeze_ref_period、72h/100 期窗口与 hash_updates。**期数进度不读几百 MB 封存账本**（selected_six_seals.json 430M/he3 97M/r1_framework 92M），改用 `data/stats_draw_history.json`（800 期、73K），status 1.4s。kill3 旧 `ops/kill3-freeze.json` 已迁移（ref=20261105056，init 时 34 期）。
- 接线（`1d98eca`）：`ops_health_check.py` 的 `check_kill3_freeze` 从 CHECK_FUNCS/FALLBACK_NAMES 替换为通用 `check_freeze_guards`（files sha 变→WARN、rule_version 变→WARN、shared 变只记录、到期只记录；7 框架当前 0 变更）；同时接入 `check_hitrate_attribution`（>40min 过期→WARN）。旧 `check_kill3_freeze` 函数保留但不再注册（历史参照）。`/root/run_stats.py`（**不在 git 仓库内**）两处 update_panel 链加 `install_quiet_refresh(apply_hitrate_attribution_block(apply_hitrate_stability_block(apply_control(strip_retired_panels(page)))))`，备份 `run_stats.py.bak-20261105-0729xx`；`lib/panel_live_refresh.py` 白名单加 HITRATE_ATTRIBUTION、版本串 `quiet-panel-v25`→`v26-live-sections`；cron 新增 `6,16,26,36,46,56` 跑 attribution（flock+timeout 120s，日志 data/hitrate_attribution_cron.log）。
- 证据：crontab 基线 install 后 `diff ops/crontab.txt <(crontab -l)` = 0 且 attribution 行命中 1；手动 run_stats 后 panel.html `#hitrate-attribution` data-as-of=20261105091、区块 7282 字节、正文含 killer 9 项/framework/six 表与「整组 34.0%（基线 41.6%）」，`#hitrate-stability` 出现「近24期覆盖」列，版本串 v26；`ops_health_check.py --json` 31 项、overall=warn，仅 4 个已知 warn（kill3_stability 24h 内 2 次 v15 切换、evidence_backup 26.6h 超过 26h 阈值、coverage 历史缺口已标注、hitrate_stability 观察 killer/six），freeze_guards/hitrate_attribution 均 ok；`refresh_source_manifest.py --if-drift` = no-drift（auto-baseline-20261105-074623）。
- 冻结守卫使用方式：本轮之后受控文件（如 `lib/kill3_tail.py`）再改会 WARN，必须 `python3 scripts/freeze_guard.py update --framework <k> --file <rel> --reason "..."` 留痕，或 `init --renew` 重开窗口；`freeze_guard.py status` 查看各框架 期数/小时/变更。
- 回滚：`git revert 1d98eca 63aab0f 940b3e4 be58b99`；从 ops/crontab.txt 删 attribution 行后 `bash scripts/crontab_tool.sh install`；用 `/root/run_stats.py.bak-20261105-0729xx` 覆盖并 `pm2 restart`（如需）；`rm ops/freeze-guards.json` 即回到 kill3 单框架时代（但 ops_health 的 freeze_guards 会 WARN「无框架记录」）。

### 2026-11-05 08:03 补丁：selected_six 口径版本解析（`d24d02f`）

- 症状：`freeze_guard.py status` 里 selected_six 的 `rule=None`，因为 `lib/selected_six_contract.py:24` 是间接赋值 `REVISION = V7_REVISION`，旧解析器只匹配单引号字面量，导致该框架口径变更不会被 `rule_changed` 捕获（files sha 仍会 WARN）。
- 修法：`_rule_version()` 在字面量未命中时按 `NAME = OTHER_CONST` 跳跃最多 3 层再取字面量；现在解析为 `selected-six-independent-v7-single-tail`。`ops/freeze-guards.json` 中 selected_six 的 `rule_version` 由 null 补为同值（基线 adopt，不产生 rule_changed 告警）。
- 备份：`scripts/freeze_guard.py.bak-20261105-0801`；复验：`freeze_guard.py status` 7 框架 changed 全空、ops_health freeze_guards ok。

### evidence_backup OOM 根治：RDS 流式导出 + 大文件校验降峰值（2026-11-05 08:55，\`7f57324\`）

- 症状/取证：\`latest-verified.json\` 停在 11-04T05:13（ops_health evidence_backup WARN 26.6h）。crontab 第 19 行 05:09 确实触发（syslog 05:09:01 CRON）；dmesg 05:09:21 全局 OOM \`Killed process 557250 (python3) total-vm:7887464kB anon-rss:7065024kB task_memcg=/system.slice/cron.service\`；同链 refresh 已先打印 no-drift ⇒ 被杀的是 \`run_evidence_backup.py\`。
- 根因（两层）：① \`_validate_json_bytes\` 对每个证据文件整棵 \`json.loads\` 做凭据检查，data 证据 1.32GB/6703 个文件，最大 \`data/selected_six_seals.json\` 453,102,857B（对象图可达数 GB）；② 真正峰值在 RDS 导出：旧 \`export_draws\` 用 \`subprocess.run(stdout=PIPE)\` 缓存 821MiB、\`splitlines()\` 再展开、\`_canonical(draws)\` 生成 str+bytes 双份、draws 对象约 0.9GiB —— \`/usr/bin/time -v\` 实测 Max RSS 7,840,388KB。叠加 05:00 业务日批次并发（run_selected_six 常态 1.8GB）与 \`vm.swappiness=0\`（2GB swap 完全未用）→ 全局 OOM。
- 改动（\`lib/evidence_backup.py\`，备份 \`lib/evidence_backup.py.bak-20261105-0810\`）：新增 \`_CanonicalWriter\`（容器递归 text/raw，单行/标量交给 C \`json.JSONEncoder\`，1MiB 缓冲）替代 \`iterencode\` 纯 Python 慢路径（821MiB 实测仅 1.2MiB/s）；新增 \`_CanonicalRows\` 行 spool 与 \`_LazyBlob(path,size,sha256)\`（tar 阶段按文件流式 \`addfile\`）；新增 \`_export_rds_streaming\`：\`Popen\` 逐行解析 NODE_EXPORT，行级 \`_check_credential_fields\` 与 official_draws 校验（period 槽位/四球/open_time 分钟窗口）后 canonical 片段写 spool，complete 后按表顺序拼 canonical 文件，单遍完成、峰值只与单行大小相关；超时 60→180s（node 导出 model_predictions 737MB 约 29s，实测整段 ~99s）；\`create_bundle\` 改 \`exporter=_export_rds_streaming\`，RDS 临时文件 \`_check_secrets_file\` 分块扫描后转 \`_LazyBlob\` 入 tar；\`_validate_json_bytes\` >64MiB 改结构哨兵 + 敏感键正则（不物化对象图），\`.jsonl\` 逐行解析；crontab 第 19 行 refresh 与 backup 之间 \`&&\` 改 \`;\`（refresh 失败不再阻塞备份，失败原因由日志/ops_health 暴露）。
- 等价证明：冻结 NODE_EXPORT 真实输出 \`/tmp/rds_raw.jsonl\`（822,909,709B/25,427 行），fake Popen 回放——旧路径 size 821,719,074 sha \`c94119ebaa49911d\`、新路径同 size/sha（60.8s，EQUIVALENT True）；assembly 对嵌套/unicode/浮点文档与 \`_canonical\` 逐字节一致。负例：含 \`sk-…\` 的文件抛 secret-content-refused、坏 .jsonl 仍抛 invalid-evidence-json。
- 全量验证：refresh 生成 baseline 后后台跑 \`run_evidence_backup.py\` → \`status=verified\`、archive \`evidence-20261105-084604-ff712789.tar.gz\` 327,077,122B、sha256 \`a909546606452422295aceff9209793b7ab82d4b46a2d0489b1c5648748e97b2\`、source_count 442、rds row_count 5000（last_period 20261105105），耗时 3m12s、Max RSS 5,600,932KB（含 verify 阶段 json.loads 821MiB canonical 峰值）、exit 0；\`latest-verified.json\` 已更新。
- 系统层（同一根因）：\`vm.swappiness\` 0→10 持久化（\`/etc/sysctl.conf\` 与 \`/etc/sysctl.d/99-sysctl.conf\`，备份 \`.bak-20261105\`），新增 2GB \`/swapfile-extra\`（\`/etc/fstab\` 持久化）；swap 总 4GB。目的：内存压力先换页，避免直接触发全局 OOM kill。
- 回滚：\`git revert 7f57324\`；crontab 用 \`/tmp/crontab.backup.20261105_085017\` 恢复（\`crontab /tmp/crontab.backup.20261105_085017\`）；系统 \`cp /etc/sysctl.conf.bak-20261105 /etc/sysctl.conf && cp /etc/sysctl.d/99-sysctl.conf.bak-20261105 /etc/sysctl.d/99-sysctl.conf && sysctl --system\`，\`swapoff /swapfile-extra\` 并从 fstab 删行。

### six 全史逐项判定上线（2026-11-05 09:15，\`7a7c099\`）

- 背景（用户 m00531「照你想法来」三项之①）：近 100 期归因里 six「单双反向排除」−4.75pp（z=−1.96, n=100）是唯一关注项，当时约定「满 200 期再判」。② killer 个位杀已单独取证：全史 n=2549（20261019003→20261105106）k1 89.80%、k2 89.02%、k3 89.49%、k4 88.78% vs 90%（−1.22pp z=−2.05 p=0.040），k4 换码子集 −1.76pp z=−2.26（多重比较下不硬）、四项合计 −0.73pp p≈0.014，无单项崩塌、与 hold3 无关（hold3 只作用于 he_* 合尾），不改线上规则、继续观察。
- 新增 \`scripts/hitrate_verdicts.py\`（cron \`34 * * * *\`，flock + timeout 300s，日志 \`data/hitrate_verdicts_cron.log\`）→ \`data/hitrate_verdicts.json\`：读 \`data/selected_six_hit_stats.json\` 全量 verified（首跑 1,123 条），对 13 个 six 项按 \`six_baseline\` 精确基线算全史 n/命中率/基线/Δpp/z/p、近 100/200/300/400/800 期窗口、分 revision 明细。
- 判定规则（预登记）：n≥200 才判；family-wise α=0.05/可判项数（首跑 0.05/13=0.003846，Bonferroni）；p<α 且 Δpp≤−3 才记 \`confirmed-deficit\`，否则 \`not-confirmed\`，n<200 \`pending\`。另设观察项：近 300 期 z≤−2.58（双侧 p<0.01）且全史 Δpp<0 记 \`watch\`——watch 不等于确认亏损。单跑 4.4s、Max RSS 1.15GB。
- 首跑结果：13/13 项可判、0 项确认亏损；唯一 watch=「单双反向排除」n=882 92.06% vs 93.05%（−0.98pp, p=0.232），但近 100/200/300 期分别为 −6.75/−4.75/−4.08pp（近 300 期 z=−2.92, p=0.0035）——全史被更早 v7 前段与 v4 段拉回基线，属近段偏离而非全史持续亏损；其余项全史均不显著（最大 百十个合尾杀 −2.43pp p=0.132、千位杀码 +1.07pp）。
- 面板：\`lib/hitrate_attribution_view.py\` 在 \`#hitrate-attribution\` 区块 reasons 之后加一行「全史判定」（CSS \`.ha-verdict\` 三态 ok/watch/alert），文案含「无确认亏损，仅近期偏离 1 项 · 可判 13/13 项（n≥200 才判 · Bonferroni α=0.0038）· 单双反向排除 近 300 期 -4.08pp（z=-2.92, p=0.0035），全史 -0.98pp」+ 生成时间/as_of；判定报告缺失时整行不渲染，不影响主链。
- 体检：\`scripts/ops_health_check.py\` 新增 \`check_hitrate_verdicts\`（CHECK_FUNCS / FALLBACK_NAMES 注册）：报告缺失或 age>150min → WARN、\`confirmed\` 非空 → WARN、watch 只写 detail 不告警。\`/etc/logrotate.d/penta5\` 补 \`hitrate_attribution_cron.log\` 与 \`hitrate_verdicts_cron.log\`（备份 \`/etc/logrotate.d/penta5.bak-20261105\`，\`logrotate -d\` 已读到两个新路径）。
- 验证：三文件 \`py_compile\` OK；手动跑脚本 → JSON 含 summary/items/windows/by_revision、as_of=20261105108；\`render_block()\` 输出含 verdict 行；面板真实重生成（09:07:03）后 \`panel.html\` 出现 \`class="ha-verdict ha-watch"\` 与「全史判定」正文（run_stats 链已吃到 view 改动，非手写面板）；\`ops_health_check.py --json\` 新增 \`hitrate_verdicts ok\`（judged=13 / pending=0 / as_of=20261105108 / 近期偏离 单双反向排除），crontab install 后 \`diff ops/crontab.txt <(crontab -l)\` = 0。
- 回滚：\`git revert 7a7c099\`；从 \`ops/crontab.txt\` 删 verdicts 行后 \`bash scripts/crontab_tool.sh install\`；\`/etc/logrotate.d/penta5.bak-20261105\` 覆盖回去；面板下期重写即恢复（无独立数据依赖）。

### 生成型页面不再计入证据基线：hitrate.html 噪声消除（2026-11-05 09:38，`68e4899`）

- 症状：09:34 verdicts cron 自触发复核时 `ops_health` 报 `manifest_drift fail`（no baseline manifest matches live sources），而 09:08:18 刚刷新过 baseline（no-drift）。
- 取证：对比 baseline `auto-baseline-20261105-090818` 的 442 行与实时 mtime，唯一变化是 `hitrate.html`（09:22:17，16,894B）；该页由 `scripts/framework_hitrate_cumulative.py:1011` 每 15 分钟调用 `lib/hitrate_page_view.write_page()` 重写（标题带 as_of 期号），且与 `panel.html` 一样被 `git update-index --skip-worktree` 标记，工作树变化不进 git status。`scripts/refresh_source_manifest.py` 的 `VOLATILE_GENERATED` 原先只含 `panel.html`，导致该页每 15 分钟必然制造 drift。
- 修法：`VOLATILE_GENERATED = {'panel.html', 'hitrate.html'}`，并在 `_canonical()` 中对「旧 manifest 路径 ∪ 目录扫描」的并集按根目录文件名再过滤一次（避免被历史 baseline 重新带回）；docstring 的排除说明同步更新。备份 `scripts/refresh_source_manifest.py.bak-20261105-0938`。
- 验证：`py_compile` OK；`refresh_source_manifest.py --if-drift` → `baseline-written` `auto-baseline-20261105-093809`，441 files（较 442 少 `hitrate.html`），manifest 内 hitrate.html/panel.html 计数 0；`ops_health_check.py --json` overall=warn，manifest_drift 恢复 ok，仅剩既有 kill3_stability / coverage 两条 warn；refresh 脚本写入后自带 `_source_entries` 自检通过（evidence_backup 侧可继续用新 baseline）。
- 回滚：`git revert 68e4899`；或 `cp scripts/refresh_source_manifest.py.bak-20261105-0938 scripts/refresh_source_manifest.py` 后重跑 `--if-drift` 重建 baseline。

### kill1 v4：双位置杀码 + 一合尾（3 项，移除形态）（2026-11-05 10:18，`cb32582`）

- 需求（m00002）：用户说「调整这个框架，定位排除俩个，加一个合尾」。确认后口径为 2 个不同位置各杀 1 码（两项均计入）+ 1 个合尾杀 1 码，形态完全移除，整组 3 项全中；位置稳定化 v4 起两位置各自独立 hold3，合尾 hold3 保留；旧 v1–v3 封存归档保留、不计入新统计。
- 取证：旧规则 519 条封存逐配置 10^4 回放均值 0.75406532（已注册 0.7538；v1 117 条 0.75365812、v2 33 条 0.75498485、v3 369 条 0.75411220），解析值 0.9×0.9×(67/72)=0.75375，自证通过；新规则 30000 条配置（6 位置对×100 杀码×5 合尾×10 尾杀）10^4 全枚举，每条恰好 7290 次命中、均值 0.72900000 → 登记 (3,0.729)。枚举脚本 `/root/ops/kill1v4-101000/enumerate_baseline.py`。
- 修法：`lib/kill1_tail.py` RULE_VERSION='kill1-tail-v4-two-position-one-tail'、COUNTED_POSITIONS=2、normalize_response 收 positions 数组恰 2 个不同位置、build_prompt v4 契约（positions 数组、3 项全中、双位置独立 hold3 说明）、settle_pending 无 form 记录 form_hit=None（group_hit=两位置全中 and 合尾命中；旧封存仍按 form 判定）、create_seal 不再写 form_type/form、apply_position_policy 两位置独立 hold3；`lib/kill_one_view.py` 重写（标题「AI 双位置杀码 + 一合尾建议」、基线 0.729、3 项 2 位置+合尾、位置稳定化 items、legacy v3 form 卡兼容、攻击语 aria「AI 双位置+一合尾攻击语」）；`scripts/run_kill1_tail.py` 打印无形态；挂接点：panel_context `_kill1_self`/`_ai_self_frame` 去 form_type/form 加 position_policy，model_call_rec/tail_hold_max_shadow/attack_editor_view/ops_health_check/tail_hold_review 文案，hitrate_baseline 与 framework_hitrate_cumulative 追加 v4 基线行与 LABELS。kill9 池不改：`build_form_pool` 对无 form 封存自动跳过，来源退化为 kill3(2)+kill8(2)，已注册基线 0.70822528 不变。
- 验证：py_compile 全绿；10:15:35 cron 首期 v4 发布（ref 20261105123 → target 20261105124，published 10:16:09.973，positions 百位 7 / 个位 2、合尾 tail_qbs 杀 1 沿用上期第 2 期、无 form 字段）；同轮结算 v3 旧封存 target 20261105123 verified=True group_hit=True（actual 1683，tail 命中 5、form 双单双单 vs 单双双单 命中），证明旧证据口径未变；panel.html KILL_ONE 块 as-of 20261105123、攻击语存在（10:16:38 用 panel_io 重发布修正首版 `%` 格式化 bug）；ops_health kill1_tail OK；freeze_guard init --renew + kill3 panel_context 受控变更登记后 freeze status kill1/kill3 changed=-（kill9 changed 属另一窗口在途）。
- 回滚：`git revert b24153c 80c97c6 461707f cb32582`；或 `cp /root/ops/kill1v4-101000/<相对路径> <相对路径>` 逐文件恢复后 `python3 scripts/freeze_guard.py init --framework kill1_tail --renew` 重建冻结基线；下期 cron 即回 v3 口径。

### kill9 v5：新增三定位排除 + 软结合形态，整组 9 项上线（2026-11-05 10:2x，`63f7e64` `714b37b` `a35d27b` `80de052`）

- 用户需求：调整 kill9 框架，增加「定位排除 3 个位置」并「结合形态」；钉死口径 = 从千/百/十/个中选 3 个不同位置、每位排除 1 个 0-9 数字（命中 = 该位实际开奖号 ≠ 排除码，单项基线 90%），定位排除为「软结合」——提示词提供 4 个形态排除项在各位置的单双/大小方向 + 各位热码/冷码/上期重码/邻码，模型自主选位选码，reason 必须说明如何结合，本地只校验结构不做硬绑定；9 项全部计入整组（全中才算中）。
- 口径与基线：新 rule_version = `kill9-tail-v5-pool-three-all-form-three-kill-he2he3`；整组 9 项 = 3 池选形态（基线 93.75/93.75/93.75）+ 1 全同形态（原样排除，75.00）+ 3 定位排除（各 90.00）+ 二数合（98.08）+ 三数合（93.75）；精确期望 E_v5 = 旧 E_v4 0.70822528 × 0.9³ = 0.51629622912，登记 (9, 0.51629623)，面板显示「整组精确基线 51.63%（9 项）」。v4 及更早封存/战绩原样保留、不计入本版统计。
- 实现：`lib/kill9_tail.py`（RULE_VERSION、POSITION_KEYS/索引、position_evidence（各位置 hot/cold/previous/repeat/neighbor）、position_basis='soft-form-direction-hot-cold-repeat-neighbor'、prompt 定位段与 positions 契约、normalize_response 校验 3 个不同位置各 1 码、create_seal 写 positions/position_basis/position_evidence、settle_pending 新增 is_v5 分支写 positions{q,b,s,g:{kill,actual,hit}} 且 v4 旧封存仍走无 positions 分支、today_stats 新增 position_total/position_hits/item_total=9、build_attack 追加「千位除X百位除Y十位除Z」）；`scripts/run_kill9_tail.py` 日志打印定位；`lib/hitrate_baseline.py` + `scripts/framework_hitrate_cumulative.py` 登记 v5 基线/LABELS/cum fallback；`lib/kill_nine_view.py` 9 卡布局（定位排除 1-3 + 该位今日命中率 note）、新标题、新 σ 公式、v4 无 positions 时降级 6 卡；`ops/freeze-guards.json` 重置。
- 验证（真实周期）：① 离线 10 项 TEST（含 5 个非法 positions 全拒、v4 回归、结算/统计契约）ALL-OFFLINE-TESTS-OK；② v5 首期封存 ref=20261105124 → target=20261105125，published 10:20:59.697，positions {q:'7',b:'2',s:'4'}，reason「千位偶数与小数各占2个…排除邻码7；百位单数占多数…排除冷码2；十位小数占多数…排除冷码4」，position_evidence 四位齐全；③ 同轮 v4 旧封存 target=20261105124 结算 verified=True、group_hit=False、无 positions 键（旧证据口径未变）；④ v5 首期结算 target=20261105125（actual 2/0/4/1，drawn 10:25:12，settled 10:25:47）positions {q:7→2 hit, b:2→0 hit, s:4→4 miss}，其余 7 项中仅全同形态「小小小小」未命中（开奖大小正好全小），整组 9 项 8 中 → group_hit=False，与「全中才中」口径一致；⑤ panel.html 10:26:52 重生成：9 卡（3 定位排除卡 + 该位今日命中率 100/100/0%）、标题含「三定位排除」、整组 0.00%（0/1 期）、各单项基线正确、底部 51.63%（9 项）、本版 2 期、连败 1 期。
- 体检：`refresh_source_manifest.py --root /root/penta5-runner --if-drift` → no-drift（auto-baseline-20261105-102814）；`ops_health_check.py --json`（10:28）overall=fail，fail 项 hitrate_stability（kill1_tail 现行口径仅存活 0 期，属另一窗口 kill1 v4 上线过渡态），warn 项 kill3_stability/coverage（既有）、freeze_guards（framework 的 lib/auto_r1_framework.py 与 shared lib/panel_context.py 由其他窗口在途改动，kill9 不在其列）、deepseek_balance=7.51 CNY、model_usage（余额约 1.8h）；kill9 自身无告警；`freeze_guard.py init --framework kill9_tail --renew` → ref=20261105124、periods=0/100、changed=-、rule=v5（该次 init 同时刷新了 shared panel_context.py 的登记哈希，因另一窗口在途改动被一并记录）。
- 备份/回滚：改前备份 `/root/ops/kill9-v5-100610/`（8 文件 + sha256）、应用前备份 `/root/ops/kill9-v5-apply-101759/`（两 Python 文件 + payload + patcher）；回滚 `git revert 80de052 a35d27b 714b37b 63f7e64` 后 `python3 scripts/freeze_guard.py init --framework kill9_tail --renew`，下期 cron 即回 v4 口径（旧封存保留）。

### kill9 v5 基线独立复算取证（2026-11-05 10:31，read-only）

- 交付：`/root/ops/kill9-v5-100610/baseline_kill9_v5.py`（29,468B，sha256 `584d6da73ae19ff8ad6ec86160c625184868f2e1f86b7daaa5b40cca6b627b95`）+ `baseline_kill9_v5_receipt.json`（10,314B，sha256 `4ce531597eb8765b4df3ed993b1fbb2d47b708b2defcb195a7df84e507d21495`）；python3.10.12 + numpy 2.2.6，仓库零改动。
- 与登记值对照：E_draw_all_he=2161303/2500000=0.8645212（10^4 开奖全枚举）✅；定位因子 4×10^7 配置枚举命中 29,160,000 → 729/1000 ✅；E_v4(登记)=371314016703/524288000000=0.7082252821 ✅；E_v5(登记)=E_v4×729/1000=0.516296230653（8dp 0.51629623）✅ 与 `hitrate_baseline` / `framework_hitrate_cumulative` / 视图 fallback 一致；台账自证 v3=0.7538576289、v2=0.7123435181 均吻合。
- 发现历史差（非本轮引入）：池选期望登记 E_pool3=859005/1048576=0.81921101，而按注释池模型修正后精确枚举为 859425/1048576=0.81961155（Δ+0.0004005）。根因=v3 枚举脚本 `f_pool` 的 U=2 补位项笔误（210/256 少记 6）；v4 脚本已修正但登记沿用 v3 旧值；模型口径参考 E_v4=0.70857156、E_v5=0.51654867。
- 决定：不改已注册历史基线，v5 保持与 v4 同一登记口径（0.51629623，面板 51.63%）；模型口径 0.51654867 仅作参考记录（差异 ≤0.03pp，不影响 n=1 判读），留待后续 revision 统一校准。receipt 唯一 warning（`TOTAL_FORMS` 表达式常量未匹配）属校验误报，不影响数值。

### framework 形态反向约束：千/百位单双+大小均取杀码反面（2026-11-05 10:36，`5462c0c`）

- 需求（m00002/m00036）：用户贴「🎯 DeepSeek 杀码框架」面板卡，指出第 5/6 项「单双反向形态 / 大小反向形态」与上面第 1/2 项定位排除码不构成反向（「形态反向修正」「反向形态，出码不对上面」）。定口径：球一（千位 q_kill1）、球二（百位 b_kill1）为被杀位置，两项形态在 0/1 位的 4 个字符都取杀码形态的反面（排除双写单、排除大写小）；十/个位（index 2/3）仍自由，由联合优化器在 4×4 内取联合命中最大。与 kill3_tail.py:420-422 / kill8_tail.py reverse_constraints() / kill1 面板「被杀位置取杀码形态反面」的既有惯例一致。
- 取证：① prompt（lib/auto_r1_framework.py p.124-170）只把 pattern_ds/pattern_dx 定义成「按千百十个顺序排除的完整四位形态」，无反向约束；git 全史（6fd13af..0fe6900）也从未有过形态反向规则 → 口径缺失不是回归。② 形态实际由「AI 校验之后」的 lib/joint_form_optimizer.optimize_framework_response 在 10000 空间暴力枚举 16×16 替换（2026-10-31 joint_form 转正，data/joint_form_auto_promote.json enabled=true），目标只有联合命中最大，因此系统性选到与杀码同向的形态。③ 2831 条封存回放：千/百两位都取反的仅 4.1%（20261101 后 570 条仅 0.2%）。④ 强制四位反向的 10^4 回放：整组命中 55.14%（1561/2831）vs 封存 55.53%（1572/2831），−0.39pp；逐期精确期望均值 0.5474 vs 0.5605（−1.3pp）。⑤ 反事实：ref 20261105126（kills 4/2）四位锁最优 ('单单双单','大大大大') expected 0.5447，而无约束最优 ('双双单单','小小大大') 0.5733。⑥ 首版实现（958a012）只锁 ds[0]/dx[1] 两位，实际封存 单双单单/小大大大 仍与杀码同向（百位单双 双 vs 杀 2 双、千位大小 小 vs 杀 4 小），已 soft-reset 重做为四位锁。
- 修法（`5462c0c`，2 files +80/−8；改前备份 /root/ops/joint_form_optimizer.py.bak-20261105-103406）：lib/joint_form_optimizer.py 新增 PARITY_OPPOSITE/SIZE_OPPOSITE 与 digit_parity/digit_size；reverse_lock(response) 返回 {'pattern_ds':[opp(千杀码奇偶),opp(百杀码奇偶)],'pattern_dx':[opp(千杀码大小),opp(百杀码大小)]}（JSON 可序列化）；_best_framework(lock=) 把 ds/dx 的 0/1 位四个字符全部钉死；apply_reverse_form 为不依赖掩码的确定性兜底（同样改四位）；optimize_framework_response(..., reverse_form=True) 回传 reverse_lock；killer 路径未动。lib/auto_r1_framework.py：joint 路径写 metadata reverse_form{applied,mode,lock,before,after}；优化器未启用/异常时走 apply_reverse_form 兜底（日志 REVERSE_FORM_FALLBACK / REVERSE_FORM_FAILED）；卡头注改「形态：本地精确联合优化 + 反向约束（千/百位单双/大小均取杀码反面，AI 定位杀/合尾杀/保留集不变）」；封存 metadata 增 reverse_form。
- 验证：① py_compile 两文件通过。② 离线跑最近 4 条封存：123 kills 4/2 → lock 单单/大大 → 单单单双/大大大小 exp 0.5471；124 kills 9/7 → 双双/小小 → 双双双双/小小小小 exp 0.5455；125 kills 4/2 → 单单/大大 → 单单双单/大大大大 exp 0.5447；四项字符断言与 expected_opt≤pure 全过；fallback ('单双单单','小大大大') → ('单单单单','大大大大')。③ 真实周期（10:36:00 发布）：ref 20261105127 → target 20261105128，kills 4/2、tails 2/0、forms 单单双单/大大大大，model_metadata.reverse_form={'applied':true,'mode':'joint_form','lock':{'pattern_ds':['单','单'],'pattern_dx':['大','大']},'before':['双双单单','小大大大'],'after':['单单双单','大大大大']}，joint_form_optimized=true；千位杀 4(双/小)→形态千位 单、大 ✓，百位杀 2(双/小)→百位 单、大 ✓。panel.html R1_FRAMEWORK block 与攻击语「千123567890百134567890十1234567890个1234567890四定千百十合134567890百十个合123456789除单单双单除大大大大二数合0248三数合012348各」逐项对上（keep 集与 8 项一一一致），卡头注已是新文案。
- 影响 / 已知取舍：八项联合精确期望由 0.5605 降到约 0.5474，是「形态必须反向」口径的固有代价；shadow 的 opt 列仍是纯优化期望（约 0.573–0.577），framework_hitrate_cumulative 的 drift expected 会略乐观；joint_form_promotion_gate 的 live delta 会转负，但 not-met 不会自动关闭优化器（只有 manual_disable 才停）。本轮不改 hitrate_baseline（framework 8 项 0.5559 沿用旧口径，与 10-31 joint_form 转正时同样未改的先例一致）。仅 ref 20261105126（10:31:06 发布）属 2 位锁过渡期，旧封存不改。
- 冻结守卫：`python3 scripts/freeze_guard.py update --framework framework --file lib/auto_r1_framework.py --reason "反向形态约束（千/百位单双+大小四位锁，5462c0c）"` → 06b294257804 -> 761f9cea59f0，status framework changed=-。
- 回滚：`git revert 5462c0c`，下期 cron 即回到纯联合优化形态；如需核对某期是否新口径，看该期封存 model_metadata.reverse_form 是否存在及其 lock 结构（list=2 位锁旧期，dict=四位锁）。

### kill9 v5 上线后首小时实盘补记（2026-11-05 10:39）

- v5 已连续结算 3 期（target 20261105125/126/127，均由 cron 自动封存+结算）：125 期整组未中（定位 2/3；十位排除 4 vs 实际 4）、126 期整组命中（actual 3/7/7/4，定位 3/3 + 其余 6 项全中）、127 期整组未中（actual 1/8/2/2，定位千位 4 vs1 中、百位 8 vs8 失、个位 2 vs2 失——该期模型选的是 千/百/个 三位，证明「4 位中任选 3 位」契约生效）。累计 1/3=33.33%（整组登记基线 51.63%，n=3 小样本仅作波动参考），各定位单项 100.00%/66.67%/50.00% 已按位入面板。
- 累计报告已自动收口：`data/framework_hitrate_cumulative.json`（10:37:20）新增 kill9_tail 行 rule=kill9-tail-v5-…、n=3、hits=1、expected=0.516296，rows 6→7，as_of=20261105127；`hitrate.html` 同步重写；cron 日志 `rolling kill9_tail 24h coverage=46.88% plan_hit_rate=36.46%`。

### kill9 v5 攻击语修正：定位排除改用位置保留码（2026-11-05 10:47，显示层）

- 问题：v5 上线后攻击语把 3 个定位排除项渲染成「千位除4百位除7十位除9」，不符合本仓库/平台统一攻击语语法。本仓库 kill1/kill2/kill3/八项/四定框架的攻击语对位置项一律用「千/百/十/个 位置保留码」表达：被杀位置为去掉该数字的 9 码、未选位置保留 10 码 `1234567890`（见本台账 2026-11-01 kill2 首期攻击语例）。
- 修法：`lib/kill9_tail.py::build_attack` 新增 `ATTACK_ORDER='1234567890'` 与 `ATTACK_POSITION_LABELS={'q':'千','b':'百','s':'十','g':'个'}`；封存含 `positions` 时先输出 4 段位置保留码，再照旧输出「除<形态>」×4 + 二数合 + 三数合 + 各；v1–v4 无 `positions` 的旧封存攻击语保持原「除…」格式不变。属显示层修改，不改封存字段/结算口径/RULE_VERSION/命中统计。
- 校验：py_compile OK；模块测试 /tmp/test_kill9_attack.py 13/0——seal 20261105128 逐字节等于期望「千123567890百123456890十123456780个1234567890除双单双双除双单单双除小大大小除大大大大二数合0248三数合01247各」；126/127/129/130 位置段与尾部逐项一致；v1/v4 旧格式不变；非法定位码抛 ValueError。
- 证据：修复前 `lib/kill9_tail.py` sha256 b598b3e9fb019918676355ee72b98d9747d315d9191f3698d82102f9a53cf054（备份 /root/ops/kill9-attack-fix-104725/ 含 freeze-guards.json），修复后 6ec1779362bdcb89fa8c0977948755800b12298b758b5426a88a34e6999f2343；freeze_guard 受控变更已登记。后续等 kill9 cron 下一期真实封存后，从 `panel.html` 的 `textarea#k9-attack` 复核攻击语与 `build_attack(seals[-1])` 一致。

### 运行观测/模型用量读取修复 + 封存账本容量预警（2026-11-05 11:24，`4ae28b8` `1283e12` `7354a94`）

由用户「全部检查各模型运行数据读取是否正确」触发：只读审计确认 4 个读取/展示问题与 2 个相邻区块缺陷，修复限于读取与展示层，不改预测语义，无需重启（cron 自动加载）。

**改动**
- `4ae28b8` 运行观测（lib/runtime_observability.py、lib/runtime_observability_view.py、lib/panel_live_refresh.py）：
  - selected6 封存账本 469MB 超过 128MiB 读取上限，导致每分钟 invalid-source、面板 0%/缺期 77（实际约 98.7%）。collect 对 selected6 先 stat，超限时改走 `scripts/build_six_seal_index.py --if-stale` 生成的紧凑索引（复用 lib/strategy_comparison.py 同款路径）；回退失败仍按 invalid-source 显示「数据源不可读」。
  - parse_log 完成标记补 `FRAMEWORK_PUBLISHED`/`KILLER_PUBLISHED` 行，framework「超时/策略拒绝」不再整列显示「—」（今日策略拒绝 3 可见）。
  - he3 发布后 180s 内面板 retained 块尚未翻新时不再把 current 降级为 pending（PANEL_GRACE_SECONDS=180），消除每期 flap。
  - selected6 标签由硬编码「Pro 七项」改为按 PENTA5_MODEL_SIX 动态档位（现为 Flash 七项）；view 删除已退役 pro4 行，active_count 改取 round.total_count → 摘要显示「完成 3/4」而非「完成—/5」；panel_live_refresh 删除 pro4 残留 selector。
- `1283e12` 模型用量/调用推荐：调用推荐补 4 个任务的 usage key 映射（selected_six→six、he3_refine→he3、kill3_tail→kill3、kill1_tail→kill1，六任务 usage 全部非空）；KPI「成功响应」→「HTTP 200 响应」并补口径脚注；新增错误分布行（今日/近7日：服务商/限流/无效响应/欠费/超时）；burn=null 且充值后显示「充值后重新起算」；provider 错误标签「服务商错误」。
- `7354a94` ops_health：新增 ledger_size 检查（he3_refine_seals ≥110MiB WARN、≥120MiB FAIL；selected_six 超 128MiB 仅记录；其它账本 ≥200MiB note）。

**验证**
- py_compile 7 个改动文件全部 OK；`scripts/ops_health_check.py --json`：ledger_size=ok、manifest_drift=ok（`refresh_source_manifest.py --if-drift` → `/root/backups/auto-baseline-20261105-113232/verification-manifest.json`，441 files）。
- 观测 cron 新代码 11:28 起生效，单轮 16.3s / 39.7s（并发干扰）/ 21.4s / 11.0s / 27.7s / 21.6s，均低于 wrapper 50s 上限。
- 11:35:46、11:37:46 两个连续真实周期：errors=[]；selected6 由 unknown→current，数字恢复（今日 80/81 发布、98.75%、missing 1 = target 20261105104 真实缺期）；framework 策略拒绝 3 可见；摘要「完成 3/4 · 异常 0」；面板 4 行无 pro4。
- he3 宽限证据：11:32:46 status=current、panel_matches=False、published_at=11:31:46（60s<180s）不再降级 pending；11:37:46 同样 current/已发布等待面板重建。
- 模型运行块数字经原始日志复算一致；model_call_rec.compute 六任务 usage_7d 非空（killer1050/framework1070/six1138/he3 916/kill3 718/kill1 691）。

**备份 / 回滚**
- 改动前备份：`/root/ops/fix-20261105-112457/backups/`（7 文件 + SHA256SUMS）。
- 回滚：`git revert 7354a94 1283e12 4ae28b8`，或按 backups 覆盖后 py_compile；无需重启（cron 自动加载）。

**风险 / 遗留**
- ops_health overall 仍 fail：仅既有 hitrate_stability（kill1_tail 新口径存活 1.0h/12 期，另一窗口 kill1 v4 过渡）+ kill3_stability/coverage warn，非本轮引入。
- selected6 主账本 469MB 仍在，观测每分钟依赖约 80MB 紧凑索引；索引重建（每期一次、--if-stale）会让个别观测轮次多 10~30s，可能被 flock 跳过该分钟（实测刷新间隔 1-2 分钟，仍小于 view 150s「旧」阈值）。若后续出现连续超时，可把 wrapper timeout 50→90，或把索引解析改为增量。
- he3_refine_seals 101.2MiB，距 110MiB 预警线约 9MiB；selected_six_hit_stats 232.9MiB 仍按既有归档策略处理。

### kill3 本版累计命中率展示 + 基线带告警（2026-11-05 11:47，`6a9b15a` `0d45d27` `7d891e8`）

由用户提问（m00002「该框架命中率，有没有办法维持住」）触发的只读取证结论：面板上的 69% 是当前 v15 的 8 项结构与 82 期小样本的上沿；按 10^4 开奖空间逐期精确枚举，v15 样本基线 58.31%、注册基线 58.63%，长期期望应按 58.63% 管理，口径不变的前提下没有可持续的增益杠杆。用户随后回复 m00050「排除项不动」⇒ 不改排除项/选码/尾码/8 项结构/封存/结算/AI prompt，也不升 RULE_VERSION，只做只读展示与告警。

**改动**
- `6a9b15a` `lib/kill_three_view.py`：新增「本版累计整组命中率」行，读 `data/framework_hitrate_cumulative.json` 的 kill3_tail 行（row.rule 必须等于 kill3_tail.RULE_VERSION，不随 05:00 清零），显示命中率/期数/95%CI/规则常量精确基线/偏差/二项 p/近 24 期/截至 ref；原整组行改为「今日整组命中率」；脚注补累计口径说明；样式 `.k3-cum` 4 条。属只读展示，选码/封存/结算/AI prompt 未动。
- `0d45d27` `scripts/hitrate_stability.py`：新增 kill3_tail 累计 vs 规则常量基线的单侧 2σ 带（`BASELINE_BAND_KEYS=('kill3_tail',)`、n≥50 起判、z≤-2.58 alert / z≤-2.0 watch），产物 revision 升 `hitrate-stability-v2`、`out_rows` 增 `baseline_band`；其余 6 个框架不启用，避免无关框架改变全局 health。

**验证**
- A/B：新版 render 7435 chars vs 旧 6801；归一化（剥掉 k3-cum div/CSS/今日前缀/脚注新句）后与旧版逐字节相同，`ab_identical_after_normalize=true`；START/END 各 1、k3-cum 1；面板文本含 69.51%/57/82/58.63%/+10.88pp/p=0.045/ref 20261105139 全部断言通过。
- 首版踩坑：format string 里字面 % 未转义（`95%CI %.1f`）→ `ValueError: unsupported format character 'C'` 被 try/except 吞掉、行静默为空；改为 `95%%CI %.1f` 修复，并加固为断言渲染文本与 JSON 数字一致（`/root/ops/test_kill3_cumstats.py`）。
- stability 实跑 exit 0：`kill3_tail.baseline_band={n:82,hits:57,expected:0.5863,mean_hits:48.08,sd:4.46,z:2.0,level:ok}`，其余 6 行 null；summary 与改前一致（既有 kill1_tail 版本 churn alert + selected_six watch，非本次引入）。
- 线上实证：真实周期 ref=20261105141 → target=20261105142（11:46:26 发布）后，panel.html 的 KILL_THREE 块 data-as-of=20261105141、块长 7420 字节，渲染「本版累计整组命中率 69.51%（57/82 期，95%CI 58.9–78.4%）· 精确基线 58.63% · 偏差 +10.88pp · p=0.045 · 近 24 期 79.2%（19/24）· 截至 ref 20261105139」；今日行为 68.29%（56/82）。
- 冻结登记：`7d891e8` 更新 `ops/kill3-freeze.json` 的 `lib/kill_three_view.py` sha256 `fde296d8…`→`f2dbb4fd…` 并追加 hash_updates（frozen_at 不重置、RULE_VERSION 未变）；`scripts/freeze_guard.py update` 同步受控登记该文件 fde296d8…→f2dbb4fd…（commit 6a9b15a）。

**备份 / 回滚**
- 改动前备份 `/root/ops/kill3-cumstats-20261105-114346`（panel.html、data 快照、ROLLBACK.txt）；回滚 `git revert 6a9b15a 0d45d27 7d891e8`，或按 ROLLBACK.txt 覆盖回读。

**风险 / 遗留**
- 累计行与 2σ 带在 n<200 前只作参考（累计 JSON verdict 仍为「样本不足」）；kill3 长期期望仍按 58.63% 管理，69% 不应当作可维持水平。
- ops_health overall 仍 fail 属既有 hitrate_stability（kill1_tail 新口径存活期 + selected_six watch）与 kill3_stability/coverage warn，非本次引入；本次未新增 alert（kill3 band 当前 ok）。

### selected_six（七项独立框架）退役（2026-11-05 12:37，`988b221` `e963f71` `30c48c3` `468784d` `a285b83`）

- 需求（m00002 用户截图「删除改框架彻底清理」）：退役面板卡「DeepSeek Pro · 七项独立框架」= `#six-independent-card`（revision `selected-six-independent-v7-single-tail`，7 项：双位置杀码 + 合尾 + 二数合保留 4 码 + 三数合保留 5 码 + 单双反向形态 + 大小反向形态），停止每 5 分钟预测、删除代码本体、面板区块、派生统计与 data 封存；dormant 的「Pro 自选七项」`selected_framework`（仅代码，无 cron/无封存/无面板块）不受影响。
- 取证：crontab 第 6 行 `*/5 * * * * flock -n /var/lock/penta5-six.lock … run_selected_six.py >> data/selected_six_cron.log`；面板三块标记 SELECTED_SIX_INDEPENDENT / _STATS / _ATTACK 各 1 次；攻击语版本串 v16；数据 selected_six_seals 473MB / hit_stats 245MB / seal_index 80MB；全仓 six 引用 588 处（含无关语义：`inject_peishu.py` 六肖六码、he3 六码、ai_call_control「six public fields」）。
- 代码本体：删 `lib/selected_six_{contract,model,request_options,stats,view}.py`、`lib/six_tail_hold.py`、`lib/model_signi_view.py`、`scripts/{run_selected_six,build_six_seal_index,hitrate_verdicts,model_significance,strategy_settled_report}.py`（12 文件）；`lib/selected_strategy_validation.py` 收敛为 seven-only（保住 `selected_request_options.py`/`selected_framework_model.py` 的导入）。
- 运行层：crontab 删每 5 分钟 run_selected_six 任务、seal 归档 `--target selected_six_seals`、两条 six 注释、hitrate_verdicts（每小时）与 strategy_settled_report（每 6h）两个纯六项派生任务；`/etc/logrotate.d/penta5` 删 selected_six_cron.log / hitrate_verdicts_cron.log / strategy_settled_cron.log 三段（`logrotate -d` 通过）。crontab 44→38 非空行，`crontab_tool.sh install` 后 live 与 `ops/crontab.txt` diff=0。
- 共享链路：panel_context（frameworks.selected_six、model_tiers.six、PENTA5_MODEL_SIX）、runtime_observability（selected6、SIX_LEDGER/SIX_SEAL_INDEX/_six_ledger_fallback、selected-six 错误码）、model_call_rec/model_usage（six 日志源）、evidence_backup/seal_archive（six 账本注册）、strategy_comparison（six 模型/策略/six_seven cohort/封存索引回退）、strategy_features（LEGACY_SIX/SIX/SINGLE_TAIL_SIX 三策略）、framework_coverage、tail_hold_max_shadow、tail_stability_shadow、tail_hold_review、hitrate_{baseline,attribution,stability,guard,verdicts,real_hitrate_report}、ops_health_check、freeze_guard 全部去六项。
- 面板层：panel.html 持 `panel.html.lock` flock + os.replace 剥 SELECTED_SIX_INDEPENDENT / _STATS / _ATTACK / MODEL_SIGNI 四块（415,722→387,068B，之后由 run_stats 重建）；`lib/panel_live_refresh.py` 去三个刷新 token + `selected6` selector/attribute/target + 死 CSS + 时间可读性注入；`lib/attack_editor_view.py` 版本串 `all-attack-editor-v16-add-kill9`→`v17-retire-selected-six`、删七项独立攻击语编辑器；`lib/hitrate_attribution_view.py` 删「全史判定」verdict strip；panel_delivery_timing/panel_io/ai_runtime_jobs/publication_validation_cache/operational_availability 去六项挂接。
- 派生统计：hitrate_baseline 7→6 行、framework_hitrate_cumulative 7→6 行、tail_hold_review 5→4 框架、tail shadow 5→4、model_usage logs 7→6、hitrate_attribution frameworks `['framework','killer']`、hitrate_stability rows=6；model_significance / hitrate_verdicts / strategy_settled_report 三个纯六项产物退役（脚本+面板块+数据）。
- A/B 证据：固定 fixture REF=20261105148/TGT=20261105149，`$B/panel_context.py` 与新版 `build()+prompt_text()` 对比——prompt sha `6d77b883daca8d72`（6,192B）→ `a66a08e1f3647f45`（5,935B），快照结构 diff 恰为 `/frameworks/selected_six` 与 `/model_tiers/six` 两项删除，其余字段逐字节相同；据此完成 6 个 guard 的 panel_context 受控哈希更新与 he3_refine_stats 注释更新（`468784d`）。
- 验证：改动文件 `py_compile`/`compileall` 全绿；run_stats 手动重建（12:30:50 rc=0）后 panel.html 四块标记计数 0、攻击语 v17 2 次、刷新名单无 SELECTED_SIX/MODEL_SIGNI、as-of=20261105150、hitrate_baseline rows=6；面板内联 JS 全部 node --check 通过（8 个 `type=application/json` 数据块除外）；cron 重算后 attribution/cumulative/stability 均 rows=6 且无六项；`freeze_guard.py status` 6 guard changed=-；故障修复：`scripts/hitrate_attribution.py` 的 `_model_tier()` 在删 six_baseline 时被误删导致 12:26 NameError，已从 `git show HEAD` 取回原文并手动重跑通过（见 `a285b83` 前状态）。
- 文案残留：`f95282d` 清理 `lib/tail_hold_max_shadow.py` 合尾影子区块的「七项为单尾」说明为「AI双位置/AI三位置为单尾」，并重生成该区块（panel.html「七项」仅剩 dormant seven 框架攻击语编辑器定义 1 处，属未退役框架历史命名）。
- 归档：`/root/archive/retired-20261105-selected_six/`：`selected_six-data.tar.gz` 83,350,731B sha256 `9f7b73182f7955106fb9a376efb7930303b43f3468d18a0659407cb15e0c9925`；`selected_six-logs.tar.gz` 248,643B sha256 `fc0152bc8296eb6e246b6937569fdf20ff8487cf6be57964584c773c9847495d`；`selected_six-cold-shards.tar.gz` sha256 `b09e93ff8728ca3822efaffa81486cb0e46ef0812e6f8992f7e72349438bf6a0`；`sha256.txt`。另补 `README.md`/`rollback_commit.txt`。
- 备份：`/root/ops/selected-six-retire-20261105-120919/`（baseline、before_*.json、crontab、panel_context.py、run_stats.py、panel.before.html、.env.bak/.env.before-edit、logrotate-penta5*.bak、freeze-guards.json.bak、kill3-freeze.json.bak、tail_*_shadow.py.before-edit）；live crontab 备份 `/tmp/crontab.backup.20261105_121022`。
- 回滚：`git revert a285b83 468784d 30c48c3 e963f71 988b221`；`.env` 用 `$B/.env.before-edit` 覆盖；logrotate 用 `$B/logrotate-penta5.bak` 覆盖；`/root/run_stats.py` 用 `$B/run_stats.py` 覆盖；data 解归档三个 tar 回 `data/`；无需重启（cron 下轮自动加载）。panel.html/hitrate.html 为 skip-worktree 生成物，回滚后下轮 run_stats 自动重建。
- 非 git 改动：`.env` 删 `PENTA5_MODEL_SIX=deepseek-flash`、`PENTA5_SIX_PEI_SIX_WINDOW=30`；`/etc/logrotate.d/penta5` 删 3 个日志段；`/root/run_stats.py`（不在仓库）删 selected_six import/stage 与 model_signi 链。
- 风险/遗留：未改任何剩余框架口径；ops_health 仍有既有 kill1_tail stability FAIL（另一窗口 kill1 v4 在途）与 coverage/data_tmp warn，非本次引入；dormant selected_framework 的历史命名（`selected-six-*` 错误码、`selected-six-stats-v1` 等）按「不属于被退役框架」保留，未改名。


### kill3 展示回退（2026-11-05 17:19，`c72ff75`）

- 用户指令 m00187「反回前面样子，现在的不稳」：撤回 `6a9b15a` 的只读累计展示行，KILL_THREE 卡片恢复改动前外观（单行「整组命中率（8 项全中：…）」），选码/尾码/8 项结构/封存/结算/AI prompt 未变，未升 RULE_VERSION。
- 回退方式 `git revert --no-edit 6a9b15a`（仅 `lib/kill_three_view.py`，sha 回到 `fde296d8…`）；保留 `0d45d27` 的累计 vs 基线单侧 2σ 带（仅告警、不显示）与 `c7f7034` 台账。
- 冻结登记：`ops/kill3-freeze.json` 与 `ops/freeze-guards.json` 的 `kill3_tail.files['lib/kill_three_view.py']` 由 `f2dbb4fd…` 回退为 `fde296d8…`，追加 hash_updates（frozen_at 不重置、RULE_VERSION 未变）。
- 备份 `/root/ops/kill3-revert-20261105-171827`（view、两个 freeze json、README、panel.html、ROLLBACK.txt）。
- 回退前现况（17:14）：累计 n=148 命中 95 = 64.19%（p=0.170，z=1.37 baseline_band ok），今日 63.95%（94/147）；长期期望仍按 58.63% 管理，本次未改口径。

### kill3 相关改动全部恢复（2026-11-05 17:33，本 commit）

- 用户指令 m00259「恢复早上高命中的时候，你动过的，恢复回去」：把本窗口今天为 kill3 加的改动全部撤回。`0d45d27` 的「累计 vs 规则常量基线单侧 2σ 带」已反向应用（`git apply -R`），`scripts/hitrate_stability.py` 回到 `hitrate-stability-v1`，`data/hitrate_stability.json` 重新生成后不再含 `baseline_band`；`6a9b15a` 的面板累计展示已于 `c72ff75` 回退。
- 现在 kill3 的运行时（选码/尾码/8 项结构/封存/结算/AI prompt）与改动前完全一致；`ops/kill3-freeze.json` view hash 已是原值 `fde296d8…`，frozen_at/RULE_VERSION 未变。
- 说明：命中的数字不能靠回退恢复——本窗口的改动只有展示与监控，不参与预测；中午 12:31–12:38 另一窗口 selected_six 退役改了共享 `lib/panel_context.py`（kill3 prompt 上下文少 selected_six/six 两段），那是本窗口之外、且数据已删无法原样恢复的改动，未动。
- 回滚本 commit：`git revert <this>` 即可重新启用基线带告警。


### kill9_tail（AI 四形态排除 + 三定位排除 + 二数合 + 三数合）退役（2026-11-05 18:01，`353a886` `6505158` `f860879` `54f8376` `ad4bcb7` `f59169a`）

- 需求（m00002 用户截图 + 「清理整个框架」）：彻底退役面板卡「AI 四形态排除（池选三 + 全同一）+ 三定位排除 + 二数合 + 三数合建议」= 框架 `kill9_tail`（RULE_VERSION `kill9-tail-v5-pool-three-all-form-three-kill-he2he3`，9 项：3 池选形态 + 1 全同形态 + 千/百/个三定位排除 + 二数合保留 4 码 + 三数合保留 5 码），停止每 5 分钟预测、删除代码本体、面板区块、攻击语编辑器入口、派生统计、共享 prompt 上下文、冻结守卫与 data 封存。与 selected_six 退役同族（截图 + 一句指令），按彻底退役执行，未再弹确认问卷。
- 取证：crontab 第 24 行 `*/5 … run_kill9_tail.py >> data/kill9_tail_cron.log`；`/etc/logrotate.d/penta5` 的 kill9 日志段；panel.html `KILL_NINE_START/END` 各 1（全文 3 处含刷新名单）、卡片 data-as-of 20261105214；`lib/panel_live_refresh.py` 名单、`lib/attack_editor_view.py` 的 `k9-attack`；data/kill9_tail seals.json 7.95MB/391 条 + settlements 530KB + cron log 392K；派生层 7 文件登记；freeze 6 守卫含 kill9_tail；`/root/run_stats.py` import + 两条 lambda 链。改前全部备份并留 prompt-baseline。
- 运行层（`353a886`）：`ops/crontab.txt` 删每 5 分钟 `run_kill9_tail.py` 任务（38 行，`bash scripts/crontab_tool.sh install` 后 live diff=0，旧 live 快照 `/tmp/crontab.backup.20261105_175251`）；`/etc/logrotate.d/penta5` 删 `data/kill9_tail_cron.log` 段（非 git 文件，备份 `$BK/files/logrotate-penta5`），`logrotate -d` 校验通过。
- 面板层（`6505158`）：panel.html（skip-worktree 生成物）持 `panel.html.lock` + os.replace 剥整块 `KILL_NINE_START/END`（385,137→375,216B），刷新名单去 KILL_NINE，内联版本 v26→`quiet-panel-v27-retire-kill9`；`lib/panel_live_refresh.py` 同步；`lib/attack_editor_view.py` 删 k9 攻击语编辑器、版本串 v17→`all-attack-editor-v18-retire-kill9`；`lib/model_retirement.py` 新增 `KILL_NINE_MARKERS=('KILL_NINE',)` 并纳入 strip 循环（防旧进程写回）。改后真实周期 run_stats 重建（18:00:29 374,820B），KILL_NINE=0、KILL_ONE/KILL_THREE all=3、KILL_EIGHT all=2。
- 代码本体（`f860879`）：`git rm lib/kill9_tail.py lib/kill_nine_view.py scripts/run_kill9_tail.py`；`/root/run_stats.py`（仓库外 untracked）删 `from kill_nine_view import apply_kill_nine_block` 与两条 lambda 链调用，33,606B、kill9 引用 0、py_compile + import 通过。坑：只删调用 token 会留下未匹配 `)`（`SyntaxError: unmatched ')'`），须括号配对删除；曾从备份恢复重做。
- 共享链路（`ad4bcb7`）：`lib/panel_context.py` 删 `_kill9_self()`、`_ai_self_frame` 的 kill9_tail 分支、`_ai_self_frames()` 的 kill9 条目、`build()` 的 `kill9_stats/kill9_previous` 参数与 kill9_self 注入、drop 列表 `kill9_self`、`_model_tiers` 的 `PENTA5_MODEL_KILL9`；新 sha256 `83a2208855b37cfc4a048a7d775c1aba432854016435fc9366ffdfd6dd93a44a`（旧 82d9174a…）。A/B（固定 ref=20261105213/target=20261105214，`/tmp/ab_capture2.py`，前后 JSON 在 `$BK/meta/`）：kill8/kill3/kill1 三变体 len+sha+snapshot 逐字节相同（kill8 5,017B/243365e4…、kill3 5,161B/3fd7b29d…、kill1 5,179B/a0d9f176…）；相对更早 ab-before 的缩短来自派生层删 kill9 行（约 628B）+ 真实周期字段漂移，非本次共享改动。
- 派生统计（`54f8376`）：`lib/hitrate_baseline.py` 删 kill9 五行基线/LABELS/`_kill9_rows`/extend（rows 7→6）；`scripts/framework_hitrate_cumulative.py` 删五行/LABELS/WINDOWS/`cum_kill9`/注册元组（写 hitrate.html，rows 6→5）；`lib/evidence_backup.py` JSON_DIRS、`lib/model_usage.py` SOURCE_LOGS、`scripts/ops_health_check.py` PROVIDER_402_LOGS/`check_kill9`/CHECK_FUNCS/FALLBACK_NAMES 与 `HITRATE_CUMULATIVE_MIN_ROWS` 6→5。重跑 hitrate_baseline / framework_hitrate_cumulative / run_model_usage 均成功；当前 rows=5（killer/framework/he3_refine/kill3_tail/kill1_tail；kill8 行因另一窗口 v4 切换瞬时无匹配记录，后续周期恢复，非本次引入）。
- 冻结守卫（`f59169a`）：`ops/freeze-guards.json` 删 kill9_tail 守卫；framework/he3_refine/kill1_tail/killer 的 `shared['lib/panel_context.py']` 与 kill3_tail 的 `files[...]` 更新新 sha 并各追加 hash_updates（commit ad4bcb7 + A/B 理由）；`ops/kill3-freeze.json`（legacy）files 新 sha + 追加一条 hash_updates；`scripts/freeze_guard.py` FRAMEWORKS 删 kill9_tail 块。现状 5 框架 `changed=-`、ops_health `check_freeze_guards`=ok；历史 hash_updates 的 reason 文字中的 kill9 属历史记录保留。
- 验证：改动 py_compile/compileall + node --check 全部通过；crontab live 与 ops/crontab.txt diff=0 且 live 无 kill9；logrotate/data 无 kill9 残留；ops_health 18:00 全量跑 overall=fail 但非 ok 项与改前完全一致（hitrate_stability=kill1_tail 口径清零风险、kill3_stability/data_tmp/coverage 既有），无任何 kill9 检查项；下一个真实周期面板重建后 KILL_NINE 仍 0、刷新版本 v27。
- 归档：`/root/archive/retired-20261105-kill9/`：`kill9_tail-data.tar.gz` 584,272B sha256 `d538ccfd8917416e91120579b1d54ed9cba95091c160b6607f2edb7bae0b833a`（data/kill9_tail/seals.json 391 records 7.95MB + settlements.json 530KB + data/kill9_tail_cron.log 392K + 遗锁）与 `kill9_tail-logs-rotated.tar.gz` 29,344B sha256 `a3ba46af57b31f95e25efd3673dabcc76df7daf796e8a3b3e0047a03b7024071`（cron.log.1/.2.gz/.3.gz）、sha256.txt、README.txt（含回滚）。tar 校验后已删 data/kill9_tail、data/kill9_tail_cron.log、data/.kill9_tail_job.lock 及轮转日志。
- 备份：`/root/ops/kill9-retire-20261105-174946/`（files/ 全部原件 + meta/state.txt、sha256-before.txt、prompt-baseline.json、ab_capture.py、ab-before.json、ab-pre/post-panel-context.json）。
- 回滚：`git revert 353a886 6505158 f860879 54f8376 ad4bcb7 f59169a`；data 解两个 tar 回 data/；`/root/run_stats.py` 用 `$BK/files/run_stats.py` 覆盖；`/etc/logrotate.d/penta5` 用 `$BK/files/logrotate-penta5` 覆盖；`bash scripts/crontab_tool.sh install` 并跑 `python3 scripts/freeze_guard.py init --framework kill9_tail --renew`；panel.html/hitrate.html 为生成物，下轮 run_stats 自动重建；无需重启。
- 非 git 改动：`/root/run_stats.py`、`/etc/logrotate.d/penta5`、panel.html（生成物）、data 封存删除。
- 风险/遗留：并发窗口提交 `615b3e1 feat(kill8): ③④方向改为多数锁定` 与本退役交错（未触碰其文件、未 git add -A），本段 commit 列表不含它；仓库内 kill9 字样仅剩历史台账/版本串（panel_live_refresh、attack_editor v18）/历史 hash_updates/`model_retirement` marker/未跟踪 .bak 备份；未改任何剩余框架口径。

### kill8 ③④ 多数锁定升级（2026-11-05 18:00，`615b3e1`）

- 需求（m00229）：「四个形态取反向」；问卷 reverse_scope 选「③④混杂时也要反向锁定」——描述原文「不再让 AI 二选一，按四个位置排除数字的多数方向取反向（如 3 大 1 小 → 全小）」。
- 口径（v4）：①② 单双/大小不变（16 选 1 + 位置反方向约束）；③④ 全大/全小、全单/全双 由「四个位置全部同向才锁定」改为**多数锁定**：每位置排除数字在该维度全部同向才投票；严格多数 → 锁定反方向全形；平票（如 2 大 2 小）或全无票 → 仍由 AI 在 2 种里按冷热自选。结构仍 4 项、整组 4 项全中，基线不重算（沿用 v3 0.7733、4 项）。
- 代码（commit `615b3e1`，3 files changed +52/−26）：lib/kill8_tail.py RULE_VERSION=`kill8-tail-v4-majority-lock-four-reverse-cold-forms`、新增 `_majority_lock()`、`reverse_constraints(with_details)` 返回多数投票结果、`_all_constraint_text` 锁定/平票文案、build_prompt audit `kill8-prompt-v3-majority-lock-four-forms`；lib/kill_eight_view.py scope 与 foot 改「多数锁定」；lib/hitrate_baseline.py 追加 v4 行 `(4, 0.7733)`。
- 测试/探针：/tmp/kill8_v4_test.py PASS=69 FAIL=0；/tmp/kill8_v4_view_probe.py PASS=22 FAIL=0（旧 v3 封存按 legacy 渲染 4 卡 + 旧口径提示；合成 v4 4 卡、无 legacy 提示、4 个「除」；空态）。
- 真实周期（2026-11-05 18:00 手动 run）：data/kill8_position_exclusions.json 临时设 {"q":"5","b":"6","s":"7","g":"9"}（4 位置全偏大 → 锁全小；5/7/9 单、6 双 → 3 单 1 双 → 锁全双）；封存 ref=20261105216 → target=20261105217，published_at=18:00:37；forms parity=双单双双、size=小小小小、all_size=小小小小、all_parity=双双双双；攻击语「除双单双双除小小小小除小小小小除双双双双」；面板 KILL_EIGHT 区块 4 卡、含「多数锁定」文案、基线 77.33%、data-as-of=20261105216。同日 17:5x 另一 v4 封存 ref=20261105215→216 已按 counted_forms=4、group_hit=True、4 子项全 hit 结算（今日 v4 total=1/group_hits=1；v3 历史封存计 legacy=7 期、全中）。验证后排除数字已还原为 {"q":"9","b":"2","s":"","g":""}。
- 备份 `/root/ops/kill8-v4-175342/`（files 改动前、files-after 改动后、evidence：patch/测试/探针、MANIFEST.txt sha256）；回滚 `git revert 615b3e1`。v3 历史封存（`kill8-tail-v3-four-reverse-cold-forms`）保留为 legacy，不计入 v4 统计。
- 显示修正（`3da4488`）：卡片副标题原把 FORM_LABELS（「单双形态」等）再拼「形态」，渲染成「单双形态形态」；模板改为标签只渲染一次（「排除该四位单双形态（计入整组）」）。不改口径/基线/RULE_VERSION。2026-11-05 18:11 手动 run（ref 20261105218→219，exclusion {"q":"9","b":"2"}）后 panel.html KILL_EIGHT 区块 4 卡副标题正常、无「形态形态」；封存 forms parity=双单双单 size=小大大小 all_size=小小小小 all_parity=双双双双；旧封存 20261105216→217 亦已按 counted_forms=4 结算全 hit。
- 输入源提示（`4d026e4`）：面板输入区顶部新增一行「本期计算使用排除数字：千 X · 百 X · 十 X · 个 X（输入框仅为浏览器本地记忆；点「启动本框架」后按输入框重新计算）」，数值从封存 position_exclusions 读取，避免浏览器本地记忆与服务端实际输入不一致造成误读；纯展示，不改口径/基线。

### kill3 专属静态上下文回灌试验（2026-11-05 18:10，`c5f6094` `75ebe96`）

- 需求（m00325「回做」；背景 m00287）：12:26 后 kill3 整组命中率从 69.57%（ref 057–148）落到 56.06%（149–214），用户要求恢复早上 70% 的样子。取证已证明命中率不是可回退开关（当天选码/结算/8 项结构未变），真实变更点是 12:22–12:26 退役 selected_six 后，共享 `lib/panel_context.py` 把 frameworks['selected_six'] / frameworks['six']（及 model_tiers.six）从 kill3 每期 prompt 上下文里删掉。本试验只把这两段历史静态快照补注入回 kill3 的 prompt 面板段，用于验证输入差异是否影响命中率；不做整体回退（共享 build 改回会把 12:11 冻死的 selected_six 死数据注入 kill1/he3/stat50 全部 prompt，且 six 条目已不可重建）。
- 改动（`c5f6094`）：`scripts/run_kill3_tail.py` 增 `FROZEN_CONTEXT_PATH` 与 `_apply_frozen_panel_context()`，在 `panel_context.build(...)` 之后只对 kill3 的 panel_snapshot 补注入 frameworks.selected_six / frameworks.six / model_tiers.six；文件缺失或解析失败静默 no-op（删该 JSON 即关闭）。新增 `ops/kill3-frozen-context-ref20261105148.json`（1,242B，sha256 `c9529a79…`），内容取自 seal ref 20261105148 的 panel_context（source_published_at 2026-11-05T12:21:45.839558+08:00，退役 commit 988b221）。不升 RULE_VERSION（v15）、不动选码/尾码/8 项结构/封存/结算、不碰共享 `lib/panel_context.py`。
- A/B（18:07 离线）：当前 ref 20261105217→218 重建 panel_context 后提示词面板段 6,135B → 6,602B（+467B，sha 0911e283…→e3e71d8b…）；除 frameworks 新增两段与 model_tiers.six 外其余 section 逐字节相同；幂等；缺失文件路径不注入；py_compile 通过。
- 冻结登记（`75ebe96`）：`ops/kill3-freeze.json` 的 files['scripts/run_kill3_tail.py'] 7c55f6ce…→ac37eb42…，追加 hash_updates（frozen_at 不重置、RULE_VERSION 未变）；`ops/freeze-guards.json` 同步 `freeze_guard.py update`，5 框架 changed=-。
- 备份/回滚：`/root/ops/kill3-frozen-ctx-20261105-180740`（改动前 4 文件 + frozen json + ROLLBACK.txt）。回滚 `git revert c5f6094 75ebe96 <this>`；只关闭回灌（保留代码）`rm -f ops/kill3-frozen-context-ref20261105148.json`，下一个 5 分钟周期即恢复旧上下文。
- 风险：注入的是 12:11 冻死的 selected_six 死数据（today 42/86=48.84%，latest_settled 20261105146），会作为静态死数据长期留在 kill3 prompt 里；仅实验，跑 20–30 期后按整组命中率 vs 精确基线 0.5863 与改前窗口对比决定保留/撤销。

### hitrate_stability 已登记升版过渡期豁免（2026-11-05 19:41，3d78525）

- 需求：check27 hitrate_stability 在 kill1 v4 有意升版后按「口径清零」报 fail（预计 11-06 10:28 自愈）；用户指令「修复完善」——只对「已登记的有意升版」降噪，不下调全体阈值。
- 代码：scripts/hitrate_stability.py 新增 WHITELIST='ops/hitrate_stability_whitelist.json' 与 _load_whitelist()；_judge(..., whitelist_entry=None) 命中登记且 until 未过期时只把 churn 告警降级为 watch 并标注原因，覆盖断崖/短期陡降等其他 alert 逻辑不变。报告 revision 升为 hitrate-stability-v2，新增 whitelist（active/expired/ignored）与 summary.whitelisted 字段；过期或 until 无法解析的条目自动失效，不形成无声长期豁免。
- 登记：ops/hitrate_stability_whitelist.json → kill1_tail / kill1-tail-v4-two-position-one-tail，until 2026-11-06T10:30:00+08:00（24h 自愈点 +2min）。
- 验证：手动 python3 scripts/hitrate_stability.py → status=warn alerts=0 watch=1（kill1_tail 降级），as_of=20261105235；沙盒 /tmp/hs_test 五档回归（active/expired/mismatch/baduntil/empty）分别得到 watch/alert/alert/alert/alert，rc=0；py_compile 通过。
- 回滚：git revert 3d78525；或删 ops/hitrate_stability_whitelist.json（代码保留时无条目=原行为）。

### 运行观测三模型读数审计修复（2026-11-05 19:45，5bdc599 023557f）

- 需求：用户 m00001「检查全部模型，数据是否读取正确。」只读审计（skill audit-panel-runtime-data-reads）结论：runtime_observability 面板 3 个模型（killer/framework/he3_refine）逐列与原始账本独立复算全部一致，P0 无；he3 今日 ref 20261105104 真实缺 1 期（其 target=20261105105），最可能为 08:40:01 的 */5 he3 cron 因 /var/lock/penta5-he3.lock 被前一轮 96MB he3_refine_hit_stats 重写/归档占用而 flock -w 30 静默跳过（日志 227 warning / 227 HE3_REFINE / 454 AI_HTTP 成对，无孤儿运行），未 100% 钉死。用户 m00146「修复完善」后落地下列 P1/P2。
- 展示层（5bdc599，lib/runtime_observability.py + lib/runtime_observability_view.py）：① he3 档位标签——_MODEL_ENV_KEYS 增 'he3_refine': 'PENTA5_MODEL_HE3' 且 _model_tiers 接受该 .env 键（.env 实际值 deepseek-flash），summary/panel 行 label 由「三合五尾」变「Flash 三合五尾」；顺带 docstring 'four tracked active AI paths'→'three'。② 摘要行「数据N期」→「数据至N期」去歧义（实际含义为最新数据期号取后三位）。③ 脚注补一行「策略拒绝列只统计策略校验类拒绝；输入等其它异常拒绝只计入重试。」（framework 的 ValueError 类拒绝只进重试列）。
- 运维可见性（023557f，ops/crontab.txt 第 6 行 he3 cron）：由 flock -w 30 /var/lock/penta5-he3.lock bash -c 'sleep 40 && …' 改为 bash -c 'exec 9>/var/lock/penta5-he3.lock; flock -w 30 9 || { echo "$(date -Iseconds) WARN HE3_CRON_LOCK_TIMEOUT skipped" >> /root/penta5-runner/data/he3_refine_cron.log; exit 0; }; sleep 40 && …'，锁超时不再静默、落显式 WARN 行；成功路径行为不变（仍 sleep 40 → timeout 255 跑 he3）。
- 验证：两 py py_compile 通过；离线 render 断言 RENDER_ASSERT_OK（START/END 各 1、含「数据至」「策略拒绝列只统计策略校验类拒绝」）；锁逻辑 /tmp 三路径 TIMEOUT_PATH_OK/ACQUIRED_PATH_OK/BODY_HOLDS_LOCK_OK，inner bash -n OK；crontab_tool.sh install 后 live 与 ops/crontab.txt diff=0、grep HE3_CRON_LOCK_TIMEOUT=1；线上 obs cron 19:41:46 起 summary label he3=Flash 三合五尾，panel.html 19:42:07 的 runtime 块含「API启动 · 数据至236期」「Flash 三合五尾」「策略拒绝列只统计策略校验类拒绝」各 1 次（真实周期自动生效，未重启）。
- 门禁：refresh_source_manifest --if-drift no-drift（当前基线 /root/backups/auto-baseline-20261105-194034）；ops_health overall=warn，manifest_drift/crontab_drift/freeze_guards ok，warn 项（kill3_stability、ledger_size、coverage、hitrate_stability）均为既有、非本次引入。
- 备份/回滚：/root/ops/fix-runtime-observability-20261105-193805/（改前 lib/runtime_observability.py、lib/runtime_observability_view.py、ops/crontab.txt、crontab.before；原 sha256：runtime 036b6dda…、view 3e0af8fe…、crontab df93a3e3…）。回滚：git revert 023557f 5bdc599（crontab 回退后需 bash scripts/crontab_tool.sh install），或按备份覆盖后重装 crontab。
- 未修（记录为限制）：data/ai_delivery_timing.json 自 2026-11-01 02:16 无 killer/framework/he3 事件，故三行「服务端攻击语就绪：未观测 / 本浏览器本次收到：未采集」为观测缺失而非读错，需上游遥测源；P0 无。

### he3 封存账本热窗收紧（2026-11-05 19:50，292691e）

- 需求：ops_health ledger_size 连续 WARN——data/he3_refine_seals.json 111.3MiB（110MiB 预警线，128MiB 为 runtime_observability 读取上限）、data/he3_refine_hit_stats.json 96.2MiB；he3 每期封存约 90KB，7 天热窗约 1230 期约 112MiB，随期数线性增长会击穿读取上限。
- 方案：归档设施（lib/seal_archive.py 的 read_hot/read_full/archive/write_full_separated + data/archive 月度冷分片）已存在，问题只是保留窗口过长。在 lib/seal_archive.py 新增模块常量 DEFAULT_KEEP_DAYS = 2 作为 archive() 与 write_full_separated() 的默认值（hit_stats 写方不传参也自动 2 天，策略单一来源），scripts/archive_seals.py CLI 默认与 ops/crontab.txt 行内 --keep-days 同步 7→2；read_full 对冷分片+主文件透明合并，统计/面板/回放口径不变。
- 归档执行：python3 -u scripts/archive_seals.py --target he3_refine_seals --target he3_refine_hit_stats --keep-days 2 --apply（19:47:24–19:47:41，RC=0）。seals hot attempts 1240→356 / prediction_seals 1232→355，冷分片 202610 +1113、202611 +648，archived=1761；hit_stats hot verified 1226→353，202610 +548、202611 +325，archived=873。体积 data/he3_refine_seals.json 116,817,920B→39,834,132B（112MiB→38MiB）、data/he3_refine_hit_stats.json 100,980,374B→33,107,941B（97MiB→32MiB）。
- 完整性：归档前后用 read_full 逐期 canonical sha256 指纹对比（/tmp/digest_he3.py + compare）——seals full attempts 2367→2368、prediction_seals 2355→2356（唯一新增 20261105237 为 19:46 真实周期）、hot 356/355；hit_stats full verified 2349 不变、hot 353；missing=0 mismatch=0，RESULT: PASS。read_full 冒烟 seals 2356 / attempts 2368；freeze_guard 五框架 changed=-。
- 门禁：刷新 manifest 后 ops_health 复验（manifest_drift/crontab_drift/ledger_size/freeze_guards OK）；下一轮 he3 因输入指纹变化冷算一次，并复查 hit_stats hot 仍约 350 期不被写方回灌。
- 备份/回滚：/root/backups/he3-ledger-shrink-20261105/（三文件改前原件、digest_before/after、archive_run.log）；回滚 cp 备份回三文件 + bash scripts/crontab_tool.sh install；已归档冷分片无需回滚（read_full 语义等价）。

### 运行观测交付观测断更 + 非策略拒绝口径修复（2026-11-05 20:15，6d4c509 c843194 fdcdee6）

- 需求：用户 m00238「全部修复完善啊」——把上一节“未修（记录为限制）”的 data/ai_delivery_timing.json 断更与 framework 非策略拒绝口径一并修好并上线取证（本节点同时关闭上一节限制）。
- 根因 1（交付观测静默断更，已钉死）：7ce1814（2026-11-01 02:26 退役 cold_framework）把 cold 从 lib/panel_delivery_timing.py 的 MODELS 移除，但账本仍留 387 条 cold 历史事件；_read 的 identity['model'] not in MODELS 校验令整个账本 invalid（ValueError('delivery-invalid-event')），record_delivery 每次先 _read 都抛错，lib/panel_io.py `_delivery_worker` 的 `except Exception: pass` 静默吞掉 → data/ai_delivery_timing.json 自 2026-11-01 02:16:58 起停写，三模型“服务端攻击语就绪”永久未观测。离线复现：tmp 副本调 record_delivery 抛 delivery-invalid-event；把历史过滤到当前 MODELS 后写 tmp 账本成功追加 3 条且 measurement_error=False。
- 根因 2（framework 非策略类拒绝不可见）：lib/runtime_observability.py parse_log 只把 StrategyValidationError 计入“策略拒绝”、Timeout/ReadTimeout/ConnectTimeout 计入超时，ValueError 等其它 error_type 被忽略、只体现在重试数；今日 ValueError(ref 20261105162) 即此类。
- 修复：①（6d4c509）_read 跳过已退役模型的旧事件（保留历史；3 天保留策略在下次成功写入时自然淘汰）；_delivery_worker 异常写 data/panel_delivery_observer.log 有界诊断（>256KB 清空；UTC 时间 + 异常类型 + 前 300 字符）。②（c843194）parse_log 新增 other_rejected 集合，rejected 分类 strategy / timeout / else，_period_row/_quality 新增 other_rejected_count，运行明细表扩为第 4 个数字（今日缺期 / 超时 / 策略拒绝 / 其它拒绝），表头与脚注同步。③（fdcdee6）lib/operational_availability.py 增 PUBLISH_ORDER_SLACK=5s 的封存-提交有序容差（面板提交可比封存 published_at 早数毫秒，identity 仍为 hash-bound），修复 he3 永久 unobserved。
- 线上真实周期证据（未重启，obs cron 每分 sleep45 自然生效）：data/ai_delivery_timing.json 20:05:28 重写 1446B/3 events（旧 1924 条 cold/selected6 历史按 3 天保留淘汰），20:11:17 已 9 events；summary 20:12:46：labels killer=Flash 杀码 / framework=Flash 框架 / he3_refine=Flash 三合五尾，framework today strategy_rejected=3 / other_rejected=1 / retry=4；availability killer=verified(attack)、framework=verified(attack)、he3_refine=card-only（seal 20261105241 published 20:06:24.995155 vs 观测 20:06:24.994426；容差后 card-only、before_close=False）；panel.html 20:13:08：仅卡片已观测=1 / 攻击语提交已观测=2 / 服务端攻击语就绪：未观测=0 / 其它拒绝=1 / 0 / 0 / 3 / 1=1；data/panel_delivery_observer.log 不存在（无新失败）。
- 离线验证：tmp 账本 record_delivery 追加 3 条 + measurement_error=False；oa.observation('he3_refine', seal241,...) → card-only / margin 145.006 / before_close False；view.render(collect()) 断言“数据至”“其它拒绝”、脚注句、“0 / 0 / 3 / 1”各 1，FRAG_LEN=10251；5 个 py 均 py_compile 通过。collect() 在 lib/runtime_observability.py:620 返回 build_summary 后的 summary，离线正确入口是 view.render(collect())。
- 门禁：scripts/refresh_source_manifest.py --root /root/penta5-runner --if-drift → no-drift（基线 /root/backups/auto-baseline-20261105-201451）；ops_health overall=warn，30 项中仅 kill3_stability / coverage / hitrate_stability 三个既有 warn，manifest_drift / crontab_drift / freeze_guards ok。本次只改观测读取/展示/诊断层，不动预测语义、封存与结算。
- 备份/回滚：/root/ops/fix-runtime-observability-2-20261105-200335/（backups/lib 五文件、backups/data/ai_delivery_timing.json、backups/ops/README.md、before.sha256、before/after.operational_availability.sha256）。回滚：git revert fdcdee6 c843194 6d4c509（obs cron 下一分钟周期自动用回旧代码），或按 backups 覆盖。
- 未覆盖（限制）：浏览器侧“本浏览器本次收到”仍为未采集——服务端观测只能证明面板提交时刻，浏览器实际收到需浏览器端遥测，本次未新增；本节点关闭第一节 ai_delivery_timing 限制，P0 无。

### kill8 v5 ①/② 与全形卡同形时自由位强制去重（2026-11-05 20:30，`a4fee74`）

- 需求：用户 m00410「又重复了」——面板 ref 20261105244→245 四卡 单单单单 / 大小小小 / 小小小小 / 单单单单，①与④同形（服务端排除数字 {q:0,b:8} 全偶 → ①千/百钉死单、④多数锁定全单；①剩余十/个自由位被 AI 又选单，本可避免）。问卷 id=dedupe_free_positions 选「有自由位时强制不同」：被排除数字钉死的位置绝不动；同形且该卡还有自由位时，翻自由位保证四卡不同形；四位全钉死仍允许同形；只改选形逻辑，基线不变。
- 代码（3 文件，+41/−12）：`lib/kill8_tail.py` 新增 `_dedupe_forms(forms, constraints)`——parity/size 与对应全形卡同形且存在自由位时翻最靠前的自由位；`normalize_response` 校验约束后调用去重；`build_prompt` 加去重说明与任务第 5 条口径、audit version 升 `kill8-prompt-v4-dedupe-four-forms`；`RULE_VERSION='kill8-tail-v5-dedupe-four-reverse-cold-forms'`；`lib/kill_eight_view.py` scope/foot 补去重规则与「默认自动去重」文案；`lib/hitrate_baseline.py` 追加 v5 行 (4, 0.7733)（结构未变，沿用，未重算）。
- 验证：`/tmp/kill8_v5_test.py`（v4 用例迁移）PASS=69 FAIL=0；行为四例：0/8 → 单单双单（千/百仍钉死单，十位翻双）、1/3 → 双双单双/大大小大、四位全 0 → 允许同形、无排除 → 双双单双/大小小小；`/tmp/kill8_v5_view_probe.py` PASS=22 FAIL=0（legacy v4 渲染容错 + foot/scope 含去重文案）。
- 真实周期（v5）：2026-11-05 20:30:47 首枚 v5 封存 ref 20261105246→247，四形态 单单双单 / 大小小小 / 小小小小 / 单单单单（①与④不再同形：十位自由位由单翻双）；20:35:50 第二枚 247→248。首枚已结算 counted_forms=4 / group_hit=True / verified=True（实际 7 7 0 8：parity 单单双单 vs 单单双双 hit、size 大小小小 vs 大大小大 hit、③④ hit），今日 total=1/group_hits=1、legacy={17 中 16}；面板 data-as-of=20261105247、4 卡、77.33%、去重文案在线。
- 备份/回滚：/root/ops/kill8-v5-202730/（files 改前、files-after 改后、evidence 补丁/用例/探针/行为、MANIFEST）；回滚 `git revert a4fee74`；方向口径与基线未变，v4 历史封存保留为 legacy。

### 命中率展示层 P0（2026-11-05 20:44，`3f7cb69` + 紧随的 chore(ops) 提交）

- 需求：用户 m00001「聊聊，一运行久命中率就下降问题」→ m00075「有没有办法杜绝这种断崖试下跌」→ m00112「修复完善」。确认只做 P0 只读展示：不动选码/封存/结算/prompt/RULE_VERSION；6a9b15a 那版累计行曾被回退（c72ff75），故先给方案再落地。
- 结论依据（只读分析）：命中率随运行时长"下降"是 ①小样本向精确基线回归 ②三类假断崖（05:00 清零 / 换 rule_version 清零 / 漏发按 0 命中）③二元化度量太粗。随机模拟（p=0.5863, n=178）：累计曲线最大回撤中位 9.3pp、p90 16.3pp；n=2939 中位 10.4pp（取极值机会更多，不随 n 缩小）。项级度量（framework 平均 7.42/8、92.8%；kill3 7.53/8、94.3%）最大回撤 2.1/2.9pp，约为整组的 1/3.6；69.57%→56.98% 的 12.6pp 段落落差在 80% power 下每侧约需 236 期才能判别。
- 改动（`3f7cb69`，5 文件 +449/−6）：
  - 新增 `lib/hitrate_display.py`：`build_display_metrics()` 只读派生 `drawdown`（自本版峰值回撤 + 同 n 纯随机最大回撤中位/p90/P≥观测，seed 20261107，burn-in 25 期）与 `item_stats`（平均命中项数/项级命中率/逐期项数分布）；序列按累计 `as_of_period` 截断使 n 与累计行一致；kill3 8 项 = 2 counted 位置 + 合尾 + 2 反向形态 + 3 三数避让，kill1 3 项 = 2 位置 + 合尾。
  - `scripts/hitrate_stability.py`：报告行合并上述字段（try-import，异常只跳过展示，不影响护栏判定）。
  - `lib/kill_three_view.py`：kill3 卡新增「本版累计（跨天不清零）」行（整组%/95%CI/精确基线/偏差/p/自峰值回撤与随机参照/平均命中项数/n<200 提示），今日口径行不变。
  - `lib/framework_hit_view.py`：framework 卡 notice 后新增「累计口径」行（CRLF 保持，diff 仅新增行）。
  - `lib/hitrate_stability_view.py`：累计列补 Δ±pp·p 与回撤（随机中位），首列补平均命中项数，脚注解释回撤/随机中位。
- 验证：`/tmp/offline_check.py` 用备份 .bak 旧模块做只加不改 A/B——摘除新增行后 kill3（6817B）、framework（7435B）渲染与旧版**逐字节一致**；stability 表去新增片段后一致；panel 副本 30 个区块标签配平、无未闭合。`py_compile` 全过；stability 单轮 0.80s（cron timeout 90s）。
- 线上生效（未重启，面板由 `/root/run_stats.py` 每期重生成）：20:38:17 自动刷新后 `panel.html` 已含三处新行——kill3 `本版累计 62.03%（116/187 期，95%CI 54.9%–68.7%）· 基线 58.63% · +3.40pp · p=0.345 · 回撤 9.1pp（随机中位 9.8pp）· 平均命中 7.53/8（94.1%）`；framework `55.95%（1651/2951）`；稳定性表 kill3/framework/kill1 三行含 `回撤 x.xpp（随机中位 y.yp）` 与 `平均命中`。20:44 数据刷到 kill3 n=190 62.63%、framework n=2954 55.92%。
- 冻结/门禁：`ops/freeze-guards.json` kill3_tail 的 `lib/kill_three_view.py` fde296d8…→f6fb76d6…（`freeze_guard.py update`，五框架 changed=−）；`ops/kill3-freeze.json` 同步 sha + hash_updates 追加（frozen_at 不重置、RULE_VERSION 未变）。`refresh_source_manifest.py --if-drift` → baseline `/root/backups/auto-baseline-20261105-204004`（429 文件）；`ops_health` overall=warn，仅既有 coverage / hitrate_stability(kill1 白名单观察) / kill3_stability 三项，freeze_guards、manifest_drift 均 OK。
- 备份/回滚：`/root/ops/hitrate-display-20261105-202016/`（5 个改前文件 .bak + pre.sha256）。回滚 `git revert 3f7cb69 <本节 ops 提交>`；或用备份覆盖 5 文件、`git checkout ops/freeze-guards.json ops/kill3-freeze.json` 后重跑 `python3 scripts/freeze_guard.py status`。
- 未做（需用户拍板）：P1 版本切换不清零重标定 + 失败分型；P2 覆盖率补跑 + killer v4-pro 0/11 核查；P3 改项数。


### 命中率展示层 P1：切版不清零 + 失败分型（2026-11-05 20:5x，`bfd8414` + 紧随的 chore(ops) 提交）

- 需求：用户 m00360「完善」——接续 P0 做 P1：①换 rule_version 时累计不再整段清零，改「跨版本核心杀项项级累计 + 近 5 版分段」展示；②把近 24h 模型调用失败分型与 kill3 缺槽/版本切换单列，缺期不按 0 命中。
- 数据依据（只读探针，全部 verified+counted）：kill3 账本 982 条中 928 条带 counted（v15 192 / v12 178 / v11 118 / None 96 用 seals.seal_rule 回填 / 其余小样本）；核心项（counted 位置 + 合尾）= 2589/2888 = 89.65%（929 期，均 3.11 项/期），分段核心率 v15 90.16%、v14 92.86%、v13 89.74%、v12 90.45%、v11 90.40%、v10 83.33%、v9 92.82%、v8 85.00%、v7 88.07%、v6 93.00%、v5 89.58%、v3 75.00%。kill1 核心项 = 1276/1418 = 89.99%（646 期，均 2.20 项/期），分段 v4 89.95%、v3 90.41%、v2 87.88%、v1 89.32%。→ 核心杀项跨版同构，切版不清零可展示；整组仍按各版基线重标定、不跨版相加。
- 改动（`bfd8414`，5 文件 +221/−1）：
  - `lib/hitrate_display.py`：新增 `_settlement_version_rows()`（settlements + seals 回填 rule，取 verified+counted，item = counted 位置 hit 数 + tail hit）与 `_version_stats()`（按 rule 分段，输出 core / segments）；`build_display_metrics()` 对 kill3/kill1 追加 `versions` 并按累计 as_of 截断。
  - `scripts/hitrate_stability.py`：新增 `_failure_split()`（model_usage/summary.json 今日 attempts/responses/retries/errors + ops_health kill3_stability 的 published/missing_slots/version_changes/p50），写入 `report['summary']['failure_split']`。
  - `lib/kill_three_view.py`：卡内新增「跨版本核心杀项（切版不清零）x/y 项 = z%（基线 90.0%）」与「分段（近 5 版）」两段，位于本版累计行之后、今日口径行不变。
  - `lib/kill_one_view.py`：新增 `_cumulative_html()`（读 hitrate_stability.json 的 kill1_tail 行，校验 rule == RULE_VERSION），渲染本版累计（整组/CI/基线/偏差/p/平均命中）+ 跨版本核心杀项 + 近 5 版分段；置于 render_block 末尾。
  - `lib/hitrate_stability_view.py`：新增 `_failure_split_html()`，脚注展示「近 24h 模型调用 x 响应 / y 异常（invalid_response、timeout），重试 z；kill3 发布 a 槽 / 缺槽 b / 版本切换 c 次；缺期按覆盖率单列，不计入累计 n，也不按 0 命中」。
- 验证：`py_compile` 五文件全过；`flock -w 120 /var/lock/penta5-hitrate-stability.lock python3 -u scripts/hitrate_stability.py` → as_of=20261105250、status=warn、alerts=0、watch=1（kill1 白名单仍生效）；新字段核对 kill3 versions.core period 929/item 2589/2888、segments 13 段（v3..v15）、kill1 core 646/1276/1418、summary.failure_split={model_calls:{attempts 975,responses 956,error_total 19,errors{invalid_response 18,timeout 1},retries 4}, kill3_slots:{published_24h 259,missing_slots_24h 2,version_changes_24h 2,p50 82.3}}。
- 离线渲染：`/tmp/p1_render_check.py` 把三个 apply 依次作用到 panel.html 副本——kill3/kill1/stability apply 全 ok，delta 1004B，`k3-acc`/`k1-acc`/`跨版本核心杀项（切版不清零）`/`失败分型：` 各 1、31 个区块标签全配平；预览 /tmp/panel_preview_p1.html。
- 线上生效（未重启、未手动触发；面板由 /root/run_stats.py 每期由 penta5-panel-sync 守护进程重新生成）：20:57:10 自动刷新后 panel.html（as_of 20261105251，378140B）已含——kill3 `跨版本核心杀项 2589/2888 项 = 89.65%（基线 90.0%，929 期，均 3.11 项/期）` + 近 5 版分段；kill1 新块 `本版累计 71.43%（90/126 期，95%CI 63.0%–78.6%）· 基线 72.90% · 偏差 -1.47pp · p=0.710 · 平均命中 2.70/3 项（项级 89.9%） · 跨版本核心杀项 1276/1418 项 = 89.99%`；稳定性脚注 `失败分型：近 24h 模型调用 956 响应 / 19 异常（invalid_response 18、timeout 1），重试 4；kill3 发布 259 槽 / 缺槽 2 / 版本切换 2 次；缺期按覆盖率单列，不计入累计 n，也不按 0 命中。`
- 冻结：`ops/freeze-guards.json` kill3_tail `lib/kill_three_view.py` f6fb76d6…→df32f5d0…、kill1_tail `lib/kill_one_view.py` 0a99919f…→13c4e049…（`freeze_guard.py update`，五框架 status changed=−）；`ops/kill3-freeze.json` 同步 sha + hash_updates 追加第 47 条（commit=bfd8414，frozen_at 不重置、RULE_VERSION 未变）。kill1 无 legacy freeze 文件，只走 guards。
- 备份/回滚：`/root/ops/hitrate-p1-20261105-205154/`（5 个改前 .bak + pre.sha256，路径另存 /tmp/hitrate_p1_backup_dir）。回滚 `git revert <本节 ops 提交> bfd8414`；或按备份覆盖 5 文件 + `git checkout ops/freeze-guards.json ops/kill3-freeze.json` 后重跑 `python3 scripts/freeze_guard.py status`。
- 未做（需用户拍板）：P2 覆盖率补跑 + killer v4-pro 0/11 核查；P3 改项数。


### 冷码杀号战绩窗口拉长（2026-11-05 21:00，`57816f7`）

- 需求：用户（m00002）「窗口给我拉长50-100期」，经确认取 100 / 80 期（各 +70 / +60）。
- 改动：`scripts/cold_hit_stats.py:27` `WINDOWS = (30, 20)` → `(100, 80)`（唯一 1 行）；展示层 `lib/cold_hit_stats_view.py` 从 `data/cold_hit_stats.json` 动态读 window/samples，无需改视图；systemd 守护 `penta5-position-cold.service` 已 restart 以加载新常量（否则守护进程按旧常量覆盖 JSON）。`scripts/position_cold_report.py:27`、`scripts/cold_backtest.py:27` 的同名 WINDOWS 未动（分属冷码观察/回测）。
- 验证：改后 `data/cold_hit_stats.json` as_of=20261105251、records=800、windows=[(100,700),(80,720)]，窗口100 Top1/2/3=90.04%/80.32%/70.32%、窗口80=90.56%/81.04%/71.32%（理论 90/80/70）；面板 20:56:56 自动刷新（`penta5-panel-sync` 每期结算后生成），两份卡片为「窗口 100 期 · 共 700 期样本」「窗口 80 期 · 共 720 期样本」，最多连中 16/17、最多连败 7/7、当前连中 3/3，与 JSON 一致，旧窗口 30/20 标签已消失。
- 回滚：`git revert 57816f7` + `systemctl restart penta5-position-cold.service`；或用备份 `/tmp/cold_hit_stats.py.bak-20261105-205512` 覆盖后重启。

### 命中率展示层 P2：覆盖率缺期归因 + killer 档位注记（2026-11-05 22:52，`7504f9f` + 紧随的 chore(ops) 提交）

- 需求：用户 m00449「完善」——接续 P1 做 P2：①覆盖率缺口能否补跑/如何防漏；②killer deepseek-v4-pro 0/11 核查。
- 结论 1（历史缺期不可补跑）：`scripts/run_ai_ready_job.py` 按 `expected_reference(now)` 冻结本 cron 槽，`WAIT_SECONDS=55` 内等源 latest，否则 `ReadyJobRejected('ready-job-source-timeout')`；代码明确「never switches to the next draw or creates a historical request」，且 operation window 已过的期无法再发布。→ P2 只做归因展示与防漏，不补历史。
- 24h 缺期归因（as_of 20261105273，窗口 20261104273–20261105272）：去重 10 槽 / 38 期·框架。
  - AI 总闸关闭 35 = 手动关 30（`20261104274..277`；11-04T01:29:00→23:03:53 共 21.6h）+ 余额欠费 5（`20261105043..044`；11-05T03:29:01→03:36:01，pause 时余额 0.25，充值后 9.86；guard 阈值 PAUSE/RESUME=1.00）。
  - 其它 3：he3 `20261105105`（ref 104 日志完全无 AI_HTTP/AI_RESPONSE，未发起请求）；kill1 `20261105115`（ref 114 `AI_HTTP_PHASE start` 后无 complete/validate/publish，被 timeout 220 杀掉）；kill1 `20261105137`（ref 136 complete 68s 但无 publish，校验/发布阶段失败）。
  - 7d coverage 仅 46%~55% 的主因是 11-02~11-04 多段手动关闭 AI 总闸（累计 50+ 小时；11-02 04:32→15:42、16:43→11-03 00:33、11-03 02:47→10:07、12:33→18:07、11-04 01:29→23:03），不是框架掉链；恢复后 recent24 五框架 missed=0。
- killer v4-pro 11 期核查：来自 `data/r1_stats.json` 的 `prediction_seals[ref].model_metadata.requested_model`，分布 10-30 06:16（1）、10-31 04:26–04:46（6）、11-01 06:11–06:31（5），均 joint=False；seals 模型计数 flash 1961 / None 1010 / pro 11；`.env` 现全为 deepseek-flash，`data/model_tier_guard.log` 全为 `{"decision": "disabled"}` → 历史一次性试跑/手工档位，非现行配置。0/11（p0=0.4162 期望 4.58，z≈−2.80，名义 p≈0.005）但样本 11 且为试跑窗口 → 只在展示层注记，不改语义、不改基线。
- 改动（`7504f9f`，3 文件 +257/−2）：`scripts/framework_hitrate_cumulative.py` rolling 各窗口增 `missed_periods`（`MISSED_LIST_LIMIT=60`）；`scripts/hitrate_stability.py` 新增 `_period_start`/`_balance_samples`/`_balance_before`/`_ai_egress`/`_missing_attribution`，`failure_split` 增 `ai_egress` 与 `missing_slots`，`out_rows` 写入 `model_split`；`lib/hitrate_stability_view.py` 脚注增「AI 总闸近 24h 关闭 x 次共 y 分钟（余额欠费/手动）」「近 24h 缺期 e 期·框架（去重 u 槽）：AI 总闸关闭 a（余额欠费 b 期），其它 o 槽（…）」，killer 判定格增「含 deepseek-v4-pro 试跑 11 期 0/11 中（样本不足，单独看不可下结论）」。
- 验证：`py_compile` 三文件 OK；cumulative 重跑 as_of=20261105273（`missed_periods` killer/framework/kill3 各 7、he3 8、kill1 9）；stability 重跑 as_of=20261105273 status=warn alerts=0 watch=1，`failure_split.ai_egress`={off_windows_24h 2, off_minutes_24h 25.9, balance_pause_minutes 7.0, manual_off_minutes 18.9, enabled true, generation 79}，`missing_slots`={unique 10, events 38, ai_off_events 35, balance [20261105043,044], manual [20261104273..277], other [{he3 105},{kill1 115},{kill1 137}]}，killer `model_split`=[flash n=1964/40.33%, deepseek-v4-pro n=11/0%]；离线渲染 `/tmp/p2_render_check.py` apply ok、31 区块配平、delta −29B、预览 `/tmp/panel_preview_p2.html`。
- 线上生效（未重启、未手动触发；面板由 penta5-panel-sync 每期重生成）：22:52:08 自动刷新后 panel.html（as_of 20261105273）含脚注原文「近 24h 模型调用 1071 响应 / 19 异常…；AI 总闸近 24h 关闭 2 次共 26 分钟（余额欠费 7 分钟 / 手动 19 分钟）；近 24h 缺期 38 期·框架（去重 10 槽）：AI 总闸关闭 35（余额欠费 2 期），其它 3 槽（he3 20261105105；kill1 20261105115；kill1 20261105137）」与 killer 档位注记。
- 冻结/门禁：本组 3 文件均不在 freeze guards（无需 `freeze_guard.py update`，五框架 status changed=− 复验）；`refresh_source_manifest.py --if-drift` 与 `ops_health_check.py` 见下（本节 ops 提交前刷新）。
- 备份/回滚：`/root/ops/hitrate-p2-20261105-224557/`（3 个改前 .bak + pre.sha256）。回滚 `git revert <本节 ops 提交> 7504f9f`；或按备份覆盖 3 文件、重跑 cumulative/stability 各一次即可回到旧展示（累计脚本会在下个整 5 分钟周期自然重算）。
- 未做（需用户拍板）：P3 改项数（会改预测含义与基线，需先确认）。

### 加固：余额暂停阈值上调 + 单框架缺期/总闸长关告警（2026-11-05 23:03，`9a5b1fd`）

- 需求：用户 m00704「全部完善」——接续 P2 通报的三项加固：①余额守卫 pause 阈值 ¥1→¥5~10；②单框架缺期告警；③手动关总闸 >15min 明确告警。
- 改动（`9a5b1fd`，2 文件 +55/−5）：
  - `scripts/ai_balance_guard.py`：`PAUSE_BALANCE_CNY 1.00→5.00`、`RESUME_BALANCE_CNY 1.00→6.00`（1 元回差防抖）。依据 2026-11-05 03:29 余额已掉到 0.25 才 pause、欠费 7 分钟漏 2 期；阈值适用于 cron 下一分钟起的新代码。
  - `scripts/ops_health_check.py`：`check_coverage` 新增「单框架近 24 期（约 2h）有任一缺期即 WARN」；不用绝对覆盖率判定（24h 窗口含共同历史缺口时五个框架会一起误报，首轮实测已踩到）。新增 `check_ai_manual_off(ctx)`：AI 总闸关闭 ≥15min → WARN；从 `transitions` 最后一条 disabled 起算，`auto_paused` 区分「余额欠费自动暂停」/「手动关闭」，`transitions` 缺失时同样 WARN。注册进 `CHECK_FUNCS`/`FALLBACK_NAMES`，docstring 同步；新增常量 `COVERAGE_SINGLE_RECENT_MISSES=1`、`MANUAL_OFF_WARN_SECONDS=15*60`。
- 验证：`py_compile` 两文件 OK；`scripts/ai_balance_guard.py --dry-run` → balance 131.43、enabled=true、generation=79、action=none（阈值改动对当前无副作用）；离线假 root 单测 `check_ai_manual_off`：开启→OK、手动关闭 30min→WARN、余额欠费 30min→WARN（detail=「AI 总闸余额欠费自动暂停已 30.0m（≥15 分钟，请确认是否忘记打开）」）、手动 5min→OK、disabled 且无 transitions→WARN；`ops_health_check.py` 复跑 coverage 回到 OK（五框架 97.2–97.9%，单框架误报消失）、`ai_manual_off OK`，唯一 FAIL=`manifest_drift`（改 live 源码后未刷基线，提交后刷新即恢复）。
- 回滚：`git revert 9a5b1fd`；或按备份 `/root/ops/harden-20261105-225735/`（`ai_balance_guard.py.bak`、`ops_health_check.py.bak`、pre.sha256）覆盖后等 cron 下一分钟加载。

### P3 评估：改项数（评估后不改，2026-11-05 23:10）

- 需求：用户 m00704「全部完善」——P0 阶段预留的 P3「改项数」。该项会改预测含义（=改封存口径/新 RULE_VERSION），故先只读评估再决定；评估脚本 `/tmp/p3_candidates.py`、`/tmp/p3_drawdown.py`（只读，未改任何线上文件）。
- 方法：对 kill3 v15 全部 220 期已结算封存，按「逐封存枚举 10^4 开奖空间取均值」精确算随机基线（不单项相乘）；同时从 settlements 的逐项 `hit` 复算实际全中率，对每条口径做 burn-in 25 期的累计曲线最大回撤，并用同基线纯随机序列（4000 次模拟）给回撤分布。
- 结果（n=220，kill3 v15）：

| 口径 | 随机基线 | 实际 | 实际自峰值回撤 | 同基线随机回撤中位 | 同基线 p90 |
|---|---|---|---|---|---|
| 8 项（现行：2位置+合尾+2反向形态+3避开） | 58.64% | 62.27% | 10.41pp | 9.32pp | 16.67pp |
| 6 项（去掉 2 反向形态） | 68.69% | 71.36% | 13.20pp | 8.97pp | 15.56pp |
| 5 项（去掉 3 避开组） | 62.16% | 65.00% | 8.83pp | 9.23pp | 16.40pp |
| 3 项（2位置+合尾） | 72.90% | 75.00% | 11.21pp | 8.67pp | 14.87pp |
| 2 项（仅 2 位置） | 81.00% | 82.73% | 10.76pp | 7.65pp | 12.95pp |

- 结论：改项数对「断崖」的改善只来自 σ=√(p(1-p)/n) 的收缩——同基线随机回撤中位最多只降 18%（8→2 项），实际样本里 6 项口径的回撤反而更大（13.20pp）；同时会把「整组命中」从产品口径（8 项全中）改成更宽松口径，对外含义变了。→ **评估结论：不划算，保持 8 项不动**；P0/P1 展示层（整组累计 + CI + 基线 + 随机回撤带、平均命中项数、核心杀项跨版累计）已覆盖「看得清」的需求。
- 若之后仍要改：走 `upgrade-sealed-framework-revision` 完整升版（先定去掉哪几项、是否同步改 prompt，新 RULE_VERSION、两处基线表登记、冻结重置、旧版按 P1 的跨版展示迁移），不要只改结算计数。
- 回滚：本节只加文档，无代码/数据改动。
### 冷码口径：位置冷码窗口 30/20 → 100/80（2026-11-05 23:11，`e82b5c5`）

- 需求：用户反馈「30期的不稳波动太大」且 8 点前要用；延续 20261105 冷码杀号战绩 30/20→100/80 的口径统一，把面板剩余的位置冷码卡也拉长（冷码观察卡保持 30 期：每日 05:00 清零、8 点满窗，拉长反而未满窗）。
- 改动（`e82b5c5`，1 文件 +3/−3）：`scripts/position_cold_report.py` 的 `WINDOWS = (30, 20)` → `(100, 80)`、`RANK_BY_WINDOW = {30: 2, 20: 1}` → `{100: 2, 80: 1}`（长窗口给第 2 冷、短窗口给第 1 冷，保持两卡不重复），注释同步；冷码排序口径（次数升序/遗漏降序，沿用 `cold_observation._group`）不变。
- 依据：800 期回放（20261103050~20261105273）按「每期 4 位置至少一个换码」概率：第2冷 30 期 70.4% → 100 期 60.9%；第1冷 42.1% → 32.1%。
- 验证：`py_compile` OK；`systemctl restart penta5-position-cold.service` → active；23:02:24 起每期重算 `data/position_cold_report.json`（23:11:08 as_of 20261105278，windows [100 第2冷 20261105177~276、80 第1冷 20261105197~276]）；面板 23:11:10 自动刷新（as_of 20261105277，大小 377914B）后含「最近 100 期 · 第2冷」「最近 80 期 · 第1冷」，旧「最近 30/20 期」标签计数 0（冷码观察仍「近30期」、冷码杀号战绩仍 100/80）。
- 备份/回滚：`/tmp/position_cold_report.py.bak-20261105-230223`（pre sha256 `3a969b295d71df351795a45d4e857900…`）。回滚 `git revert e82b5c5` + `systemctl restart penta5-position-cold.service`（JSON 下期重算，面板下个结算周期自动更新）。

### 冷码观察窗口：近30期（每日05:00清零）→ 跨日100期滚动窗口（2026-11-05 23:21，`dc2eae4`）

- 需求：用户确认把「冷码观察」拉长为跨日 100 期（去掉每日 05:00 清零），使早 8 点面板也满窗稳定。
- 改动（`dc2eae4`，2 文件 +38/−35）：`lib/cold_observation.py` `WINDOW_SIZE` 30→100、`REVISION` `cold-observation-v4-30-first`→`cold-observation-v5-100-crossday`，`build_observation` 由「当前业务日（05:00 起）最近 ≤30 期」改为「最近 100 期滚动窗口」（按 5 分钟期槽连续、跨自然日；缺期/冲突仍显式报错 `cold-history-gap` / `cold-conflicting-records`，窗口外旧冲突不污染）。`lib/cold_observation_view.py` 同步 REVISION、校验上限 100、标题「冷码观察 · 近100期每项第1名」、meta「跨日滚动窗口（每日 05:00 不清零） · 实际 N 期，最多100期」、note 与失败文案。
- 验证：`py_compile` OK；`refresh_cold_observation` 重算 `data/cold_observation.json`（revision v5-100-crossday、window_size 100、sample_count 100、样本 20261105181~20261105280 连续 100 期，跨度 495 分钟）；独立复算 4 位置冷码（千2/百1/十5/个2）与 JSON 一致；面板 23:21:33 自动重建后含「冷码观察 · 近100期每项第1名」「跨日滚动窗口（每日 05:00 不清零）」，旧「近30期/最多30期」计数 0，`COLD_OBSERVATION` 区块标记 1/1 配平。冷码描述性统计不进 AI 上下文（`panel_context` 的 `extra_panel_input` 默认 False，全仓无 True 调用），本次不改任何预测语义。
- 回滚：`git revert dc2eae4`（JSON 与面板在下一结算周期自动恢复旧口径）；改前备份 `/tmp/cold_observation.py.bak-20261105-232010`、`/tmp/cold_observation_view.py.bak-20261105-232010`。之后 `python3 scripts/refresh_source_manifest.py --root /root/penta5-runner --if-drift`。

### 冷码杀号战绩窗口 30/20 → 100/80（2026-11-05，`57816f7`）

- 需求：把「冷码杀号战绩」卡的窗口从 30/20 期拉长到 50~100 期，并说明样本代价。
- 改动（`57816f7`，1 文件 1 行）：`scripts/cold_hit_stats.py` 的 `WINDOWS = (30, 20)` → `(100, 80)`；视图与 JSON 契约不变，`samples = len(rows) - window`。
- 样本代价（records=800，as_of 20261105280）：30 期 770 期样本/3080 注 → 100 期 700/2800（−70 期，−9.1%）；20 期 780/3120 → 80 期 720/2880（−60，−7.7%）。若取区间另一端 50 期：750/3000（−20，−2.6%）。
- 精度影响：Top3 命中率 70.50%（100 期）/71.35%（80 期）对理论 70%，95% CI 半宽 3.22pp → 3.38pp（n=700），基本不变。
- 副作用：窗口越长选码越黏，相邻期 Top3 码集换码率 89.2%（30 期）→ 79.7%（100 期），连中/连败被机械拉长（100 期 Top1 最长连中 56），有效独立样本低于名义期样本。
- 验证：`data/cold_hit_stats.json`（23:21:02，由 position_cold_daemon 每期结算后重算）windows=[100 samples 700, 80 samples 720]；panel.html 23:23 含「窗口 100 期 · 共 700 期样本」「窗口 80 期 · 共 720 期样本」。
- 回滚：`git revert 57816f7`，daemon 下期重算 JSON，面板下个结算周期自动刷新。

### 位置杀码稳定化影子恢复多档只读对照 + 提示词回灌由模型自决（2026-11-05，`db5be81`）

- 需求：影子此前标注「全部转正、不再采集」，本轮恢复为 hold2/hold3/hold5/不设上限 四档只读对照，并把对照结果与上期同位杀码、命中情况一起写进提示词，由模型决定是否沿用；明确不做自动写开关。
- 改动（`db5be81`，5 文件 +55/−14）：`lib/position_kill_shadow.py` 空态文案改「多档只读对照（hold2/hold3/hold5/不设上限）；是否沿用由模型自行决定，不由代码按阈值切换」；`lib/kill3_tail.py` 的 `build_prompt` 新增 `previous_hits` 参数，影子段输出「上期（参考期）你的位置杀码与命中情况：千位：杀 2（上期该位开 7 · 杀码命中）」并标注【参考信号】；`scripts/run_kill3_tail.py` 由上期封存 positions 与本参考期开奖算 hit/miss 传入，seal 留痕键改为 `position_hold_shadow` / `position_basis_flags` / `hold_adopted`（`scripts/kill3_v15_review.py` 新旧键兼容读）；`ops/kill3-freeze.json` version 2→3 重算受控 hash。
- 数值（`data/position_kill_shadow.json` 23:51:52）：kill3 回放 965 期/2031 注，现行 90.00%（换码 900）；hold2 89.46%/576、hold3 89.76%/434、hold5 89.27%/349、until-miss 90.00%/184；forward 654 期/1308 注，四档 backfill 与 forward 两段均有值。
- 真实周期：seal ref 20261105287 → target 20261105288（published 23:56:13）rule_version=v15、`position_hold_shadow`（966 期/2033 注）、`position_basis_flags` {q: cold_top3+hold_previous, b: cold_top3+hold_previous, s: cold_top3+hold_previous}、`hold_adopted`=['b','q','s']、`hold_policy`=model-decided；cron 日志 `kill3 previous position hits: 千位 5 hit · 百位 2 hit · 十位 0 hit`；该期结算已出现（target 20261105288 actual 7/8/9/8、counted q,b）。
- 边界：无 `data/*_auto_promote.json` 写入、无按阈值切 TAIL_POLICY/RULE_VERSION 的分支（kill3 路径 grep auto_promote = 0）；kill1 仍为 2026-11-01 起的确定性 hold3-until-miss，不进影子；现网封存/统计/攻击语口径未变。
- 回滚：`git revert db5be81` 并按 `ops/kill3-freeze.json` 上一版重算 hash；改前备份 `/root/ops/position-shadow-model-decide-20261105-235118/`（4 源文件 + freeze.json）。


### 反向取冷统一：kill3 升 v16（弃位取冷）+ killer 形态四位置反向锁定（2026-11-06 00:02，`417c472`/`73b9b91`/`bec59dd`/`4702422`）

- 需求：用户 m00208「统一马上执行」，接续 m00094「反向取冷形态」的只读评估——把带形态的框架统一为「反向 + 取冷」口径（形态项是排除项：命中 = 实际形态 ≠ 封存形态，单项随机基线 15/16 = 93.75%）。
- 各框架落点：
  - kill8 v5：原本已符合（反向只定方向、冷热只定剩余自由度），未动。
  - framework：不改码——`lib/joint_form_optimizer.py` 的 `reverse_lock` 已锁千/百位，后两位在锁内 16 组枚举取整组联合命中期望最大（10^4 精确枚举 = 锁内最冷）。
  - kill3：升 `kill3-tail-v16-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid-cold-drop`——被杀 3 位仍机械取杀码形态反面；弃位（dropped）两形态字符改由系统按该位置近 50 期字符冷热（出现次数升序、同次数遗漏降序、再按字符表序）确定并覆盖模型填写，seal 写 `dropped_form_cold`；8 项结构（2 计入位置+1 合尾+2 反向形态+3 组三数共现避开）、2 注/期、尾码 hold3-until-miss 均不变，基线追加同值行 (8, 0.5863) 不重校准。
  - killer：`p1/p2` 由 `k1..k4` 杀码完全确定（四位单双/大小全取反，`killer_reverse_lock`），不再 16×16 联合穷举；杀码/合尾/三合尾 hold 不变；日志与 metadata 增 `reverse_lock`。
- 只读回放依据（3423 期开奖 20261018021–20261105284；窗口 = ref 前 50 期；kill3 n=306、framework n=157 仅 2026-11-05 10:34 反向锁上线后、killer n=2905）：kill3 两项同中 现 272/306(88.9%) vs 取冷 275/306(89.9%)，p≈0.73；framework 形态对 现 144(91.7%) vs 取冷 142/141(90.4%/89.8%)，p=0.82/0.65，整组 99/95/94；killer 形态对 现 2615(90.1%) vs 反向 2562(88.2%)（p=0.0272）、整组 1196(41.2%) vs 1074(37.0%)（p≈0）→ **killer 反向锁定显著更差**，按用户「统一马上执行」执行，回滚见末行。
- 改动（7 文件）：`lib/kill3_tail.py`（`cold_dropped_form`/`_replace_at`/`normalize_response(rows)`/`request_prediction(rows)`/`create_seal` 写 `dropped_form_cold`、docstring+RETRY+prompt 任务 3）、`lib/kill_three_view.py`（弃位取冷展示与脚注）、`scripts/run_kill3_tail.py`（传 rows）；`lib/joint_form_optimizer.py`（`killer_reverse_lock`、`optimize_killer_response` 改锁）、`lib/auto_r1_killer.py`（日志/metadata/攻击语）；`lib/hitrate_baseline.py` 与 `scripts/framework_hitrate_cumulative.py`（v16 基线行 + 分界说明）。
- 验证：`/tmp/test_rev_cold.py` 31 项全 PASS（弃位取冷逐位置复算、v16 覆盖/旧路径不覆盖/被杀位仍强校验、RETRY format、seal 字段、killer 锁与 expected_opt 精确复算、framework 反向锁不变、两基线含 v16、v15 旧封存不动）；7 文件 py_compile 全过；真实快照 800 行 `cold_dropped_form` 冒烟 OK（个位取 双/小）。
- 冻结重置：`ops/kill3-freeze.json` → `rule_version` v16、`frozen_at` 2026-11-05T23:59:41+08:00、`freeze_ref_period` 20261105287、version 4、hash_updates +3；`ops/freeze-guards.json` 的 kill3_tail/killer/framework 经 `update`（记录 reason）+ `init --renew`（ref 20261105287）重置窗口，且把 `lib/joint_form_optimizer.py` 新纳入 killer/framework 受控文件清单（此前该文件不在任何冻结守卫内，属补漏）。`freeze_guard.py status` 五框架 changed=−。
- 真实周期（首个 v16 周期，cron 自动运行、未手动触发）：ref=20261105288 / target=20261106001 封存于 00:01:38 发布，`dropped`=个位，`dropped_form_cold`={parity 双, size 小}，`reverse_parity`=单双单双、`reverse_size`=大小小小（弃位字符与独立复算一致）；killer target 20261105289 封存 00:01:16，`reverse_form.lock`={pattern_ds 单双双单, pattern_dx 小小小大}，与 kills k1..k4=8/5/5/2 逐位取反一致（expected_ai 0.4378 → expected_opt 0.3761，符合「反向锁定更差」的预期）；framework 仍为「本地精确联合优化 + 反向约束」，未变。
- 面板（00:01:42 自动刷新）：kill3 卡显示「弃位个位按该位置冷热取双/小（v16 起）」、脚注「弃位字符自 v16 起按该位置冷热取冷」，旧「字符自选」计数 0；killer 卡显示「形态：四位置反向锁定（取杀码形态反面，AI 杀码/合尾杀不变）」；framework 卡文案未变。
- 门禁：`refresh_source_manifest.py --if-drift` → baseline `auto-baseline-20261106-000140`（429 files），manifest_drift OK；`ops_health` overall=warn，非 OK 三项均为已知/预期：kill3_stability（24h version_changes=3 含 v14/v15/v16、missing_slots=2 为历史缺期）、cuopt（无 CUOPT_HEALTH 证据，长期已知）、hitrate_stability（观察 kill1_tail）。
- 备份/回滚：`/root/ops/reverse-cold-unify-20261105-235458/`（改前 7 代码文件 + 两 JSON + SHA256SUMS）。回滚按序 `git revert 4702422 417c472 73b9b91 bec59dd`，并把 `ops/kill3-freeze.json` 的 rule_version/frozen_at/freeze_ref_period 恢复为 v15/2026-11-05T04:44:42/20261105056 后跑 `freeze_guard.py init --framework kill3_tail`（不加 --renew）；或直接从备份目录覆盖 7 文件 + 两 JSON，重跑 freeze_guard status 与 manifest --if-drift。


### kill10 新增：二数合 + 三数合独立框架（2026-11-06，`aee11d9`/`af97953` + 本条 ops 台账）

- 需求：用户 m00001「帮我弄一个框架跑二数合三数合」，m00094「对」确认整组口径与默认值；新建与 kill1/kill3/kill8 并列的独立框架 kill10（口径独立封存/结算/统计/面板）。kill9 已于 2026-11-05 17:55 按用户指令退役，本次从零新建，不复用 kill9 名字与代码。
- 口径（2 项，`kill10-tail-v1-he2-he3`）：二数合 he2 = 前 4 球从 `02468` 中恰留 4 个不同数字，6 个两位组合 (0,1)(0,2)(0,3)(1,2)(1,3)(2,3) 的和值个位任一落入保留集即命中；三数合 he3 = 前 4 球从 0-9 中恰留 5 个不同数字且 2 奇 3 偶 / 3 奇 2 偶，4 个三位组合 (0,1,2)(0,1,3)(0,2,3)(1,2,3) 的和值个位任一落入即命中；整组命中 = 两项同时命中；无连用限制（每期重选）。10^4 开奖空间精确枚举基线：he2 0.98080000、he3 0.93750000、整组 0.92022400（hitrate_baseline / framework_hitrate_cumulative 均登记同一值）。
- 三件套（`aee11d9`）：`lib/kill10_tail.py`（731 行；封存/结算/统计目录 `data/kill10_tail/`；提示词含近 50 期历史、近 10 期二数合/三数合和值个位明细、he2 全 5 候选近 50/近 30 命中率、he3 Top10 与面板上下文；RULE_VERSION `kill10-tail-v1-he2-he3`；模型默认 deepseek-flash，档位优先 `PENTA5_MODEL_KILL10` → `PENTA5_MODEL_KILLER` → `PENTA5_DEEPSEEK_MODEL`；request_policy_version `kill10-high-12k-deadline-v1`）；`lib/kill_ten_view.py`（面板卡 `KILL_TEN_START/END`、2 张保留码卡、攻击语 `二数合<4码>三数合<5码>各` + 复制按钮）；`scripts/run_kill10_tail.py`（每期：结算 → 封存 → 发布面板；锁 `data/.kill10_tail_job.lock`）。
- 挂接 8 文件（`af97953`）：`lib/panel_context.py`（`_model_tiers` 加 PENTA5_MODEL_KILL10、`_kill10_self()`、`_ai_self_frame` kill10_tail 分支、`_ai_self_frames` 加 kill10、`build()` 加 kill10_stats/kill10_previous 与 kill10_self 注入、超限 pop）；`lib/hitrate_baseline.py`（基线 (2, 0.92022400)、标签「AI 二数合+三数合（2 项）」、`_kill10_rows()`）；`scripts/framework_hitrate_cumulative.py`（RULE_BASELINE/LABELS/WINDOWS、`cum_kill10()`）；`lib/model_usage.py`（`data/kill10_tail_cron.log`）；`lib/evidence_backup.py`（JSON_DIRS 加 kill10_tail）；`lib/attack_editor_view.py`（`k10-attack` 定义，version `all-attack-editor-v19-add-kill10`）；`lib/panel_live_refresh.py`（names 加 `KILL_TEN`，version `quiet-panel-v28-add-kill10`）；`scripts/ops_health_check.py`（PROVIDER_402_LOGS、`check_kill10()`、CHECK_FUNCS、FALLBACK_NAMES）。
- 仓库外/部署面（本条 chore）：`ops/crontab.txt` 第 25 行每 5 分钟 `flock -n /var/lock/penta5-kill10t.lock bash -c 'sleep 50 && ... timeout --kill-after=5s 220 python3 -u scripts/run_kill10_tail.py >> data/kill10_tail_cron.log 2>&1'`（`scripts/crontab_tool.sh install` 后 35 jobs，与基线 drift=0）；`/etc/logrotate.d/penta5` 加 `data/kill10_tail_cron.log`（`logrotate -d` rc=0）；`/root/run_stats.py`（仓库外）import `apply_kill_ten_block` 并插入两条 `update_panel` 链，共 3 处 `kill_ten`；`.env` 追加 `PENTA5_MODEL_KILL10=deepseek-flash`（L90，KILL6/KILL8/KILL10 均 deepseek-flash）。
- 挂接点 grep 计数（`kill10`，大小写不敏感）：panel_context 12、hitrate_baseline 7、framework_hitrate_cumulative 9、ops_health_check 13、model_usage 1、evidence_backup 1、attack_editor_view 2、panel_live_refresh 1、ops/crontab.txt 1、/etc/logrotate.d/penta5 1；`/root/run_stats.py` 的 `kill_ten` = 3。三件套自身：kill10_tail 19、kill_ten_view 12、run_kill10_tail 25。
- A/B 与冻结：panel_context 改前/改后（旧=HEAD 模块副本，固定 now fixture）无参数 `build()`+`prompt_text()` 逐字节相同；kill3/kill1 入参路径唯一差异 = `other_ai_self` 多出 kill10 帧（`extra_panel_input` 设计内增项，同 kill9 先例）；`kill10_self` 仅在传入 kill10 参数时出现。`ops/kill3-freeze.json` 更新 panel_context sha（298ed789…）并追加 hash_updates（commit af97953）；`ops/freeze-guards.json` 的 killer/framework/he3_refine/kill1_tail（shared）与 kill3_tail（files）同步新 sha 并各追加 A/B 理由，`freeze_guard.py status` 五框架 changed=-。
- 真实周期（cron 自动触发，未手动跑预测）：首期 ref 20261106013 → target 20261106014（published 2026-11-06T01:05:59.690814+08:00），he2 0268 / he3 01239，attack `二数合0268三数合01239各`，AI_HTTP_PHASE status=200 / elapsed 6.761s，AI_RESPONSE_META requested=returned=deepseek-flash、prompt 3688 / completion 1061 / reasoning 1000 / thinking high；第二期前结算 target 20261106014（开奖 5/8/4/7）：he2_keep 0268 hit、he3_keep 01239 hit、group_hit=true、verified=true；随后封第 2 期 ref 20261106014 → target 20261106015（published 01:11:04.391393），he2 0268 / he3 05679，elapsed 11.544s。
- 面板（01:13 后）：`panel.html` 含 `KILL_TEN_START` 1 次、`k10-card` 3 次（2 卡 + CSS）、data-as-of=20261106014；卡片显示整组 100.00%（1/1 期）、二数合 100.00%（1/1）、三数合 100.00%（1/1）、待核验 1 期、连中 1 期、累计四手连败 0、小样本 σ 27.1pp/54.2pp、口径 kill10-tail-v1-he2-he3 · 自 ref 20261106013 起（本版 2 期）· 整组精确基线 92.02%、攻击语 `二数合0268三数合05679各`。
- 派生统计：`data/hitrate_baseline.json` kill10_tail 行 n=1 / hits=1 / rate=1.0 / expected 0.920224 / p=0.7684 / verdict「样本不足(n<200)」；`data/framework_hitrate_cumulative.json` 行 n=1、need_n_2pp=1335、coverage=1.0、first=last=20261106014；`ops_health` 的 kill10_tail=OK（ref=20261106014 published_at=01:11:04…）。
- 部署与备份：15 文件原子 cp+mv 安装并逐文件 sha256 校验（kill10_tail `3261585b…`、kill_ten_view `d103ccb8…`、run_kill10_tail `2d7590f5…`、panel_context `298ed789…`、hitrate_baseline `b4dfafb8…`、model_usage `ad46b492…`、evidence_backup `a0745a55…`、attack_editor_view `4d0993d7…`、panel_live_refresh `53104afa…`、framework_hitrate_cumulative `50fac245…`、ops_health_check `7d4bdddb…`、crontab.txt `d1b281d4…`、kill3-freeze.json `9c6ee8a4…`、/root/run_stats.py `2354994e…`、/etc/logrotate.d/penta5 `168bb2c0…`；kill10_tail.py 由 CRLF 规范为 LF 后打包）。改前备份 `/root/ops/kill10-20261106/`（11 文件 + crontab.live.txt + baseline.sha256）。
- 门禁：`refresh_source_manifest.py --if-drift` 重建 baseline（`auto-baseline-20261106-011435`，432 files），manifest_drift OK；`ops_health` 的 kill10_tail / kill3_freeze / freeze_guards / manifest_drift / crontab_drift 均 OK；残留非 OK 为历史/其他窗口改动项（kill3_stability warn：v14/v15/v16 24h 内 3 次版本变化 + 2 个历史缺期；hitrate_stability warn：观察 kill3_tail/kill1_tail），与 kill10 无关。
- 回滚：`git revert af97953 aee11d9`（+ 本条 chore），并从 `/root/ops/kill10-20261106/` 覆盖 `lib/panel_context.py`、8 个挂接文件、`ops/{crontab.txt,kill3-freeze.json,freeze-guards.json}`、`/root/run_stats.py`、`/etc/logrotate.d/penta5`，删除 `.env` 的 `PENTA5_MODEL_KILL10` 行与 `data/kill10_tail/`，再 `scripts/crontab_tool.sh install` 重装 crontab 并等下一期面板刷新。

### kill3 v16 稳定性告警降噪 + 累计行刷新滞后说明（2026-11-06 01:20，`e83bf0e`/`d2170e6`）

- 现象：用户 00:46 面板截图 kill3 卡「本版累计 28.57%（2/7 期）」与今日行「3/9」不一致，疑数据丢失；同时 ops_health overall=fail（hitrate_stability FAIL：`断崖/清零: kill3_tail(口径清零风险: 现行口径仅存活 0.5h / 6 期)`）。
- 根因（非数据/口径损坏）：①累计行数据源是 `data/hitrate_stability.json`，由 cron `13,28,43,58 * * * *`（每 15 分钟）生成；面板自身在每期结算后重绘、kill3 每 5 分钟 cron 也会重绘，故累计行最多滞后 1–3 期。00:46 截图对应 00:43 那版报告（2/7），00:58 报告为 3/10，01:03 面板已自动变 3/10。②v16 于 2026-11-06 00:01 首封存，24h 内 churn 护栏按设计报「口径清零风险」，而 v16 未登记白名单 → alert → ops_health FAIL（假警报）。
- 改动：`ops/hitrate_stability_whitelist.json` 追加 kill3_tail / `kill3-tail-v16-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid-cold-drop` / until=2026-11-07T02:00:00+08:00 条目（reason：v16 弃位取冷有意升级、00:01 首封存/00:13 首次观测、24h 后阈值自愈；与 kill1 条目同一机制：按 key+rule 精确匹配，仅把过渡期 churn 降 alert→watch，不抑制断崖/陡降护栏）→ `e83bf0e`。`lib/kill_three_view.py` 累计行追加只读脚注「数据每 15 分钟刷新，最多滞后约 4 期（约 20 分钟；今日行按业务日实时）」；`scripts/framework_hitrate_cumulative.py` 的 `DATA_LAG_NOTE` 由「差 ≤1 期」校正为「每 15 分钟生成一次，差 1–3 期」→ `d2170e6`。冻结受控文件按门禁重算 hash（kill3_tail `lib/kill_three_view.py` b4a30a9b→ca45d40b，`ops/freeze-guards.json` 与 `ops/kill3-freeze.json` 各记 hash_updates；只读展示，不重置 frozen_at）。
- 验证：手动重跑 `scripts/hitrate_stability.py` → kill3_tail `ALERT→WATCH`（reason 追加「已登记有意升版，过渡期降级观察」），summary `status fail→warn`（alerts=0，watch=2：kill3+kill1），whitelisted 两条，as_of=20261106014；`ops_health` 的 hitrate_stability `FAIL→WARN`、overall `fail→warn`（freeze_guards ok 变更 0；manifest_drift 由 01:18 基线刷新恢复 ok）。`_cumulative_html()` 渲染冒烟：仅新增脚注一句，其余字段逐字节相同。
- 说明：今日行（05:00 业务日、实时）与本版累计行（跨天、15 分钟报告）n 天然差 1–3 期，两行不可互校（技能口径）；本次不改任何选码/尾码/封存/结算/基线。只读审计（audit-v16-selfcheck）独立复算：15/15 seal 符合口径、14/14 结算全字段一致、三数组与 state 15/15 对应无错位；滞后由 stability cron 固定晚 1 期叠加面板 5 分钟节拍造成，最大观测 4 期（约 20 分钟），非结算错误。
- 回滚：`git revert d2170e6 e83bf0e`；随后按冻结门禁把 `ops/kill3-freeze.json` / `ops/freeze-guards.json` 的 kill_three_view sha 恢复 b4a30a9b… 并跑 `freeze_guard.py status`；改前备份 `/root/ops/kill3-lag-note-20261106-011952/`（2 源文件 + kill3-freeze.json.bak）。之后 `refresh_source_manifest.py --if-drift`。

### kill3 AI 调用层修复：max_tokens 12288→16384 + 失败响应遥测（2026-11-06，`91f10f4` + `6aedbe4`）

- 依据：T2 只读审计（近 24h 284 周期、311 次付费尝试）：284/284 全部 published/validated，0 最终失败/0 5xx/0 超时；26 次 `ValueError` 内容校验失败（9.1%/周期），其中 11 次耗时 60–77s，且成功样本出现 3 次 completion=12057–12258（99.8%×12288）→ 高度疑似 reasoning 占满 12288 上限导致响应截断、靠重试救回；失败 attempt 的 `detail` 此前不落日志，无法区分截断与格式错误。
- 改动（`91f10f4`，`lib/kill3_tail.py` +18/−4）：①`MAX_TOKENS` 12288→16384；②`request_policy_version` `kill3-high-12k-deadline-v1`→`kill3-high-16k-deadline-v1`；③rejection 日志追加 `detail=<str(error)[:100]>` 与失败响应的 `finish_reason` / `completion_tokens` / `reasoning_tokens`（仅安全数值），后续可直接区分 `finish_reason=length` 截断与格式错误。
- 不变项与理由：`MIN_REQUEST_SECONDS` 保持 20.0（提到 90s 会把「启动晚」变成少发一期，缺期风险大于晚发风险）；`timeout` 上限保持 `max(10,min(90,remaining-10))`；模型档位保持 deepseek-flash（审计结论：requested=returned=flash 286/286、finish_reason 全 stop，pro 无收益证据且更慢/402 风险更高）。
- 验证：`py_compile` OK；离线冒烟 `/tmp/k3logtest.py`（monkeypatch `_post`）三类路径均按新格式落日志——`finish_reason=length` 用例输出 `...; detail=响应未正常结束; finish_reason=length; completion_tokens=12288; reasoning_tokens=12180; not published.`，格式错误用例输出 `detail=响应中未找到 JSON 对象; finish_reason=stop; ...`；Provider 小 prompt 实测 deepseek-flash 对 max_tokens=16384 与 32768 均 HTTP 200、usage 正常，故 16384 可接受。
- 冻结：`freeze_guard.py update` 登记 kill3_tail `lib/kill3_tail.py` aefe3d0b…→3bb8d3c0…（`6aedbe4`）；`ops/kill3-freeze.json` 同步 files sha + hash_updates 追加第 56 条（len=56）；`freeze_guard.py status` 五框架 changed=−。
- 备份/回滚：`/root/ops/kill3-ai-16k-20261106-0135/lib__kill3_tail.py`（改前 sha aefe3d0b…）；回滚 `git revert 6aedbe4 91f10f4` 后重跑 `python3 scripts/freeze_guard.py update --framework kill3_tail --file lib/kill3_tail.py`，或直接用备份覆盖并回填 guards/legacy sha。
- 监控（待验证）：面板累计行脚注「最多滞后约 4 期」应在下一个 kill3 周期后上屏；后续 2–3 期观察 rejection 日志是否出现 `finish_reason=length`、elapsed p90 是否下降、超时/失败是否上升，异常则按备份回滚。

### kill1 面板卡展示层补丁：累计行 as_of/滞后、本版期数含待核验、缺期槽披露（2026-11-06，`3928131` + `1d48d75`）

- 依据：kill1 卡只读审计（技能 audit-panel-runtime-data-reads + single-position-kill-tail-form-recommendation）：卡面逐项读数与原始账本一致（整组 130/182=71.43%、位置 321/364=88.19%、合尾 169/182=92.86%、待核验 1、连败 3、四手连败 0），无 P0/P1；仅三项展示层歧义：①累计块（`data/hitrate_stability.json`）未标 as_of 且滞后 2 期（stability 15 分钟节拍）；②「本版 183 期」为 published seals 计数、含 1 期待核验，易与 182（已核验）/180（累计快照）误读；③v4 历史缺槽 20261105137（重试指令花括号未转义 → KeyError '"key"' 整期跳过，commit 11b81c1 已修复）只在 hitrate-stability 脚注披露，kill1 卡未披露。
- 改动（`3928131`，`lib/kill_one_view.py` +61/−8，仅展示层）：①累计行新增「数据截至 <report.as_of_period>（每 15 分钟刷新，最多滞后约 4 期/约 20 分钟；今日行按业务日实时）」，与 kill3 卡同款滞后口径；②`_version_html(stats)` 期数改为「本版 N 期，含待核验 P 期」（P 取 `today_stats.pending`，与顶部待核验同源）；③新增只读辅助 `_missing_version_targets(rule)`：按已发布封存 ref 序列（`next_period`）推算当前 rule 缺槽，卡面披露「缺期 1 槽（20261105137），不计入累计 n、不按 0 命中」。
- 验证：`py_compile` OK；改前/改后同进程渲染 diff 仅 2 处（累计行插入 as_of 段、版本行插入「含待核验 1 期」与「缺期 1 槽…」），其余逐字节相同；旧/新渲染 1.319s→1.582s（+0.263s，新增一次 load_seals 0.195s）；`_missing_version_targets` 返回 ['20261105137']（0.202s）；`_version_html({'pending':1})` 出现「含待核验 1 期」、`{'pending':0}`/None 不出现；`<section>` 开闭各 1。
- 冻结：`freeze_guard.py update` 登记 kill1_tail `lib/kill_one_view.py` 13c4e049…→b35f339a…（`1d48d75`）；`freeze_guard.py status` 五框架 changed=−（kill1 190/100 期、rule kill1-tail-v4-two-position-one-tail）。
- 备份/回滚：改前备份 `/root/ops/kill1-audit-note-20261106-0205/`（`kill_one_view.py` sha 13c4e049… + 改前 `freeze-guards.json`）；回滚 `git revert 1d48d75 3928131`，再跑 `python3 scripts/freeze_guard.py update --framework kill1_tail --file lib/kill_one_view.py --reason rollback` 并按备份回填 guards sha；之后 `refresh_source_manifest.py --if-drift`。
- 监控（待验证）：下一个 kill1 周期（cron `*/5` 开奖后 sleep 35s）由 `run_kill1_tail.py::publish_block()` 重写卡片后，`panel.html` 应出现「数据截至 202611060xx」「缺期 1 槽（20261105137）」；下 2–3 期核对 as_of 随稳定性报告前进、待核验期数与顶部一致。

### kill11 新增：单位置杀码 + 单双反向形态 + 一合尾独立框架（2026-11-06，`22eb650`/`f1d04f5` + 本条 ops 台账）

- 需求：用户 m00001「搞一个框架排除一个定位，一个单双形态，一个合尾」；口径四问已确认（ask_user_question 全推荐）：命名 kill11、形态只做单双（不做大小）、合尾 hold3-until-miss、位置/杀码不做连用限制。新建与 kill1/kill3/kill10 并列的独立框架（口径独立封存/结算/统计/面板）。
- 口径（3 项，`kill11-tail-v1-one-position-one-form`）：定位杀码 = 前 4 球 4 位置选 1 杀 1 码，命中=该位实际开奖号≠杀码；单双反向形态 = 4 字符仅 {单,双}（千百十个顺序），被杀位置字符必须取杀码单双反面（奇→双、偶→单），其余 3 位由 AI 自选，命中=实际四位单双串≠封存串（合法形态 8 种）；合尾 = 五种组合（千+百+十+个 `tail_all` / 千百十 `tail_qbs` / 千百个 `tail_qbg` / 千十个 `tail_qsg` / 百十个 `tail_bsg`）选 1 杀 1 个和值个位，命中=实际个位≠杀尾；整组命中=3 项同时命中；只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；只用前 4 球。10^4 开奖空间 × 16000 配置精确枚举基线：position 0.90000000、form 0.93750000、tail 0.90000000、整组 120,600,000/160,000,000=0.75375000（hitrate_baseline / framework_hitrate_cumulative 均登记 4 位 0.7538）。
- 三件套（`22eb650`）：`lib/kill11_tail.py`（48082B；封存/结算/统计目录 `data/kill11_tail/`；提示词含近 50 期历史、单双形态约束、五合尾候选、面板上下文；RULE_VERSION `kill11-tail-v1-one-position-one-form`；模型默认 deepseek-flash，档位优先 `PENTA5_MODEL_KILL11` → `PENTA5_MODEL_KILLER` → `PENTA5_DEEPSEEK_MODEL`；request_policy_version `kill11-high-12k-deadline-v1`；尾码 `hold3-until-miss`（max 3））；`lib/kill_eleven_view.py`（面板卡 `KILL_ELEVEN_START/END`、位置/形态/合尾三卡 + 攻击语 `kill11-attack-text/copy/data`）；`scripts/run_kill11_tail.py`（每期：结算 → 封存 → 发布面板；锁 `data/.kill11_tail_job.lock`）。
- 挂接 10 文件（`f1d04f5`）：`lib/panel_context.py`（`_model_tiers` 加 PENTA5_MODEL_KILL11、`_kill11_self()`、`_ai_self_frame` kill11_tail 分支（counted/form_type/form/tail_policy）、`_ai_self_frames` 加 kill11、`build()` 加 kill11_stats/kill11_previous 与 kill11_self 注入、超限 pop）；`lib/hitrate_baseline.py`（基线 (3, 0.7538)、标签「AI 单位置杀码 + 单双形态（3 项）」、`_kill11_rows()`、build rows.extend）；`scripts/framework_hitrate_cumulative.py`（RULE_BASELINE/LABELS/WINDOWS、`cum_kill11()`、build 循环）；`lib/model_usage.py`（`data/kill11_tail_cron.log`）；`lib/evidence_backup.py`（JSON_DIRS 加 kill11_tail）；`lib/attack_editor_view.py`（`kill11-attack` 定义，version `all-attack-editor-v20-add-kill11`）；`lib/panel_live_refresh.py`（names 加 `KILL_ELEVEN`，version `quiet-panel-v29-add-kill11`）；`scripts/ops_health_check.py`（PROVIDER_402_LOGS、`check_kill11()`、CHECK_FUNCS、FALLBACK_NAMES）；`scripts/tail_hold_review.py`（`_kill11_entries()`、FRAMEWORKS 加 kill11 行）；`/root/run_stats.py`（仓库外）import `apply_kill_eleven_block` 并插入两条 `update_panel` 链（`kill_eleven`=4）。
- 仓库外/部署面（本条 chore）：`ops/crontab.txt` 每 5 分钟 `flock -n /var/lock/penta5-kill11t.lock bash -c 'sleep 40 && ... timeout --kill-after=5s 220 python3 -u scripts/run_kill11_tail.py >> data/kill11_tail_cron.log 2>&1'`（`scripts/crontab_tool.sh install` 后 36 jobs，与基线 drift=0）；`/etc/logrotate.d/penta5` 加 `data/kill11_tail_cron.log`（`logrotate -d` rc=0）；`.env` L91 追加 `PENTA5_MODEL_KILL11=deepseek-flash`。
- 挂接点 grep 计数（`kill11`，大小写不敏感）：panel_context 20、hitrate_baseline 10、framework_hitrate_cumulative 13、ops_health_check 14、model_usage 2、evidence_backup 1、attack_editor_view 5、panel_live_refresh 1、tail_hold_review 5、ops/crontab.txt 3、/etc/logrotate.d/penta5 1、.env 1；`/root/run_stats.py` 的 `kill_eleven`=4；三件套自身：kill11_tail 18、kill_eleven_view 38、run_kill11_tail 30。
- A/B 与冻结：panel_context 改前/改后（旧=HEAD 模块副本，固定 now fixture；A/B v2 脚本 `/root/ops/kill11-deploy/ab_panel_context_v2.py`）`AB_RESULT {"pass": 7, "fail": 0}`：default/kill1/kill10/rich 四场景唯一差异 = `model_tiers` 多 `kill11: deepseek-flash`（rich 另有 `other_ai_self` 多 kill11 帧，`extra_panel_input` 设计内增项，同 kill9/kill10 先例），其余键逐字节相同、prompt 去掉 model_tiers 后逐字节相同；`kill11_self` 仅在传入 kill11 参数时出现；kill3 RULE_VERSION v16 与口径未变。`ops/kill3-freeze.json` 更新 panel_context sha 298ed789…→243d7e32… 并追加第 57 条 hash_updates（commit f1d04f5）；`ops/freeze-guards.json` 的 killer/framework/he3_refine/kill1_tail（shared）与 kill3_tail（files）同步新 sha 并各追加条目 + `updated_at=2026-11-06T03:43:35+08:00`；`freeze_guard.py status` 五框架 changed=-。
- 真实周期（cron 自然触发，未手动跑预测）：首期 03:45:40 运行 → 03:45:51 发布 seal ref=20261106045 target=20261106046（十位杀5 · 千百十个合尾杀4 · 形态 单双双单），AI 8.67s、deepseek-flash、finish_reason=stop；次期 03:50:41 结算 20261106045：实际 [2,9,6,9]，十位杀5 实际6 命中、tail_all 杀4 实际和值 26 个位6 命中、形态封存 单双双单 vs 实际 双单双单 命中 → `group_hit/verified=true`；同时发布 seal 20261106046 target=20261106047（十位杀6 · 千百十个合尾杀4 沿用第1期（上期命中）· 形态 双单单双），AI 14.16s。03:53 面板 data-as-of=20261106046。 第三个运行周期 03:55:41 结算 20261106046（实际 [4,2,6,2]）：百位杀1 等三项未全中——position s 杀6 实际6 未中、tail_all 杀4 实际和值14个位4 未中、形态 双单单双 vs 实际 双双双双 命中，故 group_hit=false；按 hold3-until-miss 规则换码，03:56 发布 20261106047→048（百位杀1 · 千百十合尾 杀9 · 形态 单双单双），settlements/seals 均落盘 verified。
- 面板（03:53）：`panel.html` 的 `KILL_ELEVEN_START/END` 各 1；卡显示参考期 20261106046/目标期 20261106047、整组命中率 100.00%（1/1 期）、位置杀码 100%（1/1 注）、单双反向形态 100%（1/1）、合尾 100%（1/1）、待核验 1 期、连中 1 期、四手连败 0、口径 `kill11-tail-v1-one-position-one-form` · 自 ref 20261106045 起（本版 2 期，含待核验 1）· 整组精确基线 75.38%，攻击语「千1234567890百1234567890十123457890个1234567890四定千百十个合123567890除双单单双」。
- 派生统计：`data/hitrate_baseline.json` kill11_tail 行 n=1/hits=1/rate=1.0/expected 0.7538/p=0.567661/verdict「样本不足(n<200)」；`data/framework_hitrate_cumulative.json` 行 n=1/need_n_2pp=3579/ci95 [0.206543,1.0]/coverage=1.0/first=last=20261106046；`data/model_usage/summary.json` kill11 attempts=2/responses=2/errors=0（prompt 8065 / completion 4029 / cache_hit 768），sources logs=7、collection ok events_added=2；`ops_health` kill11_tail=OK ref=20261106046 published_at 03:50:56。
- 部署与备份：三件套 sha256（kill11_tail eadeae06…、kill_eleven_view 6b4ec9cc…、run_kill11_tail 0faa3e70…）；13 处补丁 03:39:41 正式 apply（13/13 PATCH，before=0→after 19/3/9/12/14/2/1/5/1/5/3/1/1），备份 `/root/ops/kill11-apply-20261106-033941`（SHA256SUMS+manifest.json，含仓库外 run_stats.py/logrotate/.env）；pre-patch panel_context 备份 `/root/ops/kill11-deploy/panel_context.py.bak`（298ed789…）；post-patch sha256：panel_context 243d7e32…、hitrate_baseline 4505aa62…、framework_hitrate_cumulative d74c4268…、model_usage 4699389d…、evidence_backup b819b8c7…、attack_editor_view 63955405…、panel_live_refresh 5b7b9d64…、ops_health_check fb7518ec…、tail_hold_review ebc9fed3…、crontab.txt d6f8c8de…、`/root/run_stats.py` 44cb8142…、`/etc/logrotate.d/penta5` 149ac76f…、`.env` 2b0ba741…；22 项行为自测 VERIFY PASS 22 FAIL 0（/tmp/kill11-verify）；freeze 备份 `/root/ops/kill11-freeze-bak-20261106T034335+0800`；crontab 旧版备份 `/tmp/crontab.backup.20261106_034202`。
- 门禁：`refresh_source_manifest.py --root /root/penta5-runner --if-drift` 重建 baseline `auto-baseline-20261106-034353`（435 files），manifest_drift OK；`ops_health` overall=WARN（kill11_tail 已 OK；残留非 OK 为历史项 kill3_stability warn 24h version_changes=2、hitrate_stability warn 观察 kill3_tail/kill1_tail），freeze_guards OK、crontab_drift OK（36 jobs）。
- 回滚：`git revert <本条 chore> f1d04f5 22eb650`；从 `/root/ops/kill11-apply-20261106-033941` 覆盖 13 个被打补丁文件（仓库内 `tree/` + 仓库外 `external/`，含 /root/run_stats.py、/etc/logrotate.d/penta5、.env），删除 `.env` 的 PENTA5_MODEL_KILL11 行与 `data/kill11_tail/`；删 `ops/crontab.txt` kill11t 行后 `scripts/crontab_tool.sh install`；freeze 用 `/root/ops/kill11-freeze-bak-20261106T034335+0800/` 覆盖两 JSON 后跑 `freeze_guard.py status`；最后 `refresh_source_manifest.py --if-drift`。

#### kill11 上线后收尾完善（2026-11-06 06:03，本条 commit，接 22eb650/f1d04f5）

- 只读审计（39 项）结论：封存/结算逐条复算（FORM_OK 25、SETTLEMENT_BAD 0）、攻击语构造（与 kill1 build_attack 同构）、hold3-until-miss（TAIL_HOLD_BAD 0）、面板今日/累计独立复算一致、派生统计与 10 处挂接（含 evidence_backup/hitrate 页/live refresh/attack editor/ops_health/tail_hold_review）均正常，未发现功能性缺陷；唯一瑕疵为统计标签漏「一合尾」。
- 标签统一：`lib/hitrate_baseline.py:71` 与 `scripts/framework_hitrate_cumulative.py:77` 的「AI 单位置杀码 + 单双形态（3 项）」→「AI 单位置杀码 + 单双形态 + 一合尾（3 项）」，与面板标题、攻击语编辑器标签、ops_health 文案一致。两文件均未在 freeze 注册（ops/kill3-freeze.json 仅在 reason 字符串提及），无需 freeze 更新。
- 备份/回滚：改前备份 `/root/ops/kill11-polish-20261106-060133/`（`hitrate_baseline.py` sha 4505aa62…→da6e832d…、`framework_hitrate_cumulative.py` d74c4268…→fdfb4a82…，各替换 1 处、临时文件 + os.replace 原子写、py_compile OK）。回滚用 `cp -a` 将两文件覆盖回备份，再跑 `refresh_source_manifest.py --if-drift` 与一次 `ops_health`。
- 重生成派生统计（全部自洽）：`data/hitrate_baseline.json`（as_of 20261106072，kill11 n=13 hits=10=76.92%）、`data/framework_hitrate_cumulative.json` + `hitrate.html`（rolling kill11 24h coverage 100%/online 26/plan 73.08%、last_missed None）、`data/hitrate_stability.json`（n=27/w100 74.1%/cov24 100%/ok）、`data/hitrate_guard.json`（status ok，hitrate_non_ok []）、`data/tail_hold_review.json`（kill11 行首次落地：27 期、尾码 24/27=88.89%、换码 13/27=48.1%；独立复算逐项一致，报告 `/root/ops/tail-hold-review/review-20261106-060209.md`）。
- 验证：4 个派生 JSON + hitrate.html 中旧标签命中 0；manifest `refresh_source_manifest.py --if-drift` 后 no-drift；`ops_health` kill11_tail OK、无新增 NONOK（历史 kill3_stability/hitrate_stability WARN 不变）。

### kill11 运行完善：封存账本容量预警覆盖（2026-11-06，scripts/ops_health_check.py）

**背景与差距分析（与 kill1/kill10 逐项对齐）**

- 已确认无差距的运行面：AI 调用 3 次重试与 NONRETRYABLE 遥测、ops_health 框架检查（check_kill11 与 check_kill1/check_kill10 同构）、hitrate_guard（drift rows 已含 kill11_tail，脚本无硬编码框架表）、hitrate_baseline、framework_hitrate_cumulative、tail_hold_review（每日 22:35）、model_usage、evidence_backup、attack_editor_view、panel_live_refresh、coverage（MIN_ONLINE=100）、freeze。
- model_tier_guard 的 targets 只有 KILL1/KILL3 属设计；面板手动启动路由只有 kill8 有，kill1/kill10 都无，均非 kill11 缺项。
- 唯一真实差距：`scripts/ops_health_check.py` 的 `check_ledger_size` 只对 `he3_refine_seals.json` 套 110/120MiB 阈值，其它账本只记录体积且恒 OK。而 128MiB 是 `json.load` 直读的实际上限，kill1/kill10/kill11 封存账本在逼近该上限前没有任何预警。

**账本实测（2026-11-06 06:19）**

| 账本 | 体积 | 条数 | 单条体积 | 增长推算 |
| --- | --- | --- | --- | --- |
| data/kill1_tail/seals.json | 14,040,585B | 761 | 约 18.4KB | 约 5.3MB/天 |
| data/kill10_tail/seals.json | 950,478B | 64 | 约 14.8KB | 约 4.3MB/天 |
| data/kill11_tail/seals.json | 545,710B | 32 | 约 17.1KB | 约 4.9MB/天，约 22-24 天逼近 110MiB |

- 结构为 `{"version": ..., "records": [...]}`，其中 panel_context 占 7-8KB/条、kill11 另有 tail_policy 约 295B/条。
- 计数注意：不能用 `grep -o ref_period` 统计期数，会被 `tail_policy.previous_ref_period` 重复计数，必须按 records 列表长度。

**改动（只读监控，不影响封存/结算/口径）**

- `LEDGER_SIZE_FILES` 增加 `kill1_tail/seals.json`、`kill10_tail/seals.json`、`kill11_tail/seals.json`。
- 新增 `LEDGER_ALERT_FILES` 映射（he3/kill1/kill10/kill11 封存账本 → 中文标签）；`check_ledger_size` 改为按该映射统一套 110MiB WARN / 120MiB FAIL，告警文案通用化；`he3_refine_hit_stats.json` 保持仅记体积（其热文件已由滚动归档控制）。
- 未改动任何阈值常量数值与原 he3 语义。

**验证**

- `sha256: fb7518ece6b5419a185a88ef7db79a72e1bc50d9132b210376fc9c6bbf15dd87 -> a41279a309adc80fa821627326ef2839d993d6fea2035561303ed6899d8b6033`，py_compile OK。
- 手动 `python3 scripts/ops_health_check.py --root /root/penta5-runner`：`ledger_size` 明细 5 条，kill1_tail 13.4MiB / kill10_tail 943.1KiB / kill11_tail 549.8KiB 均带「阈值 110/120MiB」；`kill11_tail OK ref=20261106077 published_at=2026-11-06T06:26:06`。
- 首跑 manifest_drift 为 FAIL（另一 DSH 窗口在途 `M lib/kill8_tail.py`），按流程 `refresh_source_manifest.py --if-drift` 生成 `/root/backups/auto-baseline-20261106-062847`（440 files；包含该在途改动内容，但其未进入本提交）→ 复跑 `manifest_drift OK`、overall=WARN，仅历史 `hitrate_stability` 观察 kill3_tail/kill1_tail。

**备份与回滚**

- 备份：`/root/ops/kill11-run-polish-20261106T062701/ops_health_check.py.bak`
- 回滚：`cp -a /root/ops/kill11-run-polish-20261106T062701/ops_health_check.py.bak scripts/ops_health_check.py && python3 -m py_compile scripts/ops_health_check.py && python3 scripts/refresh_source_manifest.py --root /root/penta5-runner --backup-base /root/backups --if-drift`；或 `git revert` 本提交。

**后续（本次不做）**：kill1/kill10/kill11 封存账本尚未接入滚动归档（`lib/seal_archive.py` 的 PERIOD_DICT_KEYS 只注册 he3 两个账本，读取链路仍是 `json.load` 直读），若对账本归档会破坏读取；待账本继续增长时，需先把读取链路改为 `read_full` 再注册归档与 cron。

- 收尾复验（2026-11-06 06:31:37）：并发 DSH 窗口在途改动（`M lib/kill8_tail.py`、`M lib/hitrate_baseline.py`、`M lib/kill_eight_view.py`）使 manifest 再次漂移，再刷 `/root/backups/auto-baseline-20261106-063136`（440 files）后 `manifest_drift OK`；`overall=WARN` 仅历史 `hitrate_stability` 观察，`kill11_tail OK ref=20261106078`、`ledger_size OK`（kill11 seals 566.6KiB）。该三处并发改动均未进入本提交。


### 冷码窗口回短：100/80 → 70/50（2026-11-06 06:40，`2131a39`/`e87d54a`/`58f3f76` + 本条 ops 台账）

- 需求：用户（m00002）「100期窗口过长了，。50 70都够用了」——把「冷码杀号战绩」与「位置冷码」两张卡的滚动窗口 100/80 改短为 70/50。
- 口径决策：冷码观察同批由 100 缩为 70 期（`WINDOW_SIZE=70`）。用户问卷 Q2 点的是 50，但 50 期与「位置冷码 50 期·第1冷」同窗同排序（同为 `_group` 次数升序/遗漏降序，四位置会选出同一批号码、面板重复），且 Q1 自定义「都取第一个」指向长窗 70；如需 50 只需改 `lib/cold_observation.py` 的 `WINDOW_SIZE` 与 `REVISION` 两行。
- 改动（4 文件，3 个独立提交）：
  - `2131a39` `scripts/cold_hit_stats.py:27` `WINDOWS = (100, 80)` → `(70, 50)`（samples 730 / 750）。
  - `e87d54a` `scripts/position_cold_report.py:27-30` `WINDOWS=(100,80)` → `(70,50)`、`RANK_BY_WINDOW={100: 2, 80: 1}` → `{70: 2, 50: 1}`（长窗第 2 冷 / 短窗第 1 冷保持不变，注释同步）。
  - `58f3f76` `lib/cold_observation.py:11-12` `REVISION` → `cold-observation-v6-70-crossday`、`WINDOW_SIZE=70`（docstring 同步）；`lib/cold_observation_view.py` 同 revision、`_integer(maximum=WINDOW_SIZE)`、`window_size != WINDOW_SIZE` 校验与兜底、标题/meta/note 全部由 `WINDOW_SIZE` 派生（不再硬编码 100）。
- 未动：`scripts/cold_backtest.py:27 WINDOWS=(30,20)`（不在面板显示）；kill1/kill11 卡「窗口 50 期 · 关盘按开奖槽位倒推 70 秒」属运行窗口，与滚动统计窗口无关。
- 验证（真实开奖周期 20261106079）：`systemctl restart penta5-position-cold.service` 后 06:34:03 active、首轮 rc=0；`data/cold_hit_stats.json` windows=[(70,730),(50,750)]；`data/position_cold_report.json` windows=[(70, 第2冷, 20261106010~079),(50, 第1冷, 20261106030~079)]；`data/cold_observation.json` revision=v6-70-crossday、window_size=70、sample_count=70、first=20261106010、last/ref=20261106079。panel.html 06:36:34 自动重建后含「窗口 70 期 · 共 730 期样本」「窗口 50 期 · 共 750 期样本」「最近 70 期 · 第2冷 · 20261106010~20261106079」「最近 50 期 · 第1冷 · 20261106030~20261106079」「冷码观察 · 近70期每项第1名」「跨日滚动窗口（每日 05:00 不清零） · 实际 70 期，最多70期」；旧 100/80、近100期标签计数均为 0。冷码块不进 AI 上下文（`extra_panel_input` 默认 False），无预测语义变更。
- sha256（改后）：`scripts/cold_hit_stats.py` 945da7a762c378fe4044d9ab22321fa539b38cd51a92c32ad1e4bef30fdbee86；`scripts/position_cold_report.py` efaf516f6036b56ff783bcbcdd18d5f1534da40f575d5592fd4ae257ad8f3851；`lib/cold_observation.py` eb56351b90c7b1968427c2f2c311dd4198f89b1a7d69af3e4eff5f40e00b3b1e；`lib/cold_observation_view.py` a25665e9e795b4c1f27cb908b474e90c6fa87fa4af8378cfc3cd61a0779721ca。
- 备份/回滚：改前备份 `/tmp/*.bak-20261106-063350`（cold_hit_stats / position_cold_report / cold_observation / cold_observation_view 四个文件）；回滚 `git revert 58f3f76 e87d54a 2131a39` + `systemctl restart penta5-position-cold.service`（JSON 下期重算、面板下个结算周期自动恢复），或按备份覆盖后重启。
- 并行窗口：`M lib/kill8_tail.py`、`M lib/hitrate_baseline.py`、`M lib/kill_eight_view.py` 三处在途改动均未进入本组提交。
- 收尾复验（2026-11-06 06:38）：`python3 scripts/refresh_source_manifest.py --root /root/penta5-runner --if-drift` → 新基线 `/root/backups/auto-baseline-20261106-063810` 后 `manifest_drift OK`；`python3 scripts/ops_health_check.py --root /root/penta5-runner` 19 项 `overall=WARN`，唯一 WARN 为历史 `hitrate_stability`（as_of=20261106076 · age=10.3min · 观察 kill3_tail/kill1_tail），其余 OK（panel_html mtime 06:38:19、kill11 seals 583.4KiB、余额 58.87 CNY）。并发窗口三处在途改动仍未进入本组提交。

### kill11 运行完善部署落地（2026-11-06，commit 2e035d6）

- 落地对象：`scripts/ops_health_check.py` 封存账本容量监控扩展（LEDGER_SIZE_FILES 增加 kill1/kill10/kill11 封存账本；新增 LEDGER_ALERT_FILES 标签；对 4 个封存账本统一套 110MiB WARN / 120MiB FAIL 阈值，其余账本仅记体积）与 `ops/README.md` 运行完善台账；两者同属 commit `2e035d6`（chore(ops): 封存账本容量监控扩展到 kill1/kill10/kill11）。
- 提交=磁盘：磁盘 `scripts/ops_health_check.py` sha256 `a41279a3...` 与 `git show HEAD:scripts/ops_health_check.py` 完全一致；文件 89709B、mtime 2026-11-06 06:27。
- 无需重启：cron 每期新起 python 解释器直读磁盘，改动即生效。证据：cron 自身在 06:30:02 与 06:40:01 两次运行的日志已打印补丁后的 `ledger_size` 行（5 个账本：he3_refine_seals 41.4MiB、he3_refine_hit_stats 34.7MiB、kill1_tail/seals 13.4MiB、kill10_tail/seals 943.1→973.0KiB、kill11_tail/seals 549.8→583.4KiB，封存账本均带「阈值 110/120MiB」）；06:40:16 由 cron 生成的 `data/ops_health.json` 中 `ledger_size` 为 ok。
- 面板链路：pm2 `penta5-panel-sync` 直读磁盘 `panel.html`（`penta5_backend_runner.js:15226` 为 `return path.join(__dirname, "panel.html")`），3010 由 node 监听、nginx `/etc/nginx/conf.d/penta5.conf` 由 3001 转发；落地时 `data-as-of=20261106078`、版本串 `all-attack-editor-v20-add-kill11`、KILL_ELEVEN 区块计数 1。
- 定时与进程：`crontab -l` 与版本化基线 `ops/crontab.txt` diff 一致（40 行）；pm2 10/10 online（penta5-panel-sync 重启 3696 次、uptime 6D；penta5-runner uptime 4D）。
- 并发说明：另一 DSH 窗口在本批落地期间并行修改 `lib/hitrate_baseline.py`、`lib/kill8_tail.py`、`lib/kill_eight_view.py` 并推进冷码窗口提交（2131a39 / e87d54a / 58f3f76），不在本批落地范围，未进入本批提交；manifest 刷新按当时磁盘快照取基线，期间其继续编辑可能导致 manifest_drift 短暂 FAIL，属并发窗口行为。
- 备份/回滚：备份 `/root/ops/kill11-run-polish-20261106T062701/ops_health_check.py.bak`；回滚 `cp -a` 回 `scripts/ops_health_check.py` 后跑 `python3 scripts/refresh_source_manifest.py --root /root/penta5-runner --backup-base /root/backups --if-drift`，或 `git revert 2e035d6`。
