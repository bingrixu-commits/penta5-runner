"""位置冷码报告常驻进程:数据变化时重算,平时几乎不耗资源。

以 stats_draw_history.json 的 (mtime_ns, size) 作为变化信号。

为什么不用 state.json:冷码只依赖开奖记录,而 state.json 会被预测/计数器
频繁改写(出现过每 10 秒变一次),拿它当信号会导致大量无意义重算。
stats_draw_history.json 只在开奖记录变化时更新,是语义上正确的触发源。

单次生成约 0.4 秒,平时仅每 10 秒 stat 一次;另有 300 秒心跳兜底。
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import position_cold_report as report  # noqa: E402

WATCH = Path('/root/penta5-runner/data/stats_draw_history.json')
POLL_SECONDS = 10
HEARTBEAT_SECONDS = 300


def signature():
    try:
        info = WATCH.stat()
        return (info.st_mtime_ns, info.st_size)
    except OSError:
        return None


def main():
    from datetime import datetime
    last_signature = None
    last_run = 0.0
    print('position-cold daemon started', flush=True)
    while True:
        try:
            current = signature()
            now = time.monotonic()
            if current is None:
                time.sleep(POLL_SECONDS)
                continue
            if current != last_signature or (now - last_run) >= HEARTBEAT_SECONDS:
                started = time.monotonic()
                code = report.main()
                elapsed = time.monotonic() - started
                last_signature = current
                last_run = time.monotonic()
                stamp = datetime.now().astimezone().strftime('%H:%M:%S')
                print(f'{stamp} regenerated rc={code} elapsed={elapsed:.2f}s '
                      f'sig={current[0]}/{current[1]}', flush=True)
        except Exception as error:  # 单次失败不能终止常驻进程
            print(f'regenerate failed: {type(error).__name__}: {error}', flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
