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

- 面板 meta 增加「周期 第 N 轮 x/40 期」「复盘通过/异常/待到期」，统计行增加本轮期号。
- `ops_health_check`：`kill15_policy` 增 v4 校验（`policy.b_rotation_forced`、`cycle.*` 字段、
  B 子策略与上期同位不同）→ 违规 fail；新增 `kill15_review` 探针：
  未到期 ok / 到期未复盘 warn / 复盘不一致 fail。

## 4. 自测结果

| 脚本 | 结果 |
| --- | --- |
| `python3 scripts/test_kill15_v2.py` | **SELFTEST OK**：prompt v4、合法输出、v2 规则 1–5、v3 惯性护栏、v4 强制轮换/周期重置/封存字段、复盘一致/篡改两路（共 19 项 PASS） |
| `python3 scripts/test_kill15_health.py` | **SELFTEST OK**：kill15_tail / policy（含 v4 轮换/周期字段）/ retry / review 四类探针（共 14 项 PASS） |

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
| 回放 | v4 45 期大样本回放 06:26 启动（隔离账本 `replay_v4.json`），用于验证第 41 期周期边界；结果单列 |

**部署时序备注**：06:20 那期仍是 v3（进程在文件上传前已 import）；06:25 起 v4，符合“等下一个整 5 分钟验证”的既有结论。

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
