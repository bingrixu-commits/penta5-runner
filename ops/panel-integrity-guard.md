# 面板完整性守卫（panel_guard）

2026-11-13 建立，同日补齐“写入侧”防护。

## 背景

2026-11-13 10:10:46，`/root/penta5-runner/panel.html` 被截断成约 2 字节的碎片
（内容 `0\n`，随后被各区块写入方追加成 10KB 残页，没有 `</body></html>`）。后果：

- `/f/<框架>` 抽取不到区块，多数框架页显示“该面板区块不存在”；
- `/root/run_stats.py` 每轮报 `ValueError: Panel body missing; daily statistics guard not installed`，
  统计无法重建；
- 面板同步进程连续失败，只能人工从 `/root/backups/config-<日期>-045001/panel.html` 还原。

## 溯源结论（2026-11-13 11:10）

- 10:10:46 只有一个来自 `153.0.169.163` 的 SSH 会话（布署机），sshd 默认不记录远程命令行；
  未安装 auditd，拿不到写入进程；
- 现有 cron/脚本里没有任何一处写 `0\n` 或 `> panel.html`；
- 同一天 10:51–10:59，仓库里出现了本会话之外的新文件（`scripts/model_stat_build.py`、
  `api/model_stat_ddl.sql`、`scripts/model_stat_refresh.sh`、`scripts/kill15_drift_guard.py`）
  和 2 条新 crontab（modelstat / kill15-drift），说明**另有并行操作源在同一台服务器上直接改文件**；
- 结论：10:10 的截断不是现有面板写入管线（`lib/panel_io.update_panel`）按常规逻辑产生的，
  更像是外部直接写入；管线本身的问题在于**它允许把残页/碎片继续提交**，放大了故障。

## 写入侧防护（2026-11-13 新增）

`lib/panel_io.update_panel` 在提交前增加结构校验（所有协作写入方共用）：

- 只允许提交“完整页”：必须含 `</body>` 且以 `</html>` 结尾；否则抛
  `ValueError: panel-fragment:<模块名>:<原因>`，**不写文件**，锁内原页保持不变；
- 拒绝时写审计 `data/panel_writes.log`：时间、`kind=REJECT`、模块名、前后长度、前 120 字符；
- 单次写入体积缩小超过 60KB 记 `kind=SHRINK`（只记录，不阻止）；
- 审计文件 1MB 轮转为 `.1`，日志失败不影响发布结果。

效果：任何走 `panel_io` 的写入方（run_stats / 各框架 view / 冷码观察 / 运行时观测…）
都不可能再把残页落盘；一旦有人返回碎片，日志里能直接看到是哪个模块。

长驻进程 `penta5-position-cold.service` 持有旧版 `panel_io`，已重启加载新逻辑。

## 读取侧守卫（cron）

`scripts/panel_guard.py` 由 cron 每 2 分钟执行：

1. 与面板写入方共用 `/root/penta5-runner/panel.html.lock`（flock，最多等 8 秒；抢不到就跳过本次）；
2. 结构校验（任一不满足即判坏页）：
   - 文件 ≥ 120KB；
   - 以 `</html>` 结尾；
   - 含 `</body>`；
   - 13 个框架标记（KILL_ONE…R1_FRAMEWORK）至少存在 10 个（容忍将来正常下线个别框架）；
   - 至少存在一个统计区块（`PENTA5_STATS_PANEL` 或 `COLD_OBSERVATION`）；
   - 文件里所有 `<!-- X_START -->` / `<!-- X_END -->` 成对；
3. 正常：把当前面板快照到 `data/panel_last_good.html`（内容变化才写，原子替换）；
4. 异常：
   - 坏页留档到 `data/panel_corrupt/panel-<时间戳>.html`（保留最近 5 份）；
   - 按“`data/panel_last_good.html` → 最近修改的有效备份（`/root/backups/config-*/panel.html`、
     `/root/backups/*/panel.html`、`panel.html.bak-*`）”顺序挑第一份通过校验的；
   - 原子替换 `panel.html`；
5. 日志：`data/panel_guard.log`（超过 256KB 轮转为 `.log.1`），cron 标准输出进
   `data/panel_guard_cron.log`（正常时为空）。

## 安装

```cron
*/2 * * * * cd /root/penta5-runner && flock -n /var/lock/penta5-panel-guard.lock timeout 60 python3 -u scripts/panel_guard.py >> data/panel_guard_cron.log 2>&1
```

首次安装前的 crontab 备份：`/root/backups/crontab-20261113-105718/crontab.txt`。

## 手工检查 / 演练

```bash
cd /root/penta5-runner
python3 scripts/panel_guard.py --check          # {"ok":true,...}；坏页退出码 2
python3 scripts/panel_guard.py --verbose        # 立即守卫一次
cat data/panel_guard.log                        # SNAPSHOT / BROKEN / RESTORED
tail data/panel_writes.log                      # REJECT / SHRINK（写入侧审计）
```

沙箱演练（不碰现网）：

```bash
rm -rf /tmp/gt && mkdir -p /tmp/gt/data /tmp/gt/backups/config-x
cp panel.html /tmp/gt/panel.html
cp panel.html /tmp/gt/backups/config-x/panel.html
python3 scripts/panel_guard.py --root /tmp/gt --backups-dir /tmp/gt/backups --verbose   # SNAPSHOT
printf '0\n' > /tmp/gt/panel.html
python3 scripts/panel_guard.py --root /tmp/gt --backups-dir /tmp/gt/backups --verbose   # RESTORED
```

写入侧演练（沙箱，验证碎片被拒绝）：

```bash
python3 - <<'PY'
import sys, pathlib
sys.path.insert(0, '/root/penta5-runner/lib')
from panel_io import update_panel
p = pathlib.Path('/tmp/gt/panel.html')
try:
    update_panel(lambda content: '0\n', path=p)
except ValueError as error:
    print('rejected:', error)     # panel-fragment:__main__:no-body
PY
```

## 已知限制

- 只保证结构完整，不判断内容新旧；还原后各任务会在下一个周期重写自己的区块，统计由
  `penta5-panel-sync` 下一轮重建（最多约 5 分钟）。
- 若坏页写入方绕过 `panel_io` 且持续反复截断，日志会连续出现 `BROKEN` / `RESTORED`；
  需要按时间点继续追该写入方（可考虑安装 auditd 或 fanotify 记录 PID）。
- 守卫只处理 `panel.html`，不处理 `stats.html` / `hitrate.html` / `accuracy.html`（这些是
  生成器整页原子替换，出现半页的概率低）。
