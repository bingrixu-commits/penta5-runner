---
id: "2cb7a64d-2522-48af-b2c0-8ffc890c932e"
title: "penta5-runner：kill3 纸面跟投账本 + v16 门禁自动回滚落地（2026-11-06）"
description: "kill3 纸面影子账本（四定 9900 赔率纸面结算、面板只读块、cron 每 5 分钟增量）与 v16 预登记门禁自动回滚执行器的实现/口径/运维/复用要点；含「一跟就断崖」的 MC 证据与回答「模型为什么不赚钱」的可复用口径。当需要给排除/杀码框架加纸面跟投账本、把预登记门禁升级为自动回滚、或解释追热/累计命中率断崖时参考。"
status: "active"
created_at: "2026-11-06T10:02:17.697Z"
updated_at: "2026-11-06T10:02:17.697Z"
content_hash: "d75285f6bec0195d51f01891f8ca7f679e435e9737399ef068042899579253d5"
source_paths:
  []
session_ids:
  - "mnemon-run-v1-decd1db7-75bb-4d1c-8ca5-062afceb12dd"
memory_body_ids:
  []
---

# penta5-runner：kill3 纸面跟投账本 + v16 门禁自动回滚落地（2026-11-06）

## TL;DR
- 本批交付两件只读/自动化护栏：**(B) kill3 纸面跟投账本**（每期按攻击语剩余码各买 1 单位、四定 9900 赔率纸面结算，面板只读块展示，绝不下真实注）与 **(A) v16 预登记门禁自动回滚执行器**（guard 触发后 cron 自动执行已预登记回滚，失败退避 5 次后 give up 并报 CRITICAL）。
- 为什么这套排除模型「实战没盈利」：命中率被自身覆盖约束，而**盈亏平衡命中率 = 攻击语平均覆盖 / 0.99**（平台四定赔率 9900 含本金、抽水约 1%）。2026-11-06 18:00 实测：v16 n=197、命中 101=51.27%、平均覆盖 58.50% → 盈亏平衡 **59.09%**；固定 1 单位/码纸面 ROI **−13.24%**，全历史（v1 起 1218 期）ROI **−1.37%** ≈ 平台抽水。阶梯倍投 1→2→3→4 把纸面亏损与最大回撤放大到约 2.1 倍（−342,597 单位 / 回撤 358,770 单位）。
- 「一跟着命中率就断崖」= 追高（只在显示高点入场）+ 累计均值的均值回归 + 展示口径清零/缺期叠加，不是模型被跟崩（预测在 cron 独立进程、开奖前封存 prompt_sha256/input_sha256、开奖后结算，无用户输入反馈回路）。

## 「跟着就断崖」的可复现证据（2026-11-06 18:00 只读复算）
- 纯随机 MC（20,000 条、每条 197 期、p 取 v16 真实 51.27%）：平均每条出现 **24.8 次**「20 期窗口 ≥65%」热段；热段后 20 期命中率 = **51.2%**（= 总体基线，无热手）；**21.1%** 的纯随机序列累计命中率会先冲上 ≥65% 再必然回到 51.3%，显示上就是约 14pp 断崖。
- 用理论覆盖 58.53% 做随机 MC：**53.4%** 的序列会在某刻把累计命中率顶到 ≥65%，随后回落到 58.5%（约 6.5pp 断崖）。
- 热手检验：v16 中「过去 20 期 ≥65%」之后 20 期平均仅 **48.3%**（27 次信号）；按版本看前向命中率 0.48–0.73 乱跳、无一致性 → 无可用热信号。假设「显示累计 ≥50%/51% 才开始跟」：后续命中率 54.9%/54.5%，仍远低于 59.09% 盈亏平衡。
- 历史 70% 高点的归因见关联文档（70% 是 n≈95 的小样本累计高点，11-05 收盘 59.38%）。

## (B) 纸面跟投账本：实现与口径
- 结算语义：对每期封存，按 10^4 全空间精确枚举攻击语剩余码集——排除条件：①计入位置（key in positions 且 key != display_only）等于杀码；②合尾（tail key 位置子集和值个位 == kill，TAIL_SETS 覆盖 tail_all/qbs/qbg/qsg/bsg）；③three_avoid 每组 3 数字全部出现；④单双形态 == reverse_parity；⑤大小形态 == reverse_size（大 >=5）。剩余码数 N，命中 = 实际 4 球码落在集内（与结算 group_hit 逐期互证）。
- 盈亏模型：每期 N 码各买 1 单位（投入 N），命中返 PAYOUT=9900 单位、未中 0；另给 LADDER=(1,2,3,4) 阶梯倍投（连败封顶 4 倍、命中归 1）压力测试与 UNIT_CNY=0.1 折算；ROI=(9900·hits−stake)/stake。
- 文件：`scripts/kill3_shadow_ledger.py`（按 target_period 增量缓存，ledger_version/semantics_version 变更才全量；首轮 25.6s、增量 0.63s；原子写 `data/kill3_shadow_ledger.json`，含 current/overall/by_version/sanity/rows）；`lib/kill3_shadow_view.py`（START `<!-- KILL3_SHADOW_START -->` / END `<!-- KILL3_SHADOW_END -->`，块插在 `<!-- KILL_THREE_END -->` 之后）；`scripts/update_kill3_shadow_panel.py`（原子替换 panel.html、0o644、幂等）；`lib/panel_live_refresh.py` 版本 bump `quiet-panel-v31-add-kill3-shadow` 且 names 数组加 `KILL3_SHADOW`。
- 调度：`ops/crontab.txt` 新增 `4,9,14,...,59 * * * *`，`sleep 30` 后跑 ledger + updater；flock `/var/lock/penta5-kill3shadow.lock`，墙钟 120s/20s，日志 `data/kill3_shadow_cron.log`。
- 验证：1204+ 期复算 vs 结算 group_hit **0 mismatch**；首个真实周期 16:49:31 自动跑（computed=1、面板 updated）；run_stats 16:50/16:51 重生成 panel.html 后块仍在（KILL3_SHADOW_START/END 各 1，data-as-of 随期推进）。commit `bb85aa2`（6 files，+687 行）。

## (A) v16 门禁自动回滚：实现与行为
- 判据已预登记在 `ops/kill3-stability-plan.md` §9：n>=150 且（z<−2.5 或命中率<50%）触发。`lib/kill3_v16_guard.py` 产出 `data/kill3_v16_guard.json`（status inactive/observe/passed/triggered）。
- `scripts/kill3_v16_auto_rollback.py`：仅在 guard.status=='triggered'、现行 RULE_VERSION==v16、非 done/gave_up、距上次尝试 >= min_interval_seconds(600) 时，以 `sys.executable scripts/kill3_v16_rollback.py --execute` 执行回滚；成功=rc0 且 RULE_VERSION 变 v15 → state done；失败 attempts 累加，5 次后 gave_up 并 CRITICAL 停止；状态原子写 `data/kill3_v16_auto_rollback_state.json`，锁 `data/kill3_v16_auto_rollback.lock`（fcntl LOCK_EX|LOCK_NB）。配置 `data/kill3_v16_auto_rollback.json`（enabled=true、timeout_seconds=300）。
- 调度：`ops/crontab.txt` 行 `*/5 * * * *` + flock `/var/lock/penta5-kill3autoroll.lock` + timeout 300，日志 `data/kill3_v16_auto_rollback.log`。
- 验证：临时根 + stub 回滚脚本 10/10 PASS（disabled/guard passed 不动作、triggered→done、失败→retrying→gave_up 且不再重试、已 v15→done 不执行、600s 间隔、外部锁）；真实根无动作跑 + `kill3_v16_rollback.py` dry-run 反向补丁预检 OK。commit `a52594f`。

## 运维与复用要点
- crontab 版本化：改 `ops/crontab.txt` 后 `bash scripts/crontab_tool.sh install`，再 `bash scripts/crontab_tool.sh diff` 应零差异（当前 39 jobs）。
- 改仓库源码后：`python3 scripts/refresh_source_manifest.py --root /root/penta5-runner --if-drift`（注意必须带 `--root`），避免 ops_health 报 `manifest_drift` 的 matching-source-manifest-unavailable。
- 面板改动后旧页面需 Ctrl+F5 强刷一次（live-refresh 版本串已 bump）；该块只由 updater 写入，run_stats 重生成面板不会覆盖。
- 回滚纸面账本：`git revert bb85aa2` + crontab 去 kill3shadow 行并 install + 手工删 panel.html 的 KILL3_SHADOW 块（移除 cron 后不会自动重建）+ 可整体删除 `data/kill3_shadow_ledger.json`。回滚自动执行器：`git revert a52594f` + crontab install + 改 `data/kill3_v16_auto_rollback.json` 的 enabled=false。
- 新框架照抄三件套：账本脚本（精确枚举剩余码集 + 赔率结算 + sanity 对照结算 bool）→ view 模块（START/END + 锚点注入）→ panel updater（原子替换 + 幂等）；再把新块名加进 `lib/panel_live_refresh.py` 的 names 并 bump version，最后挂 cron 与 README 台账。
- 回答用户「模型为什么不赚钱」的可复用口径：①命中率 vs 盈亏平衡（覆盖/0.99）；②纸面 ROI（含抽水）；③n≥300 且预登记判据才可下结论；④「热信号」无预测力（前向≈基线）；⑤倍投只放大回撤。

## 数据与时间
- 服务器 root@8.210.5.221:/root/penta5-runner（Ubuntu 22.04），时间 2026-11-06 16:40–18:00 CST；HEAD 含本批 a52594f、bb85aa2；同窗口其他提交 2a91c4f/8fd34d4/b301e91（kill3 推理档 high→medium 验证）。
- 截止 18:00：`data/kill3_v16_guard.json` passed n=184→197 区间、z≈−2.05～−2.29、未触发回滚；账本 current n=197、hits=101、rate=51.27%、coverage=58.50%、breakeven=59.09%、pnl=−152,607 单位、ROI −13.24%、最大回撤 169,168 单位、最长连败 10 期。

## 关联文档
- 断崖/70% 高点诊断：`66b9c4b9-9241-4056-98f3-2fe2827de53c`（penta5 kill3 面板命中率「断崖」诊断）
- 三路审计（随机性/期望/倍投）：`c9e2bfd1-2ae0-4441-ba88-f6d211075222`（penta5-runner 排除/杀码模型审计）
