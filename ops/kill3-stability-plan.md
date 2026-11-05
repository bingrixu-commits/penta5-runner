# kill3 稳定方案（2026-10-31 起冻结）

本文件是 AI 三位置杀码 + 一合尾 + 两反向形态框架的稳定性执行方案，回答「怎么让线上的口径和数字稳定、可解释、可回滚」。

## 0. 冻结声明

- 冻结点：kill3-tail-v10-two-counted-free（3 个被杀位置全部展示、其中 2 个计入整组与攻击语、1 个 display_only 仅展示；整组 5 项 = 2 位置 + 1 合尾 + 2 反向形态；位置 2 注/期；尾码与位置/弃位均由模型自主决定，无 hold3、无连用期数上限）。
- 冻结期：72 小时或 ≥100 期，先到为准（自 2026-11-01 04:10:43 起算；2026-10-31 21:00–2026-11-01 04:10 的 v8 冻结期因用户指令变更提前结束，v8 封存只读保留）。
- 冻结期内禁止任何影响选码 / 计数口径 / 尾码策略 / 统计口径的改动；只允许不改变输出分布的 bug 修复与文案修正，且必须登记「不改变封存内容」的验证。
- **2026-11-01 04:10 用户指令变更（冻结期内例外）**：用户明确要求恢复「从三个位置选两个计入」并保留尾码 hold3 稳定化，经用户批准直接升 v9（kill3-tail-v9-two-counted-hold3），未走影子门禁（用户指令优先，已在 ops/README.md 留档）；冻结戳按 v9 重置，旧 v1–v8 封存只读保留。
- **2026-11-01 10:04 用户指令变更（冻结期内例外）**：用户明确要求关掉两套「3 期」限制——尾码稳定化 hold3-until-miss 与位置/弃位最多连用 3 期强制换码，全部交给模型自主决定；经用户确认（两套都关）直接升 v10（kill3-tail-v10-two-counted-free），未走影子门禁（用户指令优先，已在 ops/README.md 留档）；冻结戳按 v10 重置，旧 v1–v9 封存只读保留。
- **2026-11-04 00:23 用户指令变更（冻结期内例外，v13）**：用户要求把面板块「三数共现避开 1/2/3 组」并入本框架整组——整组由 5 项（2 位置 + 1 合尾 + 2 反向形态）扩为 8 项（+ 三数共现避开 3 组，每组各算 1 项同时避开才算命中），攻击语在四定合尾与形态之后追加「除对数<1组>-<2组>-<3组>」（组序固定为 threeDigitAvoid/threeDigitHit/threeDigitAvoid2 = 避开1/2/3 组，与攻击语示例「除对数579-349-089」同段语义）；三数组由后端 extraKills 按目标期给定，模型不选。经用户问卷确认（8 项口径 / 组序按 1-2-3 组标签 / 升 v13 替换现网 v12）直接升 v13（kill3-tail-v13-hot-cold-hold3-two-counted-three-avoid），未走影子门禁（用户指令优先，已在 ops/README.md 留档）；冻结戳按 v13 重置（frozen_at 2026-11-04T00:23:17 / freeze_ref_period 20261104005），旧 v1–v12 封存只读保留、结算仍按各自封存字段（v12 及更早无 three_avoid → 5 项口径不变）。

## 1. 变更门禁（冻结期结束后才允许）

1. 先做影子：只读影子模块，先回补全部历史、再逐期前瞻；不改现网封存与面板。
2. 门禁达标才考虑：样本 n≥50；整组命中率相对现行 95% CI 不劣；单项不低于精确基线 −2σ；换码次数/缺期率不增加。
3. 达标后升新 revision：一版只改一项（便于归因）；先按 10^4 开奖空间对封存配置逐期精确枚举，算出新基线并登记到 hitrate_baseline 与 framework_hitrate_cumulative。
4. 一天最多 1 次口径变更；必须等 2 个真实周期，证据齐全（封存 / 结算 / 面板 / README / commit 号）才算完成。
5. 未达标不转正：影子丢弃或回滚，旧封存只读保留。

## 2. 统计口径（防止读数误解）

- 三层读法：本版累计（对照精确基线）、近 200 期滚动（drift / CUSUM，已上线）、今日参考（n<200 明示波动区间）。
- 面板新增口径行：当前完整 rule_version、自哪期起、本版期数、6 项精确基线 54.78%。
- 任何版本切换必须在面板与 README 写清「旧版 N 期归档、新版从 0 累计」，不允许静默切换。

## 3. 部署纪律（今天两次事故的直接对策）

- 单写者：kill3 同一时间只允许一个会话部署。多个 DSH 窗口用独立 git worktree，由主窗口统一 commit / 部署；共享仓库禁止 git add -A。
- 改前：确认 git status 干净；备份到 /root/ops/<任务名>-<HHMMSS>/（目标文件 + seals/settlements 快照 + 回滚说明）。
- 改后：py_compile → 对真实封存离线渲染 / 回放（0 差异）→ 在 cron 间隙（每 5 分钟的 :00-:20）部署 → 等 1-2 个真实周期 → commit → 刷新 manifest（已挂 post-commit hook 自动）→ ops_health 复核。
- 回滚：git revert <commit> + 从备份回拷；已封存记录一律不改写、不补写。

## 4. 监控与告警

- ops_health 每 10 分钟：kill3_tail（当期封存）+ kill3_stability（24h 版本切换次数 / 缺期槽数 / 发布延迟中位数）。
- hitrate_guard 每 10 分钟：读累计报告的 drift 段（近 200/300 期 z、500 期 CUSUM 峰）。
- 阈值：版本切换 ≥2 次/24h → warn；缺期 >2 槽/24h → fail；发布延迟中位数 >180s → warn、>300s → fail。
- 证据日志：data/kill3_tail_cron.log（发布 / 换码 / 限制）、data/ops_alerts.log（状态跃迁）。

## 5. 余额与 402

- 余额低于 10 元提醒、低于 5 元预警；余额守护按现有阈值自动停 / 恢复 AI 总闸，无需手动开关。
- 402 计入缺期分类（已上线），不允许把 402 当成功重试。

## 6. 复盘节奏

- 每日 05:00 业务日切换后：各版本 n / 命中 / 精确基线 z、缺期、发布延迟、402。
- 每周一次：是否触发 drift 告警、是否需要影子评估、冻结期是否延长。

## 7. 为什么需要这套（今日事故留档）

- 20:55 cron 因改文件撞上模块导入失败 1 次（20:56 手动补发，无缺期）。
- 一天内 4 次口径切换（v6 → v7 → v6 → v8），每次切换今日命中率清零重算，观测抖动被误读为退化。
- 两个 DSH 窗口并发改同一工作区 2 次（v7 无记录回归、v8 与回滚并行）。
- 结论：稳定性的来源是口径冻结 + 单写者 + 分层统计，不是继续调策略追数字。

## 8. 冻结守卫（机器强制，2026-10-31 22:14 起）

- 冻结戳：ops/kill3-freeze.json，记录 rule_version（当前 kill3-tail-v9-two-counted-hold3）、frozen_at（2026-11-01 04:10:43）、freeze_ref_period（20261101050）、min_periods=100、freeze_hours=72，以及 4 个受控源码 sha256（lib/kill3_tail.py、lib/kill_three_view.py、scripts/run_kill3_tail.py、lib/panel_context.py）。
- ops_health 新增探针 kill3_freeze：口径版本与受控源码 hash 不变 → OK，并显示进度（N/100 期、已冻结 h/72h）；任一变化 → WARN，detail 列出变更文件或口径差异。其他框架（framework/six/cold/killer 等）的稳定化改动不在此守卫范围。
- 经批准的变更（冻结期满走门禁，或冻结期内允许的 bug 修复）完成后，必须重算受控文件 hash、更新冻结戳并记录原因与 commit；否则监控会持续 WARN。
- 冻结期结束条件：≥100 期 或 满 72 小时，先到为准；当前进度见 ops_health 的 kill3_freeze 行。

- 2026-11-05 03:15 用户指令变更（冻结期内例外，非口径升级）：四手连败展示口径改跨天累计（kill1/kill9/kill8/kill3 四卡统一，新增 four_loss_cycles_total；原「今日四手连败」保留向后兼容）。只改统计展示与脚注，不改选码/尾码/封存/结算，不升 RULE_VERSION；kill3_tail.py / kill_three_view.py 重算 hash 并记入 ops/kill3-freeze.json 的 hash_updates（frozen_at 未重置）。提交 e359450。
- 2026-11-05 03:26 用户指令变更（冻结期内例外，口径升级 v14）：位置排除依据改为「近50期热码 top3、上期重码、上期邻码（±1）」，删除冷码候选并明确禁止以冷码/长遗漏为依据（对齐 KILL_ONE v3）；整组结构不变（8 项），注册基线 0.5863 沿用、不重注册。冻结戳按 v14 重置：frozen_at 2026-11-05T03:26:17+08:00、freeze_ref_period 20261105041、frozen_commit fb5f327；lib/kill3_tail.py sha256 426d014f…→a96151f9… 记入 hash_updates。提交 fb5f327。
- 2026-11-05 04:44 用户指令变更（冻结期内例外，口径升级 v15）：用户 m00003「怎么这个框架开什么除什么，我是要结合冷码这期排除。还有影子数据，由模型决定采取」。位置段恢复「近50期热码 top3 + 冷码 top3」并列并删除「不得以冷码/长遗漏为依据」，保留上期重码/邻码段；提示词新增【位置杀码稳定化影子】（现行换码 vs hold2/hold3/hold5/hold-until-miss 的命中率与换码次数 + 上期各位杀码，只读回放 735 期/1571 注），是否沿用上期同位置杀码完全由模型决定，系统不做确定性 hold 后处理；seal 增加 position_shadow/position_basis_used/hold_adopted/hold_policy。整组结构（8 项/2 注）与尾码 hold3-until-miss 不变，基线 0.5863 追加同值行不重校准。冻结戳按 v15 重置：frozen_at 2026-11-05T04:44:42+08:00、freeze_ref_period 20261105056、frozen_commit 2c34e59；lib/kill3_tail.py sha256 a96151f9…→6eac93a3…、scripts/run_kill3_tail.py 0261afc2…→7c55f6ce… 记入 hash_updates。提交 2c34e59。
- 2026-11-05 23:51 用户指令变更（冻结期内例外，影子口径恢复，非口径升级）：位置杀码稳定化影子由「已全部转正、不再采集」恢复为多档只读对照（hold2/hold3/hold5/不设上限，全部只读），面板空态与 JSON note 同步改为「是否沿用由模型自行决定」，旧文案清零；提示词影子段补齐「上期同位杀码 + 上期该位实际开奖 + 杀码命中/失手」与四档命中率/换码次数，并标注【参考信号】；seal 留痕字段统一为 position_hold_shadow / position_basis_flags / hold_adopted（旧 position_shadow / position_basis_used 保留兼容读）；全仓无按阈值自动写开关分支（kill3 路径 grep auto_promote = 0）。本次重算回放（23:51:52）：kill3 965 期/2031 注，现行 90.00%（换码 900）、hold2 89.46%/576、hold3 89.76%/434、hold5 89.27%/349、until-miss 90.00%/184；forward 654 期/1308 注。冻结戳 version 2→3（frozen_at 未重置）：lib/kill3_tail.py 6eac93a3…→1ceb7656…、scripts/run_kill3_tail.py ac37eb42…→b5b7f97d… 记入 hash_updates；受控 manifest 无 drift（auto-baseline-20261105-235246，429 文件）。提交 db5be81。

## 9. kill3 v16 预登记回滚门禁（2026-11-06）

- 背景：用户 m00563「就是不稳定啊，漂移太严重了」；m00593「按你的意思」→ 采用方案 A：v16 维持现网、不改口径、不暂停预测，但在任何后续数据之前先把判据写死（预登记），达标自动告警并可按脚本一键回滚 v15。
- 预登记判据（观察起点即 v16 起算点 target 20261106001）：
  - 样本门槛 N = 150 期（按 288 期/日约 12.5 小时连续运行）。
  - 触发条件：n ≥ 150 且（z < -2.5 或 整组命中率 < 50%）。z 为相对 10^4 开奖空间精确枚举基线 0.5863（8 项整组）的下尾 z；门禁同时给出精确二项下尾 p_lower 与双侧 p_two 供复核。
  - 判定档位：inactive（现行 rule_version 不是 v16）/ observe（n<150）/ passed（n≥150 未触发）/ triggered（触发）。
  - 理由：z<-2.5 约为单侧 1% 假阳性；50% 硬底约等于基线 -8.6pp，作为比 z 更保守的兜底；n≥150 使 z 检验近似有效，且与「按整组统计」的口径一致。
- 实现：
  - `lib/kill3_v16_guard.py`：读 `data/kill3_tail/settlements.json`，仅取 rule_version=v16 且 verified=true 的组命中；算 n/hits/rate/Wilson 95%CI/expected/delta/z/p_lower（精确二项下尾）/p_two/thresholds/progress/verdict → 原子写 `data/kill3_v16_guard.json`；CLI `--json` / `--write`。
  - `scripts/ops_health_check.py` 新增 `kill3_v16_gate` 探针（随 10 分钟体检）：triggered → FAIL 并附回滚命令；其余 → OK 并显示进度 n/150。
  - `lib/kill_three_view.py` 的 kill3 卡新增只读门禁行（`_gate_html`）：显示「预登记回滚门禁（v16）：进度 n/150 期 · 本版 hits/n · z=… · 判定…」；triggered 时红字提示按本节执行回滚；v16 已回滚时灰字停用（读 rule_version 判定）。
- 回滚执行（triggered 后，或人工判断需要时）：
  - 预检：`python3 scripts/kill3_v16_rollback.py`（默认 dry-run，打印计划并做 `git apply -R --check` 反向补丁预检）。
  - 执行：`python3 scripts/kill3_v16_rollback.py --execute`；门禁未 triggered 时需显式 `--force`。
  - 脚本行为：备份 5 个文件到 `/root/ops/kill3-v16-rollback-<ts>/` → 以提交 417c472 生成反向补丁并 `git apply -R` → 断言 RULE_VERSION=v15 且 v16 专有字段清零 → py_compile → 重算冻结戳（`freeze_guard.py init --framework kill3_tail --renew` + 旧 `ops/kill3-freeze.json` 镜像）→ 写门禁 inactive → 按文件精确 `git add` 提交（不使用 `git add -A`）。
- 回滚后口径：v16 样本按 rule_version 自然隔离，统计回到 v15 分组；整组结构与基线 0.5863 不变，无需重注册；门禁因 rule_version 不匹配自动 inactive。
- 当前进度（2026-11-06 06:40:17 CST）：n=79、hits=33、rate=41.77%、z=-3.0424、p_two=0.0037、status=observe（离触发还差 71 期）；详见 `data/kill3_v16_guard.json` 与 ops_health 的 `kill3_v16_gate` 行。
