用户会同时开多个 DSH 窗口操作同一台服务器(root@8.210.5.221:/root/penta5-runner,共享同一个 git 工作区)。因此 git 历史中可能出现非本会话产生的、作者标识相同的提交;改动前应检查 git status/diff,避免 git add -A 把另一窗口的在途改动一并提交。
§
本机 PC-20260622JLQP（Win11 26H2）的一个环境坑：WinINET 代理绕过列表 ProxyOverride 里出现裸 IPv6 "::1" 会让 WinHttpOpen(WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY) 返回 0x80070057，导致 Microsoft Store / Windows Update / winget msstore 全部下载失败；删掉裸 ::1（保留 [::1]）即修复。RelyVPN 可能回写该列表，复现时运行 D:\文档\deepseek-harness\default-workspace\store-repair-backup\fix-store-autoproxy.ps1。2026-10-30 另已卸载伪装服务 Themes0qX 的 XDExtend/jwclient 推广残留（备份在同目录 adware-backup\）。
§
DSH 桌面版（profile desktop）安装「链接目录」本地 bundle 的怪癖：`plugin_manager install_bundle` 首次可能返回 `application: restart-required` / `restartReason: package-moved`，但下一次 Plugin Manager 重组（例如对同一 bundle 做一次 set_bundle 关闭再开启）后它会真正生效，无需重启；判断是否生效要重新 `cordis_inspect_query`（Host Config 里出现 `include:<rowId>` 行、Client Slots 里出现新 occupant），不要只信安装返回。本地插件放 `D:\文档\deepseek-harness\default-workspace\dsh-local-plugins\<name>`，包名用 `@dsh-local/<name>`；左侧栏页面 = 同时注册 `main`（key=id）与 `sidebar.panellist`（id、order、label）。
§
penta5-runner 的面板 panel.html 是运行时动态重生成的：多个视图模块（lib/panel_live_refresh.py、selected_single_view.py、selected_framework_view.py、selected_six_view.py、stat50_position_view.py）各自带一份 `var names=[...]` JS 模板和各自的区块 START/END 标记，sync_state_to_panel.py 每轮调用 /root/run_stats.py 重写面板。因此只改 panel.html 无效，必须同时改这些模板；删除功能时还要注意删除前已在跑的 run_stats.py 进程会用旧代码把区块再写回去，需等其结束后再清理一次。
§
penta5-runner 面板统计刷新（2026-10-30 起）：sync_state_to_panel.py 改为「每期开奖结算后一次」——主触发=data/state.json 的 latestRecord.period 变化、开奖后 180s 结算点执行 /root/run_stats.py；10min 心跳 / 05:00 业务日 / 失败 120s 重试兜底；实测单轮 91s、一期只刷一次（旧实现按分钟 key 触发，等于连续满载 80-258s/轮）。预测卡片由各任务自己写 panel，不依赖该进程。
§
penta5-runner 的 operation window 硬约束：lib/ai_operation_window.py 里 PLATFORM_CLOSE_SECONDS=70，即某期预测必须在开奖后 230s 内发布，否则报 operation-window-insufficient-budget / operation-window-closed。因此 he3/cold_fixed 等任务不能错峰到「开奖+3min」之后（2026-10-30 07:08 实测 he3 因错峰到 +3min 被拒）；只有 stat50/selected_six 可以移到 +1~+2min。
§
penta5-runner he3 统计缓存：lib/he3_refine_stats.py 2026-10-30 加入 data/he3_refresh.stamp.json 输入指纹缓存，he3_extra 阶段 53s→3.2s（输出逐字节一致）；PENTA5_HE3_STATS_CACHE=0 禁用、删 stamp 强制冷算；新开奖会改变 tail 记录指纹从而必 miss。同一日期还新增 scripts/ops_health_check.py（19 项体检，每 10min，data/ops_health.json + data/ops_alerts.log，仅状态跃迁告警）、scripts/refresh_source_manifest.py（--if-drift，04:55 重建证据备份 baseline）、scripts/crontab_tool.sh + ops/crontab.txt（crontab 版本化与 drift 告警）。
§
penta5-runner 面板 2026-10-31 新增「刮刮乐·单双大小」静态区块（commit 464c85e 只含 scripts/panel_scratch_card.py 幂等补丁脚本）：panel.html 里 stat-grid 之后插入 #scratch-card；样式 id=penta5-scratch-style、脚本 id=penta5-scratch-card（都无 START/END 标记，panel-sync 只按标记改区块、不会覆盖，已连续两轮重建验证保留）。卡片读 #disp_period/#disp_numbers/#disp_hewei/#disp_time，用 MutationObserver + penta5:panel-updated 事件在每期头部数字变化时自动换卡（重铺 canvas 涂层）；canvas 刮开>50% 自动揭开，60s 未刮自动揭开，右上角有「全部刮开」。panel.html 是 skip-worktree，改动仅在磁盘，备份 /root/ops/scratch-card-20261031/。注意：面板页自带 live-refresh 会校验脚本哈希，改 panel.html 后旧页面会提示手动刷新一次。
§
penta5-runner 2026-10-31 上线三项（commits 7200fcc/efedb85/9d37acf）：① scripts/ops_health_check.py 新增 deepseek_balance（ok>=8/warn>=3/fail<3 CNY，脚本自己解析 .env 的 PENTA5_FRAMEWORK_API_KEY）与 provider_402（近30分钟日志有 402→warn）；② 面板新增「整组命中率 vs 随机期望」区块：lib/hitrate_baseline.py 生成 data/hitrate_baseline.json（精确枚举基线 killer 41.62/framework 55.59/selected_six 51.60/冷码 54.27/固定 54.36/he3 93.75/kill3 61.91%），lib/hitrate_baseline_view.py 渲染，/root/run_stats.py 每轮先重算再在 update_panel 最外层注入（run_stats.py 不在 git，备份 /root/ops/remediation-20261031/）；③ six「来源核验暂不可用」修复：scripts/build_six_seal_index.py 对 350MB selected_six_seals.json 做字节级 prediction_seals 提取生成 data/selected_six_seal_index.json（54MB/1059 seals，字段全保留、校验不降级），lib/strategy_comparison.py 在源超 128MB 时读索引、源 size/mtime 变更自动重建、失败回落 source_error；面板 six 现显示整组 47.06%/定位 90.52%。
§
penta5-runner 2026-10-31 上线「DeepSeek 余额守护」：scripts/ai_balance_guard.py + crontab 每分钟（flock /var/lock/penta5-balance-guard.lock → data/ai_balance_guard.log，状态 data/ai_balance_guard.json）。逻辑：余额接口 is_available=false 连续 2 次且 AI 开关开启→用 ai_call_control.set_enabled 自动停（保留 CAS generation/审计链）；只自动恢复自己停的那一代（generation 匹配才 resume，期间手动改过就清标记尊重手动）；恢复后不补旧期，既有 */5 AI cron 下一轮自动读最近 50 期最新数据继续发布。正常轮次静默，状态文件是心跳。commits 4dcd135/7a2a359；crontab 已用 scripts/crontab_tool.sh 版本化安装（ops/crontab.txt），备份 /tmp/crontab.backup.20261031_040208 与 /root/ops/balance-guard-20261031/。AI 开关仍是 manual 模式，只有余额不可用时才会被自动改写。
§
DSH 技能配置最终状态（2026-10-31 04:3x）：注入模型时 description 只取 500 字符（dsh-tool-skill/lib/index.js:768、:1089），长描述技能的触发词要前置。用户级 25 个 ~/.dsh/skills 技能全部可模型调用、全带「当用户说…」中文触发词（4 个长描述触发句在开头）。Cursor/Codex 同步到共享目录 ~/.agents/skills 的 26 个技能（含 8 个仅 /命令 的 Codex 命令型，如 shell/goal/review/onboard）已整体移到 C:\Users\Administrator\.dsh\parked\agents-skills-20261031（manifest.json 可还原；Cursor 原副本 ~/.cursor/skills-cursor 未动），并设用户环境变量 DSH_AGENTS_HOME=C:\Users\Administrator\.dsh\agents-home，DSH 下次启动起不再读 ~/.agents/skills。不要恢复那 26 个或改回 DSH_AGENTS_HOME。
§
penta5-runner 2026-10-31 提示词部署：selected_six 首答硬性要求已上线 9939126（transport v2 兼容 v1 历史，ref 051-055 attempt=1）；framework/killer 自检 94c5000 破坏 stat50_archive 的历史 prompt 复渲染校验，已 revert d94dc1b，看板恢复；pro 切档后看板 _metadata 只认 flash 族会拒新封存。
§
penta5-runner 2026-10-31 06:51 状态：服务器全自动、本机关机不影响。A0：影子任务 68bb193、门禁 070c48a、自动解除/转正 135f17a（门禁 ready 自动写 data/joint_form_auto_promote.json enabled；framework/killer 下一期起仅把两个排除形态换成 10^4 精确联合最优，AI 杀码/合尾/保留集不变；旧封存保留；回滚=该文件 manual_disable=true）。历史 2100 期 framework 55.98→58.55(+2.57pp)、killer 39.97→42.78(+2.80pp)；优化响应通过发布校验；门禁 ready 临时根测试通过。当前 burn-in 2/72（面板底 #joint-form-shadow 实时显示）。余额 10.6 CNY 仅约1h，跑满72期需充值约40-50 CNY，否则 AI 停摆无法自动解除门禁。ops_health ok。
§
penta5-runner 2026-10-31 上线「模型运行观测层」：lib/model_usage.py + lib/model_usage_view.py + scripts/run_model_usage.py（每分钟 cron，flock penta5-model-usage，日志 data/model_usage_cron.log 已入 logrotate）；只读 7 个 AI 任务日志的 AI_* 观测行 → data/model_usage/ledger-YYYYMM.jsonl + summary.json；面板 #model-usage 区块（MODEL_USAGE_START/END，在 RUNTIME_OBSERVABILITY_END 后，run_stats 重生成不覆盖）；ops_health 新增 model_usage 项。首轮回填 8715 事件，402=619/429=7 与原始日志 grep 一致。commits 1bb95cf/270817a；备份 /root/ops/model-usage-20261031/。
§
阿萨
§
penta5-runner：面板「整组命中率 vs 随机期望（累计·版本锁定）」大表已于 2026-11-01 移到独立页 /hitrate（cron 每15min 重写 hitrate.html，面板只留 hitrate-base-entry 入口），不要再加回主面板。提交 598000f/b3bede4，备份 /root/ops/hitrate-page-20261101-044355/。
§
penta5-runner .env 的 PENTA5_MODEL_* 行由 scripts/model_tier_guard.py（cron 每10分钟，data/model_tier_guard.log）按余额自动全量切 flash/pro，且会增删个别键（如 KILL1/KILL2P 行可能消失）；各 task 脚本的真实模型解析有回退链（kill1: KILL1→KILLER→DEEPSEEK_MODEL；kill2p: KILL2P→KILL3→KILLER→DEEPSEEK_MODEL）。读「某任务当前用什么模型」必须按回退链解析 .env，不能只看单键。
§
w'q
