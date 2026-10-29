import time
import subprocess
import os

print("🚀 DeepSeek-Flash 自动推导守护进程已启动...")
last_period = None

while True:
    try:
        # 执行推导脚本
        result = subprocess.run(
            ["python3", "/root/penta5-runner/auto_r1_framework.py"],
            capture_output=True,
            text=True
        )
        if "成功同步" in result.stdout or "响应成功" in result.stdout:
            print(f"[{time.strftime('%H:%M:%S')}] ✅ 自动推导并同步面板成功！")
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] 异常: {e}")
    
    # 每 30 秒检查一次开奖与更新
    time.sleep(30)
