# -*- coding: utf-8 -*-
import os, re, time

PANEL_PATH = "/root/penta5-runner/panel.html"
RESULT_PATH = "/root/penta5-runner/filtered_results_4d.txt"

# 清空结果文本
if os.path.exists(RESULT_PATH):
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        f.write("")

# 重置网页展示为清零等待状态
# Removed retired opening-day card; keep daily result reset.

print("✅ [北京时间 05:00] 每日清零重置执行完毕！")

# Refresh empty/new business-day statistics immediately without reviving retired cards.
# 2026-10-30: a full refresh now takes 85-250s (selected_six/he3 read 300MB+
# ledgers), so the old 45s bound raised TimeoutExpired every day. Also skip when
# another refresh already holds the shared lock: panel-sync refreshes every
# minute anyway, and 05:00 has reset/guard/panel-sync racing for the same lock.
import fcntl
import subprocess

STATS_LOCK = '/root/penta5-runner/data/stats-refresh.lock'


def stats_refresh_free():
    try:
        with open(STATS_LOCK, 'a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(handle, fcntl.LOCK_UN)
        return True
    except OSError:
        return True


if stats_refresh_free():
    try:
        subprocess.run(['python3', '/root/run_stats.py'], check=True, timeout=300)
    except subprocess.TimeoutExpired:
        print('⚠️ 统计刷新超过 300s，交由 panel-sync 下一轮继续')
else:
    print('统计刷新正在进行，跳过 05:00 立即刷新（panel-sync 下一分钟会完成）')
