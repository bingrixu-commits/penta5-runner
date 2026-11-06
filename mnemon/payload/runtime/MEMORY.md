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
DSH 技能配置最终状态（2026-10-31 04:3x）：注入模型时 description 只取 500 字符（dsh-tool-skill/lib/index.js:768、:1089），长描述技能的触发词要前置。用户级 25 个 ~/.dsh/skills 技能全部可模型调用、全带「当用户说…」中文触发词（4 个长描述触发句在开头）。Cursor/Codex 同步到共享目录 ~/.agents/skills 的 26 个技能（含 8 个仅 /命令 的 Codex 命令型，如 shell/goal/review/onboard）已整体移到 C:\Users\Administrator\.dsh\parked\agents-skills-20261031（manifest.json 可还原；Cursor 原副本 ~/.cursor/skills-cursor 未动），并设用户环境变量 DSH_AGENTS_HOME=C:\Users\Administrator\.dsh\agents-home，DSH 下次启动起不再读 ~/.agents/skills。不要恢复那 26 个或改回 DSH_AGENTS_HOME。
§
阿萨
§
w'q
§
a
§
penta5-runner 于 2026-11-07 00:40 上线 kill13_tail 框架（冷合尾 2 项，纯确定性不调 AI）：50 期冷榜从 5 种合尾组合×尾码 0-9=50 候选取最冷 2 个；沿用规则 cold-hold-top2（上期 2 项仍在本期冷榜前 2 就沿用，掉出即换，无连用上限）；RULE_VERSION=kill13-tail-v1-two-cold-hewei，精确基线 10^4×C(50,2)=1225 枚举 0.8082；数据 data/kill13_tail/，面板 section #kill-thirteen-ai（KILL_THIRTEEN 标记），cron */5 sleep 50，日志 data/kill13_tail_cron.log；无 freeze guard。
