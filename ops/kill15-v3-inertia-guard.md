# kill15 v3 · 惯性护栏上线台账

- 上线时间：2026-11-12 05:35（北京时间）
- 前置：v2 防塌方加固（`ops/kill15-v2-anticollapse.md`）
- 状态：影子跑；cron 不变 `*/5 * * * *`

## 1. 为什么加 v3

v2 同 12 期回放（ref 049..060）出现**两套分配交替**：
`A=千`(B=百/十/个, B1/B2/B3) ↔ `A=百`(B=千/十/个, B1/B2/B3)，隔期完全复现。
v2 规则 1 只禁止“与上期相邻重复”，这种 2 周期循环合法但仍是长期拟合惯性。
v3 用“完整方案窗口”把它锁死。

## 2. 变更

| 项 | v2 | v3 |
| --- | --- | --- |
| `PROMPT_VERSION` | `...-v2-anticollapse` | `kill15-tail-prompt-v3-inertia-guard` |
| `RULE_VERSION` | 不变 | 不变（口径未变） |
| 完整方案 | 未定义 | `(A/B 分配, B 子策略映射)` |
| 重复窗口 | 仅上期 | **最近 2 期** `SCHEME_REPEAT_WINDOW=2` |
| 自检语义 | 与上期组合不同 | 与上期组合不同 **且** 完整方案不与近 2 期重复 |
| 封存 `policy` | — | 增 `scheme_reuse_forbidden`、`scheme_repeat_window` |
| prompt 数据 | 上期分配 | **上期方案 + 前第2期方案（禁止重复）** |

拒绝路径：`禁止惯性方案复用：完整方案（A/B 分配 + B 子策略）与前第 N 期完全相同，必须重新推导`。
允许路径：同一 A/B 分配但 B 子策略映射已轮换 → 放行（护栏只锁完整方案，不锁单个分配）。

## 3. 候选池决策（规则 5 的落地口径）

维持 v1/v2 口径：每号位取当前遗漏 ≥8 的码，不足 5 个时按遗漏降序补足 5 个。
理由：候选池是模型唯一合法杀码集合，补足能保证池内始终有 5–10 个可杀标的，
不会因“池空/池太小”阻塞整期发布；`候选不足` 仅作为防御性理由标注保留（实际几乎不触发）。
**不收紧**为“只下发遗漏 ≥8、不补足”。

## 4. 自测（14 项全过）

`python3 scripts/test_kill15_v2.py`：
- prompt v3 内容（含“前第2期方案”“最近 2 期”）、合法输出通过；
- 规则 1 沿用上期组合拒收；**v3 与前第2期完整方案相同拒收**；**同分配但子策略轮换放行**；
- 规则 3 子策略重复拒收；规则 4 A=0 / 超上限拒收；规则 5 池外码拒收；
- 自检缺失/false 拒收；runaway 语义；runaway/Hit-B<70% 未轮换拒收；
- 封存字段（`prompt_version`、`policy.scheme_repeat_window`）。

## 5. 部署与并行回放说明

- 部署时另一会话的 **200 期 v2 大样本回放**正在运行：Python 进程启动时已把 v2 模块载入内存，
  磁盘换 v3 **不影响该回放样本**（记录仍标 `prompt_version=v2`、单独账本 `replay_v2_200.json`）；
- 但该回放结束后若做代码指纹复核，`lib/kill15_tail.py` 哈希已变（v3），属预期；
  如需严格“同代码”复核，用 git `03bcc15`（v2）对照。
- 线上 cron 自 05:35 起使用 v3，封存 `prompt_version` 应为 v3、`policy.scheme_repeat_window=2`。
- **已确认**：连续 2 期线上 v3 封存成功，均 attempt 1 一次通过、无拒收重试：
  - `2026-11-12 05:35:26`：`20261112067→068`，`A=百 / B=千、十、个（B2/B3/B1）`；
  - `2026-11-12 05:40:27`：`20261112068→069`，`A=十 / B=千、百、个（B2/B1/B3）`；
  - 两期 `prompt_version=kill15-tail-prompt-v3-inertia-guard`，
    `policy.scheme_reuse_forbidden=true`、`scheme_repeat_window=2`，且与近 2 期完整方案均不同。
- 备注：v2 上线期间（05:20–05:31）曾出现 `066→067` 方案 = `064→065` 方案
  （A=千/B=百十个/B1B2B3，隔 2 期复现），正是 v3 护栏要拦的“两套方案交替”；
  v3 上线后首期即未再复现。

## 6. 观察点

1. 前 5 期关注是否出现“方案与前第2期重复”的拒收重试；若重试率高，考虑把窗口降为 1（仅上期）或放宽子策略映射。
2. 记录 v2（12 期小样本）与 v3 上线后影子命中，`data/kill15_tail/{seals,settlements}.json` 按 `prompt_version` 分组统计。
3. 200 期 v2 大样本跑完后，用其置信区间判断 v2 防塌方对命中率的真实影响；v3 再单独积累样本。

## 7. 健康巡检注册（2026-11-12 05:46 落地）

`scripts/ops_health_check.py`（cron `*/10`，写 `data/ops_health.json` / `ops_alerts.log`）
新增 kill15 三探针，纳入既有巡检总表：

| 探针 | 判据 |
| --- | --- |
| `kill15_tail` | 最新已开奖期应有开奖前封存并发布；开奖后 7 分钟宽限内 waiting=ok，超时未封存/未发布 fail |
| `kill15_policy` | 最新封存按 prompt 版本复核规则：候选池内选码、A∈[1,a_cap]、B 子策略不重复、与上期分配不同、完整方案不与近 2 期重复、self_check 五项全 true、prompt_version 与 `lib/kill15_tail.py` 一致；规则违规 fail，版本漂移 warn |
| `kill15_retry` | 近 6h cron 日志 runs/retries/failures；≥3 次重试、重试率≥25%、或出现失败 → warn |

同步落地的配套项：

- `ops/crontab.txt` 快照补齐 kill15 cron 行，`crontab_drift` 由 warn → ok（45 jobs）；
- `ledger_size` 纳入 `kill15_tail/seals.json`、`kill15_tail/settlements.json`；
- `provider_402` 日志清单纳入 `kill15_tail_cron.log`；
- 自测 `scripts/test_kill15_health.py`：10 项全过（正常封存 + 5 类违规注入 + 版本漂移 + 重试/失败 + 封存滞后 + 未发布）；
- 首跑结果（2026-11-12 05:47）：`kill15_tail`=ok（ref=20261112069 published）、
  `kill15_policy`=ok、`kill15_retry`=ok（runs=11 retries=0 failures=0）；
- `manifest_drift` 因本次源码变更一度 fail，已按日常流程
  `refresh_source_manifest.py --if-drift` 重建基线后恢复 ok；
  overall=fail 当前仅剩 AI 停机窗口（00:47–04:42）造成的历史 coverage 缺口，与 kill15 无关。

