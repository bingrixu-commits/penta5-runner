---
id: "df30b2dd-5ca2-48a0-890a-25570f0bbad3"
title: "penta5-runner kill13 冷合尾 2 项框架（口径/基线/部署落地/回滚）"
description: "kill13（冷合尾 2 项，纯确定性）的口径、精确基线 0.8082、9 处挂接点与 3 处跳过理由、2026-11-07 真实周期与部署落地审计证据、备份与回滚；回答 kill13 怎么选出两个最冷合尾、沿用规则、基线怎么来、上线/回滚怎么做时参考。"
status: "active"
created_at: "2026-11-06T17:09:42.225Z"
updated_at: "2026-11-06T17:09:42.225Z"
content_hash: "edff70c9294c6581b03df9b3738d891ca57ac7c2a71fec75439e86c0d3525933"
source_paths:
  []
session_ids:
  - "mnemon-run-v1-aa324421-45ef-45f9-a962-380e3eb47155"
memory_body_ids:
  []
---

# penta5-runner kill13：冷合尾 2 项框架（口径 / 基线 / 部署落地 / 回滚）

## TL;DR
- 用户 m00001「帮我弄一个合尾框架，结合50期数据，取俩个最冷合尾。站不动的，不冷在换。」→ 新增 kill13「冷合尾 2 项」框架（纯确定性、不调 AI、0 API 成本），2026-11-07 00:40 起真实封存。
- 50 个候选 = 5 种合尾组合 × 尾码 0-9；窗口 50 期；每期取最冷 2 个；沿用规则 cold-hold-top2：上期 2 项仍在本期冷榜前 2 就沿用，掉出就换，保留 kept/swapped 轨迹。
- 精确随机期望 0.8082（10^4 开奖 × C(50,2)=1225 配置精确枚举 = 990/1225 = 0.80816326…），不是 0.9^2=0.81；原因是两项不重复抽样。
- 2026-11-07 01:02–01:06 完成「部署落地」审计：线上真实运行（6 条封存 / 5 条结算 / 今日 5/5）、面板自动追平、ops_health kill13 OK、自检 25/25、未落地项=无。

## 口径（v1 现行）
- 合尾定义：指定位置子集求和 mod 10；只用前 4 球（千/百/十/个），第 5 球不参与（与 kill8/kill11/kill12 一致）。
- 5 种组合：tail_all (0,1,2,3)=千百十个；tail_qbs (0,1,2)=千百十；tail_qbg (0,1,3)=千百个；tail_qsg (0,2,3)=千十个；tail_bsg (1,2,3)=百十个。候选池 = 5×10 = 50。
- 冷序：窗口内出现次数升序 → 当前遗漏降序 → 组合顺序 → 尾码；窗口 WINDOW=50。
- 取最冷 2（TOP_N=2）。沿用：每期重算 50 期冷榜，上期封存的 2 项只要仍在本期冷榜前 2 就原样沿用；掉出前 2 的那项换成本期前 2 里缺的项；无连用上限。
- 设计提醒：整组只有 2 项且沿用门槛=前 2 ⇒ 选码结果恒等于「每期取本期冷榜前 2」，kept/swapped 只用于面板/审计轨迹。若嫌换码频繁，可把沿用门槛放宽到前 3/前 5，但那会改变选码 → 必须新 rule_version + 新基线。
- 常量：RULE_VERSION=kill13-tail-v1-two-cold-hewei；HOLD_POLICY=cold-hold-top2；WINDOW=50；TOP_N=2；CLOSE_SECONDS=70。
- 攻击语：四个位置全保留 10 码 → 「四定」→ 两个合尾标签 + 各自剩余 9 码（与 kill12 的尾段同构，ATTACK_ORDER=1234567890）。

## 整组基线与枚举自证
- 方法：10^4 开奖 × C(50,2)=1225 个配置精确枚举（禁止单项概率相乘）：both_hit_pairs=9,900,000，rate=0.8081632653061225，round4=0.8082；逐开奖命中候选数 h 恒为 45，分布 {45:10000}；formula 990/1225 一致。
- 脚本 scripts/enumerate_kill13_baseline.py 输出 data/kill13_tail/baseline_enumeration.json；OK 条件：configs==1225 && hit_count_is_constant_45 && formula_matches && rate_rounded4==0.8082。
- 登记两处 (2, 0.8082)：lib/hitrate_baseline.py 与 scripts/framework_hitrate_cumulative.py；封存/结算写 expected_items=2、expected_group_rate=0.8082、expected_basis=enumerated-10^4-draws-x-1225-configs。

## 文件清单与挂接点
- 新增：lib/kill13_tail.py（数据/冷榜/选码/封存/结算/统计）、lib/kill_thirteen_view.py（面板区块）、scripts/run_kill13_tail.py（cron runner）、scripts/enumerate_kill13_baseline.py、scripts/kill13_verify_online.py（只读 25 项自检）、data/kill13_tail/。
- 9 处挂接：/root/run_stats.py（import + 两条 update_panel lambda 链各插 apply_kill_thirteen_block）；lib/hitrate_baseline.py；scripts/framework_hitrate_cumulative.py；scripts/ops_health_check.py（check_kill13 + LEDGER_SIZE_FILES/LEDGER_ALERT_FILES）；lib/evidence_backup.py（JSON_DIRS）；lib/attack_editor_view.py（v26 + kill13 攻击语条目）；lib/panel_live_refresh.py（v32 + names 含 KILL_THIRTEEN）；ops/crontab.txt；/etc/logrotate.d/penta5。
- 有意跳过 3 处及理由（已登记 README）：lib/panel_context.py（kill13 不调 AI、无 AI 输入）；lib/model_usage.py（无 AI_RESPONSE_META 日志，不注册 PROVIDER_402_LOGS）；scripts/tail_hold_review.py（kill13 的沿用是冷榜 top2 沿用，不是命中 hold3 语义）。kill13 无 freeze guard（无冻结账本）。
- cron：*/5 * * * * flock -n /var/lock/penta5-kill13t.lock … sleep 50 … timeout --kill-after=5s 220 python3 -u scripts/run_kill13_tail.py >> data/kill13_tail_cron.log；日志名五处一致 data/kill13_tail_cron.log。
- 面板：section id=#kill-thirteen-ai，标记 KILL_THIRTEEN_START/END；标题「❄️ 50 期冷榜 · 两个最冷合尾建议」；区块 data-as-of = seal.ref_period；面板刷新版本串 quiet-panel-v32-add-kill13-tail。

## 上线与部署落地证据（2026-11-07）
- 提交（5 个，均已推 origin/main）：978c3d2 三件套+枚举脚本；24fc20c 派生统计/健康检查/证据/面板挂接；fad5159 上线台账；c60c565 自检脚本入库；9b5d2bb 部署落地台账。审计时 HEAD=origin/main=9b5d2bb，工作树干净，AHEAD/BEHIND=0/0。
- 真实周期：首封存 2026-11-07T00:40:52（ref 20261107008→009）；01:00:53 ref=20261107012→013，kept1/swapped1（首次换码：千十个合尾 杀0 掉出前 2 → 千百十个合尾 杀2 补入）；01:05:53 ref=20261107013→014，kept2。截至 01:08 封存 6 条、结算 5 条、今日 5/5（样本<200 仅供参考），面板 01:08:01 后 data-as-of=20261107013。
- 门禁：ops_health kill13_tail OK（ref=20261107012 published_at=01:00:53）；manifest no-drift（auto-baseline-20261107-010205）；crontab_tool.sh diff = matches（45 行）；自检 25/25；三件套+自检脚本 commit=磁盘 sha256 相等。
- 承载方式：pm2 penta5-panel-sync 在每期 new-draw 后起新解释器跑 /root/run_stats.py；后端 penta5_backend_runner.js:15226 按 panel.html 路径读取、:15232 按 mtime 判断重读 ⇒ 改面板/挂接无需重启后端。3010 后端 + nginx 3001 反代结构未改。
- 派生统计滞后属正常节拍：framework_hitrate_cumulative 每小时 7/22/37/52 分；hitrate_stability 13/28/43/58 分；hitrate_baseline 每期更新。
- 备份与回滚：改前备份 /root/ops/kill13-20261107_003532/（9 个共享文件 + /root/run_stats.py + crontab + logrotate，含 manifest.json before/after sha256）；crontab 备份 /tmp/crontab.backup.20261107_003757；logrotate 备份 /root/ops/deploy-audit-20261107/penta5.logrotate.bak-005943；审计报告 /root/ops/deploy-audit-20261107/deploy-audit.md（123 行）。回滚：git revert --no-edit 9b5d2bb c60c565 fad5159 24fc20c 978c3d2 && git push origin main；或从备份 files/ 覆盖 + crontab/logrotate 备份恢复；仅停跑=注释 ops/crontab.txt kill13 行后 scripts/crontab_tool.sh install。

## 坑与注意事项
- 面板对 kill13 的 data-as-of 会短暂落后一期：seal 在开奖后 50s 发布，run_stats 随后重生成面板（如 01:05:53 封存，01:08:01 面板才追到 013）；下一期自动追平，不是故障。
- 并行 DSH 窗口共享同一台服务器与同一 git 工作区：只 git add 自己文件、禁止 git add -A；改共享文件必须叠加在其它窗口未提交改动之上并把叠加事实写进台账。
- kill13 无 AI：不要给它注册 PROVIDER_402_LOGS / model_usage，否则会误报「无 AI 日志」或影响健康检查。
- 放宽沿用门槛会改变选码 → 新 rule_version、新基线、新实时验证期；旧封存按自身 rule_version 结算，不要改写历史。

## 参考
- 既有同构框架文档：kill12 v2（id 7b9973e0-57b8-4343-8900-cb13d7d1eef4）；四框架灵活位置改造总览（id ea2d6c9e-1e38-4d1a-856b-3d4dfe4745bf）。
- 线上路径：/root/penta5-runner/lib/kill13_tail.py、lib/kill_thirteen_view.py、scripts/run_kill13_tail.py、scripts/enumerate_kill13_baseline.py、scripts/kill13_verify_online.py、data/kill13_tail/{seals,settlements,baseline_enumeration}.json。
