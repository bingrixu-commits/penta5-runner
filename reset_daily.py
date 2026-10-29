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
import subprocess
subprocess.run(['python3', '/root/run_stats.py'], check=True, timeout=45)
