用户会同时开多个 DSH 窗口操作同一台服务器(root@8.210.5.221:/root/penta5-runner,共享同一个 git 工作区)。因此 git 历史中可能出现非本会话产生的、作者标识相同的提交;改动前应检查 git status/diff,避免 git add -A 把另一窗口的在途改动一并提交。
§
本机 PC-20260622JLQP（Win11 26H2）的一个环境坑：WinINET 代理绕过列表 ProxyOverride 里出现裸 IPv6 "::1" 会让 WinHttpOpen(WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY) 返回 0x80070057，导致 Microsoft Store / Windows Update / winget msstore 全部下载失败；删掉裸 ::1（保留 [::1]）即修复。RelyVPN 可能回写该列表，复现时运行 D:\文档\deepseek-harness\default-workspace\store-repair-backup\fix-store-autoproxy.ps1。2026-10-30 另已卸载伪装服务 Themes0qX 的 XDExtend/jwclient 推广残留（备份在同目录 adware-backup\）。
§
DSH 桌面版（profile desktop）安装「链接目录」本地 bundle 的怪癖：`plugin_manager install_bundle` 首次可能返回 `application: restart-required` / `restartReason: package-moved`，但下一次 Plugin Manager 重组（例如对同一 bundle 做一次 set_bundle 关闭再开启）后它会真正生效，无需重启；判断是否生效要重新 `cordis_inspect_query`（Host Config 里出现 `include:<rowId>` 行、Client Slots 里出现新 occupant），不要只信安装返回。本地插件放 `D:\文档\deepseek-harness\default-workspace\dsh-local-plugins\<name>`，包名用 `@dsh-local/<name>`；左侧栏页面 = 同时注册 `main`（key=id）与 `sidebar.panellist`（id、order、label）。
§
penta5-runner 的面板 panel.html 是运行时动态重生成的：多个视图模块（lib/panel_live_refresh.py、selected_single_view.py、selected_framework_view.py、selected_six_view.py、stat50_position_view.py）各自带一份 `var names=[...]` JS 模板和各自的区块 START/END 标记，sync_state_to_panel.py 每轮调用 /root/run_stats.py 重写面板。因此只改 panel.html 无效，必须同时改这些模板；删除功能时还要注意删除前已在跑的 run_stats.py 进程会用旧代码把区块再写回去，需等其结束后再清理一次。
§
penta5-runner 2026-10-30 02:57 的「108 个模块迁入 lib/」重构把若干 lib 模块写成 ROOT=Path(__file__).resolve().parent（实际变成 lib/），导致 AI 任务读 lib/data 下不存在的 ai_api_control.json 而静默 paused：杀码框架/智能排除自 02:51 起停更，冷码框架/固定框架停在 ref 20261030034。修复=ROOT 改 parents[1]，并把 lib/ai_job_status.py 的 JOBS 脚本路径改为 lib/auto_r1_killer.py / lib/auto_r1_framework.py（根目录同名入口已被移走）。
§
penta5-runner 的 .env PENTA5_DEEPSEEK_MODEL 之前是死配置：cron 不加载 .env，代码用 os.getenv 拿不到，杀码/框架实际一直走默认 deepseek-flash；2026-10-30 已改为 get_secret 读 .env（commit 56ada48），面板卡片文案改为跟随实际模型。Pro 试跑一期（ref 20261030075，thinking high）：framework 38.5s/5217 reasoning tokens、killer 74.4s/9396 tokens 均发布成功；但 killer 接近代码里 80s 单请求读超时上限，重试会顶穿 186s 期窗口，故默认仍用 Flash。
§
penta5-runner 面板统计刷新（2026-10-30 起）：sync_state_to_panel.py 改为「每期开奖结算后一次」——主触发=data/state.json 的 latestRecord.period 变化、开奖后 180s 结算点执行 /root/run_stats.py；10min 心跳 / 05:00 业务日 / 失败 120s 重试兜底；实测单轮 91s、一期只刷一次（旧实现按分钟 key 触发，等于连续满载 80-258s/轮）。预测卡片由各任务自己写 panel，不依赖该进程。
§
penta5-runner 的 operation window 硬约束：lib/ai_operation_window.py 里 PLATFORM_CLOSE_SECONDS=70，即某期预测必须在开奖后 230s 内发布，否则报 operation-window-insufficient-budget / operation-window-closed。因此 he3/cold_fixed 等任务不能错峰到「开奖+3min」之后（2026-10-30 07:08 实测 he3 因错峰到 +3min 被拒）；只有 stat50/selected_six 可以移到 +1~+2min。
§
penta5-runner he3 统计缓存：lib/he3_refine_stats.py 2026-10-30 加入 data/he3_refresh.stamp.json 输入指纹缓存，he3_extra 阶段 53s→3.2s（输出逐字节一致）；PENTA5_HE3_STATS_CACHE=0 禁用、删 stamp 强制冷算；新开奖会改变 tail 记录指纹从而必 miss。同一日期还新增 scripts/ops_health_check.py（19 项体检，每 10min，data/ops_health.json + data/ops_alerts.log，仅状态跃迁告警）、scripts/refresh_source_manifest.py（--if-drift，04:55 重建证据备份 baseline）、scripts/crontab_tool.sh + ops/crontab.txt（crontab 版本化与 drift 告警）。
§
penta5-runner 2026-10-30 模型决策：framework 已回退 deepseek-flash（.env PENTA5_MODEL_FRAMEWORK=deepseek-flash，ref 093 起 AI_RESPONSE_META 已确认 flash）；v4-pro 试运行样本仅 30 次=93.3%（flash 全史 3080=90.29%，差异不显著）且 completion tokens 约 2 倍、耗时 32-39s。scripts/model_daily_report.py 新增 framework_by_model 字段（从 r1_framework_seals 的 model_metadata.requested_model 拆分统计）。
§
penta5-runner：退役数据 selected_framework_* 与 pro_compare_* 已于 2026-10-30 归档到 /root/archive/retired-20261030/（gzip 共 12MB，原始 93MB）；evidence_backup 的 REQUIRED_DATA 已移除两项、strategy_comparison 不再读取 legacy 'selected' 账本。找回方法=gunzip 回 data/ 并回退 commit 0c86888。cuopt：用户确认没有 8.138.155.17（阿里云广州）这台服务器、也决定不再买/租服务器，已把 pm2+ecosystem 的 PENTA5_CUOPT_URL 置空（commit 8fb0c23），auto-top6 直接 JS 穷举回退，ops_health 把 cuopt-url-unset 判为「已停用 OK」；不要建议重新部署 GPU。
§
a'a'z
§
Z'x
§
penta5-runner 面板 2026-10-31 新增「刮刮乐·单双大小」静态区块（commit 464c85e 只含 scripts/panel_scratch_card.py 幂等补丁脚本）：panel.html 里 stat-grid 之后插入 #scratch-card；样式 id=penta5-scratch-style、脚本 id=penta5-scratch-card（都无 START/END 标记，panel-sync 只按标记改区块、不会覆盖，已连续两轮重建验证保留）。卡片读 #disp_period/#disp_numbers/#disp_hewei/#disp_time，用 MutationObserver + penta5:panel-updated 事件在每期头部数字变化时自动换卡（重铺 canvas 涂层）；canvas 刮开>50% 自动揭开，60s 未刮自动揭开，右上角有「全部刮开」。panel.html 是 skip-worktree，改动仅在磁盘，备份 /root/ops/scratch-card-20261031/。注意：面板页自带 live-refresh 会校验脚本哈希，改 panel.html 后旧页面会提示手动刷新一次。
§
penta5-runner 2026-10-31 上线三项（commits 7200fcc/efedb85/9d37acf）：① scripts/ops_health_check.py 新增 deepseek_balance（ok>=8/warn>=3/fail<3 CNY，脚本自己解析 .env 的 PENTA5_FRAMEWORK_API_KEY）与 provider_402（近30分钟日志有 402→warn）；② 面板新增「整组命中率 vs 随机期望」区块：lib/hitrate_baseline.py 生成 data/hitrate_baseline.json（精确枚举基线 killer 41.62/framework 55.59/selected_six 51.60/冷码 54.27/固定 54.36/he3 93.75/kill3 61.91%），lib/hitrate_baseline_view.py 渲染，/root/run_stats.py 每轮先重算再在 update_panel 最外层注入（run_stats.py 不在 git，备份 /root/ops/remediation-20261031/）；③ six「来源核验暂不可用」修复：scripts/build_six_seal_index.py 对 350MB selected_six_seals.json 做字节级 prediction_seals 提取生成 data/selected_six_seal_index.json（54MB/1059 seals，字段全保留、校验不降级），lib/strategy_comparison.py 在源超 128MB 时读索引、源 size/mtime 变更自动重建、失败回落 source_error；面板 six 现显示整组 47.06%/定位 90.52%。
§
penta5-runner 2026-10-31 上线「DeepSeek 余额守护」：scripts/ai_balance_guard.py + crontab 每分钟（flock /var/lock/penta5-balance-guard.lock → data/ai_balance_guard.log，状态 data/ai_balance_guard.json）。逻辑：余额接口 is_available=false 连续 2 次且 AI 开关开启→用 ai_call_control.set_enabled 自动停（保留 CAS generation/审计链）；只自动恢复自己停的那一代（generation 匹配才 resume，期间手动改过就清标记尊重手动）；恢复后不补旧期，既有 */5 AI cron 下一轮自动读最近 50 期最新数据继续发布。正常轮次静默，状态文件是心跳。commits 4dcd135/7a2a359；crontab 已用 scripts/crontab_tool.sh 版本化安装（ops/crontab.txt），备份 /tmp/crontab.backup.20261031_040208 与 /root/ops/balance-guard-20261031/。AI 开关仍是 manual 模式，只有余额不可用时才会被自动改写。
