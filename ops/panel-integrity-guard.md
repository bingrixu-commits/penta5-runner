# 面板完整性守卫（panel_guard）

2026-11-13 建立。

## 背景

2026-11-13 10:10:46，`/root/penta5-runner/panel.html` 被未知写入方截断成约 10KB 的碎片
（文件以 `0\n<!-- KILL_FIFTEEN_START -->` 开头，没有 `</body></html>`）。后果：

- `/f/<框架>` 抽取不到区块，多数框架页显示“该面板区块不存在”；
- `/root/run_stats.py` 每轮报 `ValueError: Panel body missing; daily statistics guard not installed`，
  统计无法重建；
- 面板同步进程连续失败，只能人工从 `/root/backups/config-<日期>-045001/panel.html` 还原。

根因尚未定位（面板写入方都走 `lib/panel_io.py` 的 `panel.html.lock` + 原子替换，
10:10 那次截断不在这条路径上）。本守卫先保证“坏页最多存在 2 分钟”。

## 行为

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

## 已知限制

- 只保证结构完整，不判断内容新旧；还原后各任务会在下一个周期重写自己的区块，统计由
  `penta5-panel-sync` 下一轮重建（最多约 5 分钟）。
- 若坏页写入方不持锁、且持续反复截断，日志会连续出现 `BROKEN` / `RESTORED`，需要按时间点
  继续追写入方（`data/panel_delivery_*.json`、`/root/.pm2/logs/*`）。
- 守卫只处理 `panel.html`，不处理 `stats.html` / `hitrate.html` / `accuracy.html`（这些是
  生成器整页原子替换，出现半页的概率低）。
