---
id: "ff1dd0eb-a0b7-4615-8ace-405e84c12999"
title: "penta5-runner AI 总闸余额守护：自动暂停/自动恢复语义与缺期不补跑（2026-11-06）"
description: "回答「AI 总闸被关后充值会不会自动恢复、恢复后会不会补读最新数据」的取证型运维说明：唯一总闸与 generation CAS 语义、guard 阈值/确认次数、自动恢复只限自己停的版本、手动关不自动开、恢复后不补历史期；含 2026-11-06 两次自动停/恢复与 kill1/kill3 缺期实证。"
status: "active"
created_at: "2026-11-06T12:38:45.704Z"
updated_at: "2026-11-06T12:38:45.704Z"
content_hash: "a7814af50d88628abe3d0b04e35fe10f905a413b06b3c6c368d20d01e14df532"
source_paths:
  []
session_ids:
  - "mnemon-run-v1-91b0009d-cae6-4168-957e-f7edc377357b"
memory_body_ids:
  []
---

# penta5-runner AI 总闸与余额守护：自动暂停/自动恢复语义与「缺期不补跑」（2026-11-06 取证）

## 结论速查（回答「AI 总闸关了，充值后会自动恢复并读最新数据吗」）
- 余额不足导致的关闸是**自动**的：余额 < 5 元、连续 2 次确认后，guard 通过 set_enabled(False) 关掉唯一 AI 总闸。
- 充值到账后最多 1 分钟**自动恢复**：余额 ≥ 6 元（1 元回差防抖）时 guard 自动 set_enabled(True)，无需人工操作。
- 但**手动关闭的总闸不会自动恢复**：guard 只恢复「自己停掉的那一代」；期间若有人手动改过开关，则只清掉自动暂停标记、尊重手动状态。
- 恢复后**不会补历史期**：下一轮 */5 的 AI cron 自动读取最近 50 期最新开奖数据继续发布；停掉的那几期永久缺失，只影响样本数/覆盖率，不是故障。

## 机制与代码事实
- 唯一 AI 总闸：/root/penta5-runner/data/ai_api_control.json（mode=manual、enabled、generation CAS 链、transitions 审计记录）。业务代码只经 lib/ai_call_control.py 的 read_control / set_enabled 操作，不直接改 JSON；缺文件或非法状态一律视为停用。
- 守护脚本：/root/penta5-runner/scripts/ai_balance_guard.py，crontab 每分钟一次（flock -n /var/lock/penta5-balance-guard.lock + timeout --kill-after=3s 45），用 .env 的 PENTA5_FRAMEWORK_API_KEY / PENTA5_KILLER_API_KEY 读 https://api.deepseek.com/user/balance。
- 阈值常量：PAUSE_BALANCE_CNY=5.00、RESUME_BALANCE_CNY=6.00、PAUSE_CONFIRMATIONS=2、BALANCE_TIMEOUT=10（1 元回差防抖，避免 5~6 元区间抖动）。
- decide() 语义：低余额且 enabled → 累计确认次数，连续 2 次才 pause；auto_paused 为真时：generation 不一致 → clear-marker；仍 disabled 且余额 < 6 → 等待；≥ 6 → resume；auto_paused 为假（手动关）→ 返回 none，不会自动开。
- 恢复后行为（脚本 docstring 原文）：「恢复后不补历史期、不回放旧数据；既有 */5 的 AI cron 在下一轮自动读取『最近 50 期』最新开奖数据继续发布。」

## 2026-11-06 实证
- 自动循环 1：08:00:01 余额 4.38 → pause（generation 83→84）；08:14:01 余额 31.79 → resume（84→85）。
- 自动循环 2：14:26:01 余额 4.59 → pause（88）；14:39:01 余额 24.37 → resume（89）。两次时间戳都落在整分第 1 秒，即 guard 的 cron 动作。
- 手动对照：11:31:52 off / 11:34:51 on（generation 86/87，非整分时间戳，走面板 /api/ai-control → scripts/ai_api_control.py），这类不会自动恢复。
- 缺期去留：14:26–14:39 停 13 分钟里，data/kill1_tail/settlements.json 缺 2 期，恢复后 14:40:59 直接从当时最新一期续发；data/kill3_tail/settlements.json 缺 3 期，14:42:45 续发。按 published_at 排序核对当日 target_period 无乱序 = 无事后回填。
- 缺期会计入统计而非故障：kill3 coverage 93.40%（19/288 缺）、hitrate_stability 的 manual_off_minutes（24h 30.7 分钟/5 个关闸窗口）都可在面板/体检中解释；排查缺期先看是否落在停闸窗口，窗口外再查发布延迟（operation window 230s）与任务失败。
- 状态快照（20:35）：enabled=true、generation=89、auto_paused=false、last_balance=55.36 元；按当时约 3–4 元/小时消耗粗算，十几个小时后可能再次触达 5 元自动暂停（快照数据，会变，勿当长期事实）。

## 运维建议
- 想省钱就让它自动停，不要手动关；手动关后充值不会自动开，需要回面板点开。
- 充值后 1 分钟内恢复；预测在下一轮 cron 直接用最新开奖继续，无需重启、导数据或手动刷新。
- 缺期解释顺序：先看是否落在停闸窗口（本条），再看发布延迟与任务失败；不要归因于面板登录/多人查看（只读，不写库、不调模型）。
- 可选改进（未实施）：面板顶部加「AI 总闸：开启 / 手动关闭（充值不自动开）/ 余额暂停（充值后自动恢复）」状态提示，减少新手误解。

## 相关文档
- 66b9c4b9-9241-4056-98f3-2fe2827de53c：kill3 面板命中率「断崖」诊断，含 11-02/11-04 长时间停闸与缺期归因（本文不重复其命中率分析）。
- c9e2bfd1-2ae0-4441-ba88-f6d211075222：penta5-runner 排除/杀码模型审计（随机性、期望与倍投风险）。
