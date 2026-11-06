---
id: "c9e2bfd1-2ae0-4441-ba88-f6d211075222"
title: "penta5-runner 排除/杀码模型审计：随机性、期望与倍投风险（2026-11-06）"
description: "penta5 排除/杀码模型三路审计（命中率 A / 经济赔率 B / 随机性与 AI 增量 C）的完整结论与证据：3178 期随机性检验、9 框架 30 版本校正后无显著优势、9900 赔率与 ROI 表、倍投破产概率、平台/自动投注设施现状。当需要回答「这套模型有没有效、稳不稳、能不能盈利、倍投行不行」或复查审计结论时参考。"
status: "active"
created_at: "2026-11-06T08:13:05.134Z"
updated_at: "2026-11-06T08:13:05.134Z"
content_hash: "010aa918f743d47f4c31a7c47952ccbaca186ada02532b3cf7044f4031a452dd"
source_paths:
  - "penta5-audit/A-hitrate.md"
  - "penta5-audit/B-economics.md"
  - "penta5-audit/C-signal.md"
  - "penta5-audit/scripts"
session_ids:
  - "mnemon-run-v1-8c0d333f-7529-4150-901f-67fba1cfe781"
memory_body_ids:
  []
---

# penta5-runner 排除/杀码模型审计：随机性、期望与倍投风险（2026-11-06）

## 背景与结论
- 用户（自称新手）反馈：「排除模型不是很稳，实战中没盈利过」；2026-11-06 对线上 `/root/penta5-runner` 做三路只读审计：命中率（A）、经济/赔率（B）、信号/随机性（C）。
- 结论：① 开奖与 iid 均匀随机不可区分（3178 期、20+ 项检验全过）；② 9 框架/30 版本/33 组复算，经 Bonferroni 与 BH-FDR 校正后无任何显著正优势；③ 平台抽水（四定 9900 赔率 ≈1%）叠加实测命中率不足盈亏平衡 → 长期负期望；④ 倍投（martingale）把负期望放大为高破产概率，是当前最该先停的环节。
- 处置建议：先停真钱与倍投；项目侧规则冻结 ≥500 期、预登记判据、面板标注「随机基线/不可下注」、关闭不必要 AI 调用。

## 系统口径（审计前提）
- 5 位数字彩票，万千百十个 0-9，288 期/天（5 分钟一期）；`AGENTS.md`：模型只用前 4 球，第 5 球（个位）只展示，用于龙/虎（万位 > 个位=龙，万位 < 个位=琥/虎，和局各 10%）。
- 框架：kill1/kill3/kill8/kill10/kill11/kill12 + killer（智能深排 9 项）+ framework（固定八码）+ he3_refine + r1_framework；各框架独立封存，数据在 `data/<fw>_tail/{seals.json,settlements.json}`。
- 面板基线文案（`panel.html` L161）：「位置杀1码/三合尾杀1码均为 90%；四位形态排除为 93.75%；单日 n<30 波动大」。
- operation window 硬约束：`lib/ai_operation_window.py` 的 `PLATFORM_CLOSE_SECONDS=70`，预测须在开奖后 230s 内发布。

## 命中率审计 A（9 框架 / 30 版本 / 33 组复算）
- 校正后无显著正优势。killer n=3183 40.37% vs 期望 41.62%（p=0.1555，方向偏低）；framework n=3179 55.80% vs 55.59%（p=0.8165）；he3 n=495 94.14% vs 93.75%；kill1 n=340 74.41% vs 72.90%；kill3 v16 n=175 49.71% vs 58.63%（p=0.0174 名义偏低，MC 后不成立）；kill8 n=42 69.05% vs 77.25%；kill10 n=171 93.57% vs 92.02%；kill11 n=139 76.26% vs 75.38%；kill12 n=43 83.72% vs 75.38%。
- 样本量：要稳定检出 +2pp 优势需 1335–4829 期，当前多数版本 n<200。30 个 tail 版本中 27 个 n<200（中位 42.5 期）；kill3 六天换 14 版；kill1 v5/v6 仅存活 0.4h/0.1h 即回退 v4。**规则频繁变更 = 永远小样本，是「不稳」的机制性原因，不是模型参数问题。**
- 模型分档无差异：framework flash 55.77% vs pro 55.73%（Fisher p=1.0）。MC 序列检验：killer 最长连败 18 vs 同 p0 随机 p95=18（p=0.06）；kill3 v16 最长 10 vs p95=8（p=0.012，偏低）。

## 经济与赔率审计 B
- 赔率口径：仓库无平台赔率字段；唯一硬证据是已删 `lib/framework_fixed_pnl.py` docstring「A hit returns 9,900 times that single-group stake TOTAL, inclusive of principal」→ 四定 9900（抽水 1%）。
- 盈亏平衡：定位胆杀1买9 → 9.7:92.784%、9.8:91.837%、9.9:90.909%、9.95:90.452%；杀2买8 → 82.474/81.633/80.808/80.402；四定 p*=N/9900，须比均匀覆盖相对高 1.01%。
- 实测缺口：千位 90.30%（n=3173）→ 9.9 差 0.61pp（ROI −0.67%）、9.8 差 1.54pp；百位 89.23%（n=3173）→ 9.9 差 1.68pp（−1.85%）。四定各框架 ROI（9900）：killer −5.20%、framework −1.17%、he3 −0.61%、kill3 −16.06%（v16 已触发回滚门禁）、kill8 −11.51%、kill11 −0.30%；kill1 +0.85%（n=338）、kill10 +0.58%（n=169）、kill12 +8.91%（n=41），CI 均跨 0。
- 倍投：steps [150,300,600,1200] 分 = 1.5/3/6/12 元；5000 组单轮满手 ¥112,500；随机口径 4 连败 6.25%/轮，按 framework 55.8% 命中约 3.8%、killer 40.4% 约 12.6%。1000 期 EV −¥160,000（解析）/−161,377（MC，sd 669,763）；本金 1 万破产 99.79%、10 万 93.55%、50 万 55.98%，中位所需本金 ¥566,700。9 轮真实记录理论 EV −¥115.09，单轮 σ≈¥4,111。
- 教训：所谓「模拟盈亏」是 5000/9900（平衡 50.505%）与基线 55.6–92.0% 自相矛盾的假账，已被完整性校验拦下并 commit `00dd99a` 删除，不可作为盈利能力证据。

## 随机性与 AI 增量 C（n=3178 期，19.6 天）
- 四位均匀性 χ²=3.09–6.76（df=9，p=.66–.96）；转移矩阵 20 项 χ²=48.6–103.7（df=90，p≥.153）；自相关 20 项仅 2 项名义显著且方向相反；游程 p≥.0517；冷热/遗漏 140 项名义 6 项（期望 7），校正后全不显著。
- AI 无增量：kill1 AI 单注安全率 89.83%（n=1209）vs 冷10 90.07%（McNemar p=0.893）；kill3 AI 89.56%（n=2537）vs 冷10 90.19%（p=0.337）；整组 AI 79.03% vs 冷10 80.20% vs 期望 81.17%。adaptive 引擎 3179 期：SKIP 3016 / EXCLUDE 148 / HALT 15；五专家单码安全率 89.7–90.1%（p≥.33）；置信度门槛 EXCLUDE 148 期 64.87% vs 57.62%（p=.067，仅前半段为正，rho=.018 p=.30）。
- `adaptive_exclusion_sidecar.json` 为 `readOnly:true, appliesToTip:false`（SKIP，confidence 0.2918<0.36）；`penta5_backend_runner.js` L10542 注明 sidecar「never copy its digits」，不进预测链路。

## 行为层与平台风险
- 用户实战采用按手倍投；平台为聊天室式私彩（15933.cn 系，用酒店术语伪装：房费=投入、面积=盈亏、中介费=含本金返还；挂牌期号=日期+当日期数，如 1106193 = 11-06 第 193 期）。
- 服务器存在真实投注设施：`lib/four_throw_robot.py`（956 行，Selenium 把攻击语展开成 4 位码表、350 码/页逐页粘贴）+ `four_throw_urls.json`（`active=1`、`amount=1.2`，URL 含 uid/sum 参数，属敏感信息勿外泄）+ `data/four-throw-jobs/` 9 个真实 job（2026-08-24，45,079 码、流水 ¥11,508.9），但无中奖/派奖/结算记录 → 真实 ROI 无法计算。
- 2026-11-06 16:12 复核：four_throw 未在运行（cron/pm2/进程均无，最近 job 为 9 月 2 日），但配置 `active=1` 仍可被手动触发。
- 平台开奖与系统取数一致：`state.json` latest `20261106193 = 9,4,4,5,1`，与截图 186–193 期逐期吻合。

## 证据路径
- 本地 DSH 工作区：`penta5-audit/A-hitrate.md`、`penta5-audit/B-economics.md`、`penta5-audit/C-signal.md`；复算脚本 `penta5-audit/scripts/`。
- 服务器 `/root/penta5-runner`：`data/real_hitrate.json`、`data/hitrate_baseline.json`、`data/hitrate_guard.json`、`data/hitrate_stability.json`、`data/position_kill_shadow.json`、`data/cold_hit_stats.json`、`data/hitrate_attribution.json`、`data/adaptive_exclusion_sidecar.json`、`data/state.json`、`data/<fw>_tail/`、`four_throw_urls.json`、`data/four-throw-jobs/`。

## 局限与待证
- 赔率 9900 由已删代码注释推断，未获平台赔率/派奖原始记录；真实 ROI 需平台账单（投注额/中奖额/余额）。
- 多数框架 n<200，「无优势」是当前证据下的判定，非永久结论；如需前瞻验证须预登记判据（n、CI、MC 校正）。
- 平台为不受监管私彩，存在提现/限额/跑路风险，不在统计模型范围内。

## 后续（预登记建议）
- 规则冻结 ≥500 期再评估；新版本上线前写死判据；面板标注「随机基线/不可下注」；停用不必要 AI 调用（7d errors 中 invalid_response 186 次）；真钱停用、倍投禁用。
