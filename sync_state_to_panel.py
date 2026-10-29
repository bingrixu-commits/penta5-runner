"""Refresh on source changes, minute checks, and the 05:00 business boundary."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import subprocess
import time
import fcntl
import signal

# logrotate 每小时执行 pm2 reloadLogs,pm2 会对每个托管进程发 SIGUSR2。
# Python 默认动作是终止,会导致本进程被杀、正在跑的 run_stats.py 子进程
# 被连带 SIGKILL(历史上每小时一次,累计 3688 次重启)。此处忽略该信号。
for _signal_name in ('SIGUSR2',):
    _signal = getattr(signal, _signal_name, None)
    if _signal is not None:
        signal.signal(_signal, lambda *arguments: None)

STATE_PATH = Path('/root/penta5-runner/data/state.json')
# 2026-10-30: 完整刷新在 10-28 已达 148s,贴近旧上限 150s 后持续超时;
# 数据体量(seals 90MB / selected_six 279MB)只增不减,放宽到 600s。
REFRESH_TIMEOUT_SECONDS = 600


def refresh_log(status, started, *, returncode=None):
    """Emit fixed operational fields only; never print subprocess arguments."""
    checked_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    elapsed = max(0.0, time.monotonic() - started)
    message = (f'Statistics sync refresh {status}: checked_at={checked_at}; '
               f'elapsed_seconds={elapsed:.3f}')
    if returncode is not None:
        message += f'; returncode={returncode}'
    print(message, flush=True)


def refresh_key(now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    stat = STATE_PATH.stat()
    return stat.st_mtime_ns, stat.st_size, int(now.timestamp() // 60)


def try_refresh(previous):
    key = refresh_key()
    if key != previous:
        started = time.monotonic()
        try:
            result = subprocess.run(['python3', '/root/run_stats.py'],
                                    timeout=REFRESH_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            refresh_log('timeout', started)
            return previous
        if result.returncode == 0:
            refresh_log('success', started)
            return key
        refresh_log('failed', started, returncode=result.returncode)
    return previous


def main():
    with open('/root/penta5-runner/data/stats-sync.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another statistics synchronizer is already running.', flush=True)
            return
        previous = None
        print('Statistics sync started: source changes + minute checks; retry failed updates.', flush=True)
        while True:
            try:
                previous = try_refresh(previous)
            except Exception as error:
                checked_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
                print(f'Statistics sync error: checked_at={checked_at}; '
                      f'error_type={type(error).__name__}', flush=True)
            time.sleep(2)


if __name__ == '__main__':
    main()
