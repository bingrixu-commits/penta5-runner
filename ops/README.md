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
