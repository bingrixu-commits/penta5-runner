---
id: "03ec4e06-aa1a-423d-9db5-b47ad011c747"
title: "Mnemon 记忆空间启用与创建（本机 DSH，含根因取证）"
description: "本机 DSH 开通 Mnemon 记忆空间的完整记录：providerReady/View 按轮组装导致创建失败的根因、安装 CLI→新轮→创建→激活→导入→复验的操作流程、本次落成的两个空间与 pitfalls。当需要重建/排查记忆空间、向空间写入或解释创建按钮灰掉/工具报 manage-spaces 未提供时参考。"
status: "active"
created_at: "2026-11-06T00:16:26.957Z"
updated_at: "2026-11-06T00:16:26.957Z"
content_hash: "3cea350c6143f1f1781011e3bfd31c9376d495e90cf552c34c80f3f265d4067a"
source_paths:
  []
session_ids:
  - "mnemon-run-v1-bb6f4fb4-4493-4a0b-9aa5-bb1690680042"
memory_body_ids:
  []
---

# Mnemon 记忆空间在本机启用与创建（含根因取证）

## 背景
2026-11-06 本机（Windows 11，DSH 桌面版，profile=desktop）用户要求把 Mnemon 记忆空间开通。起始状态：记忆空间 0 个、mnemon_status.commandFound=false（CLI 未装）、热记忆 MEMORY.md 接近上限且已有 5 条被归档进本地文件（此时没有可写空间）。

## 根因（为什么一开始创建失败）
- providerReady 判据：status.commandFound === true || providerServices.some(p => p.enabled && p.configured)（dsh-mnemon-source-memory-spaces/lib/index.js:49）；默认 provider 优先 native，native 要求 CLI 可执行（:343-345、:351-354、:1199-1202 的 live getter）。
- 记忆 View 的动作清单在 facts() 生成（同文件 :3999-4031）：actionIds 仅在 writeEnabled && providerReady 时才含 manage-spaces；否则创建/更新会报 "Source Action is not offered by the current View: memory-spaces/manage-spaces"。
- 记忆 View 每个根 turn 开头组装一次：dsh-mnemon/lib/index.js 的 ComposableMemoryTurnManager.beginTurn → lease.generation.compose（:6975-7027）；子代理带 viewId 时 pinTurn 继承父轮视图（:7338，注释 :7039 "A delegated child keeps the original grant and generation"）→ 装 CLI 与创建不能在同一轮内完成，必须开新轮。
- 同一规则也解释 "memory View Route call budget is exhausted" / "Source Route is not offered by the current View"（inspect/recall 路由与预算按轮计），换新轮即重置。

## 操作流程（本机已成功复现）
1. 装 CLI：npm install --global @mnemon-dev/mnemon@latest（本机 0.2.13；C:\Users\Administrator\AppData\Roaming\npm\ 下 mnemon / mnemon.cmd / mnemon.ps1；mnemon.cmd 是指向 node_modules/@mnemon-dev/mnemon/bin/mnemon.js 的 npm launcher，符合 native-cli.js:28-41 的识别条件；CLI 无 version 子命令）。
2. 开新一条消息（新轮）让 View 重组；mnemon_status 应显示 commandFound=true、writeEnabled=true。
3. 创建：mnemon_memory_body_create({name, description})；manual 策略下不要传 providerId/reason/confidence（会报 provider placement conflicts）。第一个 native 空间自动取 id "default"，之后取 UUID；description 写清「存什么 + 何时召回」。
4. 激活：mnemon_memory_body_update({memoryBodyId, active:true})（新建默认 active=false；成功写入也会自动激活）。
5. 写入：mnemon_remember({memoryBodyId, content, category, tags, entities})；有多个激活空间时必须显式传 memoryBodyId。
6. 复验：mnemon_status（memorySpaces total/active/healthy、aggregate.totalInsights / oplogCount / edgeCount / dbSizeBytes）→ mnemon_memory_bodies（items 的 active、providerEnabled）→ 磁盘 C:\Users\Administrator\.mnemon\data\.dsh-memory-bodies.json（{"version":1,"bodies":[...]}，含 active:true）与 C:\Users\Administrator\.mnemon\data\<id>\ store 目录。

## 本机落成结果（2026-11-06）
- 空间一：penta5 运行与口径，id=default，已激活。存 penta5-runner 各框架的 rule_version 与结算口径变更史、封存与命中率基线、审计结论、回滚验收判据、cron 与守护进程、面板重生成等运维知识。
- 空间二：本机环境坑与 DSH 配置，id=0dcb869d-6a01-4654-bf94-c25bd925dc8d，已激活。存 DSH profile 与插件安装、billion-context 与记忆配置、Windows 代理与 Store 修复、凭据与网络踩坑、跨窗口协同等。
- 已把 C:\Users\Administrator\.mnemon\runtime\archived\memories.jsonl（原文件保留未删）里的 5 条归档记忆导入空间一：cold 框架 pro 模型与 402 余额事故、2026-10-30 六个 AI 任务最终状态与 107-110 缺口、he3 合尾改 v4-pro 及两处修复、kill3-tail v3/v4 口径与冷码回测、面板品牌改名。
- 另在空间二写入 1 条环境坑：记忆视图按轮组装 + 新轮修法。
- 复验：healthy=true、version 0.2.13、memorySpaces total 2 / active 2 / healthy 2；aggregate totalInsights 6、oplogCount 6、edgeCount 42、dbSizeBytes 212992；注册表磁盘两条 active:true；两个 store 目录存在。

## PITFALLS
- 不要手写 .dsh-memory-bodies.json 造空间：DSH 进程内 registry 是内存态，外部写可能被后续 save() 覆盖；native 空间须先有对应 store（mnemon store create <id>）。
- 装完 CLI 后 commandFound 是 live getter 会变 true，但已组装的 View 不重算 → 必须新轮。
- embedding 未配置时写入返回 unembedded（无向量）：关键词/链接/时间召回可用，语义召回需在 Mnemon Native embedding 设置里填 endpoint 并点 Test 回填向量。
- 空间 id 即 Mnemon store 名（native provider 用 store:<id> 调 CLI），default 是合法 id。

（来源：DSH 桌面 profile 内 dsh-mnemon 与 dsh-mnemon-source-memory-spaces 源码取证 + 本机实跑复验。）
