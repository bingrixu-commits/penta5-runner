# kill15 v4 · 周期闭环 + 抽样复盘上线台账

- 上线时间：2026-11-12 06:25（北京时间）
- 前置：v3 惯性护栏（`ops/kill15-v3-inertia-guard.md`）
- 状态：面板模式（`mode=panel`，只封存/结算/发布面板，不投注）
- 触发：按「完整闭环标准框架」补齐剩余缺口——周期重置（40–45 期）、抽样复盘（30–40 期）、
  B 组三子策略强制轮换、15 期滚动隔离显式化。

## 1. 版本与文件

| 项 | 值 |
| --- | --- |
| `RULE_VERSION` | `kill15-tail-v4-cycle-closedloop` |
| `PROMPT_VERSION` | `kill15-tail-prompt-v4-cycle-closedloop` |
| 旧代际 | v1/v2/v3 记录原样保留、不迁移；统计按 `rule_version` 切分 |
| 主体 | `lib/kill15_tail.py` |
| 面板 | `lib/kill_fifteen_view.py` |
| 运行入口 | `scripts/run_kill15_tail.py`（cron 不变 `*/5 * * * *`） |
| 复盘 CLI | `scripts/kill15_review.py`（新增） |
| 健康巡检 | `scripts/ops_health_check.py`（新增 `kill15_review` 探针，`kill15_policy` 增 v4 校验） |
| 自测 | `scripts/test_kill15_v2.py`（v2–v4，19 项）、`scripts/test_kill15_health.py`（14 项） |
| 新增账本 | `data/kill15_tail/cycles.json`（周期起点，append-only）、`data/kill15_tail/reviews.json`（复盘记录，append-only）、`data/kill15_tail/review_alerts.log`（不一致告警） |

## 2. 完整闭环标准框架 16 项落点对照

| # | 标准项 | 落点 | 状态 |
| --- | --- | --- | --- |
| 后端 1 | 开奖历史持久存储 | 复用 `data/stats_draw_history.json` + `load_snapshot`（既有框架） | ✅ |
| 后端 2 | 本位遗漏自动计算 | `omission_map()` 每期重算四号位遗漏，写 prompt/audit | ✅ |
| 后端 3 | 候选池/深冷筛选（后端自框架） | `build_pool()` + `deep_cold_burst()`（用户自建口径，v4 未改） | ✅ |
| 后端 4 | 近 3 期深冷统计/冷反弹判定 | `deep_cold_burst(draws=3)` → `regime`（冷反弹活跃/冷沉寂） | ✅ |
| 后端 5 | 滚动 15 期台账 Hit-A/Hit-B | `performance_from_settlements(window=15)`，v4 起只取本轮周期 | ✅ |
| 后端 6 | 14 期顺局 runaway_warning | `runaway_state()`：同分配连续整组命中 ≥14，v4 起只在轮内累计 | ✅ |
| 后端 7 | 二次硬校验/丢弃重调/脏数据禁入台账 | `normalize_response()` 全量硬校验 + 重试 ≤3，失败不发布；台账只从已校验封存结算 | ✅ |
| 后端 8 | 独立单期调用 | 每期独立进程 + 单条 user message 请求，无会话复用；封存 `policy.request_isolation` 留痕 | ✅ |
| 后端 9 | 周期重置（40–45 期） | **v4 新增**：`cycle_state()/current_cycle_settlements()`，40 期一轮软重置 | ✅ |
| 模型 1 | A/B 号位动态配比 | `a_cap()`（盘面 + Hit-A/Hit-B）→ prompt「A 组上限」 | ✅ |
| 模型 2 | Hit-A/Hit-B 联动自适应 | `a_cap()` 阈值联动（<70%/>88% 压低 A、双低 A=1、Hit-B<70% 强制轮换） | ✅ |
| 模型 3 | B 三子策略强制轮换 | **v4 加强**：由“低命中/顺局才轮换”改为**每期逐位强制轮换**（校验 + prompt + 巡检） | ✅ |
| 模型 4 | 14 期预警打乱 A/B | `runaway_warning` 时强制打乱分配 + 轮换全部 B 子策略 | ✅ |
| 模型 5 | 每期完整自检 | `self_check` 五项强制输出，缺项/false 即拒收重试 | ✅ |
| 运维 1 | 台账隔离（严格 15 期滚动） | 统计只取本轮周期最近 15 期（`policy.stats_scope=current-cycle-last-15`） | ✅ |
| 运维 2 | 抽样复盘（30–40 期） | **v4 新增**：每 30 期自动核账（`audit_settlements` 纯函数）+ `kill15_review` 巡检 | ✅ |

## 3. 变更明细

### 3.1 周期软重置（`CYCLE_PERIODS = 40`）

- `generation_settlements()`：按 `rule_version` 切代际；`cycle_state()` 由本轮已结算期数推导
  `cycle_no / cycle_pos / is_cycle_start`；`current_cycle_settlements()` 只返回本轮周期记录。
- Hit-A/Hit-B 只统计**本轮周期内**最近 15 期；顺局连击只在本轮内累计；跨轮自动清零。
- 每轮首期封存成功后向 `cycles.json` 追加周期起点记录（`cycle_no/start_ref/start_target/previous_cycle`），
  重复调用不重复写；封存/结算新增 `cycle`、`policy.cycle_*` 字段留痕。
- 每轮首期 prompt 增「周期软重置」提示：历史胜率偏好已清空，必须按本期指标重新推导。
- 周期 40 期可调（40–45 区间），改 `CYCLE_PERIODS` 后按新 `RULE_VERSION` 重开代际。

### 3.2 B 组三子策略强制轮换

- 后端硬校验：每个 B 号位子策略必须与上期**同位**不同（无条件执行，不再只在 Hit-B<70%/顺局时）；
  B 多号位之间仍不得重复。
- prompt 改写：配比规则第 4 条、防塌方规则第 7 条、自检 `dispersed` 语义同步；
  封存 `policy.b_rotation_forced=true`；`kill15_policy` 巡检新增「B 子策略未强制轮换」硬校验。

### 3.3 抽样复盘（`REVIEW_PERIODS = 30`）

- `audit_settlements()`（纯函数，可离线自测）：把最近 30 期结算与封存、原始开奖逐项核对：
  开奖号/命中判定/整组判定、封存发布早于开奖、自检、A 上限、子策略分散、强制轮换、
  近 2 期完整方案去重、杀码在候选池、结算-封存一致性、已开奖未结算封存。
- `run_scheduled_review()`：每 30 期由 runner 自动跑一次（按 bucket 去重），
  结果 append 到 `reviews.json`；发现不一致追加 `review_alerts.log` 并计入巡检 FAIL。
- CLI：`python3 scripts/kill15_review.py [--force] [--json]`，只读账本、不调模型。
- 命中率同时给出 Wilson 95% 区间并与随机基线 65.61% 对照（显著偏离 → 提醒人工复核口径）。

### 3.4 面板与巡检

- 面板 meta 增加「周期 第 N 轮 x/40 期」「复盘通过/异常/待到期」，统计行增加本轮期号；
  并展示「全代际累计整组命中」作为对照（旧代际只读展示，不参与调参）。
- `ops_health_check`：`kill15_policy` 增 v4 校验（`policy.b_rotation_forced`、`cycle.*` 字段、
  B 子策略与上期同位不同、四码去重规则 7）→ 违规 fail；新增 `kill15_review` 探针：
  未到期 ok / 到期未复盘 warn / 复盘不一致 fail。
- runner `--dry-run` 改为零副作用：不结算、不复盘、不调用；只打印上下文与 prompt。

## 4. 自测结果

| 脚本 | 结果 |
| --- | --- |
| `python3 scripts/test_kill15_v2.py` | **SELFTEST OK**：prompt v4、合法输出、v2 规则 1–5、v3 惯性护栏、v4 强制轮换/周期重置/封存字段、复盘一致/篡改两路（共 19 项 PASS） |
| `python3 scripts/test_kill15_health.py` | **SELFTEST OK**：kill15_tail / policy（含 v4 轮换、周期字段、四码去重规则 7）/ retry / review 四类探针（共 15 项 PASS） |

## 5. 线上验证（2026-11-12 06:26–06:31）

| 项 | 结果 |
| --- | --- |
| 首个 v4 封存 | `20261112077 → 20261112078`，`published_at=06:26:04`；attempt 1 一次通过（36.5s，6330 completion tokens） |
| 周期软重置生效 | 封存 `cycle={cycle_no:0, cycle_pos:1, is_cycle_start:true}`；`performance.periods=0`、`a_cap=2`；prompt 含「第 1 轮 第 1/40 期（周期软重置）」；模型理由「周期重置，千十深冷为A，百个对冲B，B子策略轮换」 |
| 周期台账 | `data/kill15_tail/cycles.json` 写入第 1 条：`start_ref=20261112077`、`cycle_periods=40`、`previous_cycle=null` |
| 第 2 期 | `20261112078 → 20261112079`，06:30:40 published；`cycle_pos=2`、`performance.periods=1`（轮内累计生效）；B 子策略 `B3/B1/B2` 与上期 `B2/B1` 逐位不同 |
| 台账结算 | v4 第 1 期结算 `20261112077→078 group_hit=true`，结算记录带 `cycle_no/cycle_pos` |
| 抽样复盘 | `python3 scripts/kill15_review.py --force` → review #1 `bucket=0 status=ok`，样本 1 期（整组 1/1、单码 4/4），不一致 0、提醒 0；`reviews.json` 落盘 |
| 健康巡检 | `kill15_tail` ok ／ `kill15_policy` ok（v2/v4：候选池/A→上限/B 强制轮换/周期字段/惯性护栏/自检）／ `kill15_retry` ok（近 6h runs=20 retries=0 failures=0）／ `kill15_review` ok（未到期 1/30） |
| 面板 | 区块 3320 bytes，含「第1轮」「第 2/40 期」「B 子策略每期强制轮换」；`PENTA5_PANEL_TIMING status=success` |
| v4 45 期回放 | 整组 **34/45 = 75.6%**、单码 **166/180 = 92.2%**；第 41 条记录（ref `20261112072`）进入第 2 轮：`cycle_no=1, cycle_pos=1, settled_in_cycle=0, settled_total=40`，轮内绩效从 0 重新累计（随后 periods=1,2,3,4）——周期边界在真实请求链路上验证通过 |
| 并行改动兼容性 | 06:43 另一会话上线 `ai-scope`（cron 全部作用域化，kill15 行被包为 `with_scope.sh kill15`）；06:45 v4 第 5 期照常 attempt 1 通过（21.6s），`kill15_*` 四项探针全 ok，确认不受影响 |

**部署时序备注**：06:20 那期仍是 v3（进程在文件上传前已 import）；06:25 起 v4，符合“等下一个整 5 分钟验证”的既有结论。

**并行会话并入说明**：部署窗口内另一并行会话（git 作者 `ops-bot`）于 06:28 在同一工作区追加了三项兼容改动——
`kill15_policy` 增四码去重规则 7 巡检、面板增全代际累计对照、runner `--dry-run` 零副作用；
随 v4 代码一并提交（`20040ed`）。本轮已合入本地工作副本并重跑自测（15 项全过），口径不变。
**后续如需再改同一批文件，请只保留一个会话操作，避免互相覆盖。**

## 6. 回滚方法

- 代码回滚：`git revert <v4 提交>` 或 `cp -a /root/backups/kill15-v4-<时间戳>/{lib,scripts}/...` 覆盖；
  crontab 不变，回滚后下一轮 cron 自动生效（注意：进程启动即 import，须等下一个 5 分钟整点）。
- 账本不回滚：v4 已封存/结算记录保留，按 `rule_version` 与新代际隔离；回滚到 v3 后统计自动回到 v3 口径。
- 已写 `cycles.json` / `reviews.json` 为 append-only，可保留不影响旧口径。

## 7. 观察点

1. **强制轮换的重试率**：`kill15_retry` 近 6h 重试 ≥3 次或 ≥25% → 检查 prompt 是否被模型忽略；必要时把
   `B_SUBS` 轮换窗口放宽为“与上期不同”优先、允许池内子策略映射顺序微调，但**不建议**取消强制轮换。
2. **周期边界**：第 41 期（约 3.3h 后）检查 `cycles.json` 出现第 2 条记录、prompt 出现「周期软重置」、
   封存 `cycle.cycle_no=1 / cycle_pos=1`。
3. **首次自动复盘**：第 30 期（约 2.5h 后）检查 `reviews.json` 第 1 条 `status=ok`、`kill15_review`=ok；
   若 mismatch，先人工核对原始开奖与台账再决定是否暂停发布。
4. **命中率口径**：只信 `reviews.json` 与 `kill15_policy` 的核对结果；面板数字需与复盘样本对得上。
5. **v4.1（07:05 起）**：约 08:55 第 30 期应出现第 1 条 `trigger=scheduled` 复盘（bucket 1）；
   约 09:45 第 41 期进入第 2 轮，`cycles.json` 应出现第 2 条、封存 `cycle_no=1`。
   若 AI 全局关闭，台账/结算/复盘仍继续（只停预测）；若 kill15 作用域被 stop，进程不启动（设计如此，恢复后自动补结算）。

## 8. v4.1 修复完善（2026-11-12 07:05 部署）

上线前检查发现 4 处闭环不完善，已修复并部署：

| # | 问题 | 修复 |
| --- | --- | --- |
| 1 | AI 关闭时 runner 在入口 `require_ai_enabled()` 直接退出 → 结算/复盘也停摆 | 结算+复盘抽成 `maintain_ledger()` 并前置执行，不依赖 AI 开关；模型调用单独 `request_or_skip()`，遇到 `AICallsDisabled` 只跳过本期预测（退出码 0），台账已维护 |
| 2 | 复盘只查当前 bucket，错过即永久缺失；`--force` 手核记录被当成 30 期合规 | `run_scheduled_review()` 按 bucket 顺序补跑（如 60 期一次补 1/2）、幂等；forced 记录 `trigger=forced` 不计入合规；`audit_settlements(upto=...)` 支持按 bucket 切片 |
| 3 | `kill15_review` 巡检只看最新 bucket；面板会拿 forced 记录显示“复盘通过” | 巡检改为检查**所有到期 bucket**且只认 scheduled；面板按到期 bucket 显示「待到期（x/30）/待执行/通过/异常」 |
| 4 | 周期台账无独立巡检，cycles.json 与封存/结算可能静默不一致 | 新增 `kill15_cycle` 探针：周期编号连续性、与最新封存轮次一致、与结算期数推导周期一致（允许 1 期结算滞后）、起点记录滞后 1 轮给 warn |

参数去重：复盘周期/周期长度优先读取最新封存的 `policy.review_periods` / `cycle.cycle_periods`（缺省回落 30/40），避免巡检常量与代码脱节。

**自测（全部通过）**

| 脚本 | 结果 |
| --- | --- |
| `python3 scripts/test_kill15_v2.py` | 新增 `audit upto` 切片、复盘调度补跑/幂等/forced 单列 → 全过 |
| `python3 scripts/test_kill15_health.py` | 新增 forced 不计合规、缺旧 bucket、cycle 缺失/断号/滞后 → 20 项全过 |
| `python3 scripts/test_kill15_runner.py` | 台账顺序 settle→review、AI 关闭降级、正常透传、dry-run 保护 → 4 项全过 |

**线上验证**

- 07:01 `--dry-run`：seals/settlements/cycles/reviews 四个账本 sha256 前后一致（零副作用）。
- 07:05 新 runner 首期 `20261112085→086`：attempt 1 通过（14.6s），面板发布成功；
  `kill15_tail / kill15_policy / kill15_retry / kill15_review / kill15_cycle` 五项探针全 ok
  （retry 近 6h runs=27 retries=0 failures=0）。
- 面板显示「第1轮 9/40期 ｜ 复盘待到期（8/30 期，还差 22 期）」，与账本一致（forced #1 不再冒充合规）。

### 8.1 落地完善（07:18，含页面接入与端到端验证）

| 项 | 结果 |
| --- | --- |
| `/f/kill15` 新框架页接入 | 用 node 直接调用 `framework_pages.js` 的页面处理器离线渲染：HTTP 200、7556 bytes、含 `KILL_FIFTEEN` 区块、标题「kill15 分位杀号 · Penta5」；总览页 kill15 显示「已渲染」 ✅ |
| 3010 端口实网探测 | 失败/超时——并行会话正在改造 panel/登录并反复重启后端（pm2 `penta5-runner` restarts=6、unstable=0、CPU 高），属其进行中的部署窗口，非 kill15 问题；待其稳定后复测 |
| 真实回放端到端复盘 | 用 `replay_v4.json` 45 期真实记录重建临时账本 → 自动生成 bucket 1 复盘：30 期样本、整组 22/30、不一致 0、status=ok ✅ |
| 第 2 轮周期写入 e2e | 临时账本 40 期 → `save_seal` 写入 `cycles.json` 第 2 条（`previous_cycle.settled=40`）；重复封存不重复写（幂等）✅ |
| 回归自测 | `test_kill15_v2.py` 全部通过（新增 replay-e2e / cycle-record-e2e 两项端到端）；`test_kill15_health.py` 20 项、`test_kill15_runner.py` 4 项通过 |
| 发布锚点 | git tag `kill15-v4.1` → 代码提交 `99bbe1b`（回滚用 `git revert 99bbe1b` 或 checkout 该 tag 前的 `20040ed`） |

### 8.2 v4.2 子策略定义下发（2026-11-12 07:50）

- `PROMPT_VERSION → kill15-tail-prompt-v4.2-substrategy-defs`（`RULE_VERSION` 仍 v4，统计代际不变）。
- prompt 新增【A/B 三子策略定义】与每号位「策略候选」行：A=池内遗漏最大、B1=池内近30期最热、
  B2=池内近3期深冷刚开出（无标的时按「候选不足」降级并在 reason 注明）、B3=单双/大小偏弱侧遗漏最大。
- 复盘 `reviews.json` 新增 `semantics`：A/B1/B2/B3 语义符合率（<50% 写 warning）；`kill15_review` 详情带出。
- 验证：staged 回放 12 期符合率 100%/100%/100%/100%（旧 prompt 实测 B1 25%、B2 0%、B3 0%）；
  线上首期 `094→095` 四码 4/4 与候选一致、attempt 1、五项探针 ok。
- 旧版备份 `/root/backups/kill15-tail-prompt-v4-cycle-closedloop.py`；B2 与候选池互斥问题待决策
  （详见 `ops/kill15-drift-diagnosis-20261112.md` §9.5）。


