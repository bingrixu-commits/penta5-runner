---
id: "ea2d6c9e-1e38-4d1a-856b-3d4dfe4745bf"
title: "penta5-runner 四框架灵活位置改造总览（kill1 v7 / kill3 v17 / kill11 v2 / kill12 v3，2026-11-07）"
description: "四个定位排除框架由固定位置数升级为 AI 每期自选 2~4 位置、每位置杀 1 码，并改为逐期期望基线的口径、基线表、统计机制、验证证据、提交/回滚与观察注意事项。回答“这些框架现在怎么选位置、基线多少、统计怎么算、怎么回滚/验证”时参考。"
status: "active"
created_at: "2026-11-06T17:04:08.977Z"
updated_at: "2026-11-06T17:04:08.977Z"
content_hash: "24c1cac4df7b01a704263b4ee479a82a55fe9ec518c60b1d49658a53e5e7672e"
source_paths:
  - "penta5-posvar/P-report.md"
  - "penta5-posvar/A-plan.md"
  - "penta5-posvar/kill1-map.md"
  - "penta5-posvar/kill3-map.md"
  - "penta5-posvar/kill11-12-map.md"
  - "penta5-posvar/observation-20261107-0101.md"
session_ids:
  - "mnemon-run-v1-223560de-d7c5-4728-bafe-f6bfc9fc31e0"
memory_body_ids:
  []
---

# penta5-runner 四框架灵活位置改造总览（kill1 v7 / kill3 v17 / kill11 v2 / kill12 v3）

## TL;DR
- 用户 m00199「所有模型。定位排除站位不限次数」：四个在跑的定位排除框架由固定位置数改为「AI 每期自选 2~4 个互不相同的位置、每个位置杀 1 码」，整组命中率统计改为按实际 N 的逐期期望基线（面板显示 实际 vs 期望）。
- 2026-11-07 00:27 全部上线：服务器 /root/penta5-runner，git HEAD=origin/main=4008e5e，工作树 clean。
- 用户 2026-11-06 确认：n_policy=引导+观察（不强制 N 分布，只有连续 30 期全 N=2 才复查）；rollout=三批（批 0 共享统计基建 → 批 1 kill1 v7 → 批 2 kill3 v17 + kill11 v2 + kill12 v3）。
- 2026-11-07 01:02 用户「你看那个好用那个」→ 选定继续观察+定期小结：不做 1~4 放宽、暂不自动化 kill8。

## 四框架口径与随机基线（N=2/3/4）
| 框架 | rule_version | 整组构成 | 基线 N=2 / N=3 / N=4 |
|---|---|---|---|
| kill1 | kill1-tail-v7-flexible-two-four-position-one-tail | N 位置 + 1 合尾 = N+1 项 | 72.90% / 65.61% / 59.049%（N=1 参考 81.00%） |
| kill3 | kill3-tail-v17-flexible-two-four-position-one-tail-two-form-three-avoid | N 位置 + 1 合尾 + 2 反向形态 + 3 三数共现避开 = N+6 项 | 58.63% / 51.68% / 45.57%（解析 58.3427/51.5005/45.3446；v16 实测配置回放 58.5502/51.6808/45.5719） |
| kill11 | kill11-tail-v2-flexible-two-four-position-one-form-one-tail | N 位置 + 单双形态 + 1 合尾 = N+2 项 | 67.275% / 59.985% / 53.424%（N=1 参考 75.375%） |
| kill12 | kill12-tail-v3-flexible-two-four-position-one-tail | N 位置 + 1 合尾 = N+1 项 | 72.90% / 65.61% / 59.049%（N=1 参考 81.00%） |

- kill1/kill12 期望闭式 p(N)=0.9^(N+1)；kill11 闭式 P_N=(9/10)^(N+1)*(1-(5/9)^N*2^-(4-N))——禁用概率相乘：给定位置杀中时被杀位 9 个合法数字里恰 5 个落形态侧，N=4 独立相乘会高估 1.93pp。
- kill3 无闭式：10^4 开奖 × 全配置空间精确枚举，期望=命中配置数/(10^4×配置数)；配置空间 N=2/3/4 = 8/9/10 项。
- 共同语义：合尾 hold3-until-miss 保留；kill3 取消 display_only、N 位全部计入整组，弃位（0~2 个）杀码/形态字符按近 50 期字符冷热确定；kill11 非杀位形态字符仍由 AI 自选；kill1 的 hold 采用方案 B（最终位置集合恒等于 AI 本期所选 N 位，其中上期同位命中且 run<3 则该位置沿用上期封存杀码）。

## 统计机制（最大工程坑）
- 累计统计若仍按单值 (items, expected) 常量，混合 N 会被错对单一基线，delta/p/断崖全错。必须逐期 expected。
- lib/hitrate_baseline.py：LABELS + DYNAMIC_RULES 注册四个新 rule；_kill1_rows/_kill3_rows/_kill11_rows/_kill12_rows 收集 counted_rows 并传 records=，expected 取记录 expected_group_rate。
- scripts/framework_hitrate_cumulative.py：cum_kill1/cum_kill3/cum_kill11/cum_kill12 在 rule 过滤前收集 expected_map 与 items_values；_build(..., expected_map, label, items) 用加权均值，basis=per-record-exact。
- 两处共享文件必须同步改；panel.html 为每期生成物禁手改；lib/attack_editor_view.py version 串已到 all-attack-editor-v25-flexible-kill12；scripts/tail_hold_review.py 标签同步；ops/hitrate_stability_whitelist.json 逐版本登记（until=首封存+24h）；ops/README.md 记台账。

## 验证证据
- 基线三法互证：闭式、10^4 精确枚举、20 万 MC；锚点自证（kill11 v1 0.7538 复现 0.75375；kill12 v1 复现 0.75375；kill3 旧 8 项 0.58515 vs 登记 0.5863）。
- 单测：kill12 v3 26/26 PASS、kill11 v2 18/18 PASS、kill3 v17 9/9 PASS。
- 真实周期：每框架首期新封存含 position_count/expected_items/expected_group_rate/expected_basis；结算复制四字段；新代码可结算旧版本封存（kill12 v3 结算 v2 封存 group_hit=True；kill11 v2 结算 v1 记录）。
- 部署核验：scripts/crontab_tool.sh diff=matches；refresh_source_manifest --if-drift=no-drift；ops_health 各 check OK（kill3_v17_gate observe、kill3_v16_gate inactive）。
- 备份与关键提交：kill12 v3 备份 /root/backups/kill12-v3-20261107-001659/manifest.json；提交 b4ad1e1、6bcb389（kill1 v7）、6a9f1e4、fa57aa7（kill3 v17 修复/台账）、d028c9d（kill11 v2）、e31985f、4008e5e（kill12 v3+台账）。
- kill3 v17 门禁：lib/kill3_v17_guard.py（n>=150 且 z<-2.5 或 rate<0.50 → triggered）、scripts/kill3_v17_rollback.py（默认 dry-run，--execute 反向应用 commit 417c472 三文件）、scripts/kill3_v17_auto_rollback.py（cron 每 5 分钟）、基线 ops/kill3-v17-baseline.json。

## 风险与注意
- 整组期望必随 N 增大而下降；2~4 内 N=2 期望最高，AI 可能长期只选 N=2（kill1 首日 20/30 选 2）→ 只观察不强制，连续 30 期全 N=2 才复查。
- 不要用一两天命中率判断优劣或加减仓：单框架样本 8~30 期时 1σ 约 9~17pp（观察快照见 penta5-posvar/observation-20261107-0101.md）。
- 已更正的口误：放宽到 1~4 时 N=1 期望其实更高（kill1 81%、kill11 75.38%），会抬高表面命中率但降低平均位置数——这是暂不做 1~4 的理由。
- 旧版本数据隔离：累计按 rule_version 过滤，旧行归档不计入；升版白名单 until=首封存+24h。
- freeze：kill1_tail/kill3_tail 已 renew；lib/panel_context.py 未改（改会触发 killer/framework/kill1 三个 guard 重登记）；ops/freeze-guards.json 不手改。
- kill8 未纳入（手填各位置排除数字+手动启动，最近一次运行 2026-11-06 16:07；自动化需单独立项）。

## 相关文档
- penta5-runner kill12 v2 升级落地（id 7b9973e0-57b8-4343-8900-cb13d7d1eef4）
- penta5-runner 排除/杀码模型审计（id c9e2bfd1-2ae0-4441-ba88-f6d211075222）
