---
id: "caeb51b2-f2a1-4b50-871d-40d9a71d210c"
title: "penta5-runner cold30 / cold15：定位冷码独立框架与每位置单码命中率标注（2026-11-10）"
description: "cold30/cold15 两个纯确定性四位置定位冷码框架的口径、精确基线 0.6561、代码/数据/挂接点、每位置单码命中率标注实现、部署验证流程、真实周期与提交回滚。回答「cold30/cold15 怎么算/怎么上线/命中率标注怎么做的/怎么回滚」时参考。"
status: "active"
created_at: "2026-11-10T08:01:10.927Z"
updated_at: "2026-11-10T08:01:10.927Z"
content_hash: "3c733b02f45804eca31decd315b5f347882257923066c21cc2a8cda1469eebbd"
source_paths:
  - "cold-framework-build"
session_ids:
  - "mnemon-run-v1-4a93d366-3b78-47ff-9d6e-f44fa225008b"
memory_body_ids:
  []
---

# penta5-runner cold30 / cold15：定位冷码独立框架 + 每位置单码命中率标注（2026-11-10）

## TL;DR
- 用户先后两条需求：m00001「帮我新建俩个冷码框架，最新30期定位冷码，最新15期定位冷码」；m00439「单个位置给我标识命中率」。
- 2026-11-10 上线两个纯确定性、独立封存/独立结算的四位置杀码框架：**cold30**（滚动最近 30 期）与 **cold15**（滚动最近 15 期）。每期对千/百/十/个各杀「最冷 1 码」，整组固定 4 项，4 项全中才算整组命中。
- 精确随机基线（10^4 开奖空间 × 10^4 配置空间 = 10^8 pairs，numpy 向量化枚举，与闭式 9^4/10^4 交叉校验）：整组命中率 = **0.6561**（6561000000/10^8），结果文件 /tmp/cold-build/expected_enum.json。
- 2026-11-10 15:57 给两框架每个位置卡加「单码命中率 本日/本版累计」标注（提交 57b51bc），纯展示不改规则。
- 相关文档：kill14 v2 也把冷码窗口 30/15 并入定位排除（文档 id `02a2155b-94cf-4035-b8c6-a8930c160050`），但二者是不同框架、独立账本，互不计入。

## 口径（写死，勿改）
- 候选位置固定四位置：POSITION_KEYS=('q','b','s','g')（千/百/十/个），各取最冷 1 码；TOP_N=1；EXPECTED_ITEMS=4；ATTACK_ORDER='1234567890'；攻击语形态 `千<9码>百<9码>十<9码>个<9码>四定`。
- 冷度排序：窗口内出现次数升序 → 当前遗漏降序 → 码升序；第 1 冷即杀码。
- 窗口：滚动最近 30 / 15 期（用前 4 球，第 5 球不参与）；每期严格重算，不 hold、不设上限。
- 命中：某位置实际开奖号 **不在** 该位置杀码集合（1 个码）内即该位置命中；整组命中=4 位置同时命中；随机期望 (9/10)^4 = 0.6561。
- 纯确定性，不调用 AI；平台提前 70 秒关盘（CLOSE_SECONDS=70）；业务日 05:00 清零。
- RULE_VERSION：`cold30-tail-v1-four-position-cold` / `cold15-tail-v1-four-position-cold`；EXPECTED_BASIS='enumerated-10^4-draws-x-10^4-configs'。

## 代码与数据（服务器 /root/penta5-runner）
- `lib/cold30_tail.py`（520 行）/ `lib/cold15_tail.py`：口径/封存/结算/统计。关键函数：build_candidates(rows,window)→{key:[10 ranked]}；select_cold_positions(rows,window)；create_seal(...)（input_sha256=窗口 rows 前 4 球 JSON sha256，可独立复算）；save_seal（重复期抛 PublicationRejected('existing-cold30-seal-cannot-be-replaced')，只追加）；mark_published/mark_publication_failed；settle_pending(snapshot,now)（verified=state=='published' and published_at<drawn_at）；today_stats（业务日 05:00 起）。
- 视图：`lib/cold_thirty_view.py`（section id=cold-thirty-ai，COLD_THIRTY_START/END，CSS 前缀 c30-）/ `lib/cold_fifteen_view.py`（cold-fifteen-ai / COLD_FIFTEEN / c15-）。
- 运行器：`scripts/run_cold30_tail.py` / `scripts/run_cold15_tail.py`（WAIT_SECONDS=55、flock data/.cold30_tail_job.lock、发布面板 apply_cold_*_block）。
- 数据（.gitignore，只增）：`data/cold30_tail/{seals,settlements}.json`、`data/cold15_tail/{seals,settlements}.json`。
- 定时：`ops/crontab.txt` 两条 — cold30 `*/5 * * * *` flock /var/lock/penta5-cold30t.lock + sleep 55 + timeout 220 跑 run_cold30_tail.py（日志 data/cold30_tail_cron.log）；cold15 同构 sleep 60 / 锁 penta5-cold15t.lock / data/cold15_tail_cron.log。日志在 /etc/logrotate.d/penta5。

## 挂接点（新增同类框架时按此清单，共 9 文件）
- `/root/run_stats.py`（仓库外运维文件）：import 两 view，early_panel/quiet_panel 两条 lambda 链接入两个 apply。
- `lib/hitrate_baseline.py`：RULE_BASELINE 加 (4,0.6561) 两条、LABELS、克隆 _cold30_rows/_cold15_rows、build extend。
- `scripts/framework_hitrate_cumulative.py`：RULE_BASELINE/LABELS/WINDOWS/cum_cold30/cum_cold15/funcs。
- `scripts/ops_health_check.py`：LEDGER_SIZE_FILES/LEDGER_ALERT_FILES/check_cold30/check_cold15/CHECK_FUNCS/FALLBACK_NAMES（7 分钟宽限）。
- `lib/panel_live_refresh.py`：names 加 COLD_THIRTY/COLD_FIFTEEN（版本串升版时保留）。
- `lib/attack_editor_view.py`：definitions 加 cold30/cold15 两条。
- `lib/evidence_backup.py`：JSON_DIRS 加 cold30_tail/cold15_tail。
- `ops/crontab.txt` + `/etc/logrotate.d/penta5`：见上。
- 有意跳过：lib/panel_context.py、lib/model_usage.py、scripts/tail_hold_review.py（纯确定性、无 AI、无 hold）。

## 每位置单码命中率标注（2026-11-10 15:57，提交 57b51bc）
- 需求 m00439「单个位置给我标识命中率」：每个位置卡在「保留码」行后加一行 `单码命中率 本日 H/T（R%） · 本版累计 H/T（R%）`。
- 口径：本日 = 业务日 05:00 起 today_stats().per_position 的 bets/hits；本版累计 = settlements 中 verified is True 且 rule_version 等于当前 RULE_VERSION 的记录按 key(q/b/s/g) 计 bets/hits；两者皆空则不渲染该行（原卡片逐字节不变）；保留两位小数。
- 实现（两个 view 同构）：新增 `_position_history(stats)`、`_position_hit_line(history,key)`；`_position_card(item, history=None)` 追加 hit line；render_block 里 `history=_position_history(stats)`；CSS `#cold-thirty-ai .c30-hit{...}`（c15 用 c15-hit）。
- 纯展示，不改封存/结算/规则/攻击语；无新面板区块，故不升 panel-live-refresh 版本（COLD_THIRTY/COLD_FIFTEEN 已在 names 名单）；`ops/freeze-guards.json` 未登记 cold 相关文件，无需 freeze update。
- 真实周期读数示例（data-as-of=20261110191，当日 16 期已核验，框架当天上线故本日=本版累计）：cold30 千16/16、百12/16(75%)、十16/16、个16/16；cold15 千16/16、百13/16(81.25%)、十14/16(87.5%)、个15/16(93.75%)。

## 部署与验证流程（可复用）
- 上传前先本地改副本（工作区 cold-framework-build/build/），用锚点补丁脚本逐处 count==1 断言；上传后远端 sha256 与本地一致 + py_compile。
- 视图 A/B：/tmp/coldhit/ab_test.py 去掉新增 hit 行后与旧渲染逐字节相等 + 数据独立复算一致 + div 配平（注意：该脚本曾对 render_block 输出断言 START/END 而误报，标记只在 apply 注入）；/tmp/coldhit/apply_test.py 验证 apply 后 START/END 各 1、旧内容被替换、div/section 配平。
- 真实周期验证：等 cron 自行重建面板后 `scripts/check_cold_frameworks.py`（只读自检，两框架各 45 项，共 90 项；任意 cwd 可跑，按 __file__ 推导仓库根）→ `SUMMARY: fails=0`；`scripts/ops_health_check.py` 看 cold30_tail/cold15_tail/panel_html；改源码后 `python3 scripts/refresh_source_manifest.py --root /root/penta5-runner --if-drift` 刷新证据基线（否则 manifest_drift FAIL）。
- 精确基线计算：numpy 枚举 10^4 draws × 10^4 configs，勿用概率相乘；纯 python 双循环会超 run_code 120s（实测 numpy 3.1s）。

## 真实周期与提交（证据）
- 首两期：ref 20261110175→176 published 14:35:57；176→177 published≈14:45:57；随后 179→180、180→181 等连续由 cron 发布，verified True。
- 提交链：95b6dba（三件套 4 files 1274 insertions）→ fef3003（9 文件 1026 insertions 挂接）→ 43ec98b（README 台账+自检脚本）→ 5293e9f（自检脚本任意 cwd 可跑）→ 6e9469d（部署落地台账）→ 57b51bc（位置命中率）→ 2671da7（命中率台账）；最终 HEAD=origin/main=2671da7，AHEAD=0/BEHIND=0。
- 回滚：框架 `git revert` 对应挂接提交（或删 crontab 两行 + 停用视图链接）；命中率标注单独 `git revert 57b51bc`（纯展示，无数据迁移）。原始视图文件与取证在 /root/ops/cold-hitrate-20261110-1554/{old,scripts}/，备份 /root/ops/cold30-cold15-20261110-143256/。
- 并行窗口：本批只提交自己的文件（视图 2 个 + README 台账），另一窗口 kill11/kill14 的在途 M 文件从未 add/commit；推送到 origin/main 前先看 HEAD 是否被并行窗口推进，用 ours-only `git add` 指定路径。

## 注意事项
- `ops/README.md` 台账用独立 commit（与代码提交不同 hash），每批追加 marker 小节；提交后再刷 manifest 基线，否则 ops_health manifest_drift 会 FAIL。
- panel.html 是运行时动态重生成（skip-worktree），只改 panel.html 无效，必须改视图模板。
- 自检脚本坑：hitrate_baseline 注册表变量名是 RULE_BASELINE 不是 BASELINE；scripts/ 下同名 hitrate_baseline.py 需让 sys.path 优先 lib。
