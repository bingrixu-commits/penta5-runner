"""位置冷码报告常驻进程:每次开奖后重新计算一次。

开奖规律(实测):每 5 分钟一期,开奖时刻固定在每 5 分钟的 :12 秒。

触发判据:开奖数据的最新期号 + 记录条数。不用 mtime/size —— 实测开奖前后
该文件会被连续重写数次,用 mtime 会连算 4 遍;而"最新期号变了"才真正意味着
有新一期可以参与统计。

另设两条兜底:
  - 周期对齐:每跨过一个 5 分钟槽位且已过 :18 时再算一次(防落盘异常)。
  - 600 秒心跳。
单次计算约 0.35 秒。
"""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import position_cold_report as report  # noqa: E402
import kill_three_pick  # noqa: E402
import model_significance  # noqa: E402

WATCH = Path('/root/penta5-runner/data/stats_draw_history.json')
SLOT_SECONDS = 300
SLOT_DELAY = 18
POLL_SECONDS = 5
HEARTBEAT_SECONDS = 600


def signature():
    """返回 (最新期号, 记录数);读取失败返回 None。"""
    try:
        document = json.loads(WATCH.read_text(encoding='utf-8'))
        records = document.get('records') or []
        if not records:
            return None
        return (str(records[-1].get('period')), len(records))
    except (OSError, ValueError):
        return None


def main():
    from datetime import datetime
    last_signature = None
    last_slot = None
    last_run = 0.0
    print(f'position-cold daemon started (周期 {SLOT_SECONDS}s, 判据=最新期号+条数)',
          flush=True)
    while True:
        try:
            now = time.time()
            slot = int(now // SLOT_SECONDS)
            slot_due = (slot != last_slot) and (now >= slot * SLOT_SECONDS + SLOT_DELAY)
            current = signature()
            change_due = current is not None and current != last_signature
            beat_due = (time.monotonic() - last_run) >= HEARTBEAT_SECONDS

            if slot_due or change_due or beat_due:
                reason = []
                if change_due:
                    reason.append(f'new-period={current[0]}')
                if slot_due:
                    reason.append('slot')
                if beat_due:
                    reason.append('heartbeat')
                started = time.monotonic()
                code = report.main()
                kill_code = None
                try:
                    import contextlib
                    import io
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        kill_code = kill_three_pick.main()
                except Exception as error:
                    print(f'kill-three failed: {type(error).__name__}: {error}', flush=True)
                signi_code = None
                try:
                    import contextlib as _ctx
                    import io as _io
                    _buffer = _io.StringIO()
                    with _ctx.redirect_stdout(_buffer):
                        signi_code = model_significance.main()
                except Exception as error:
                    print(f'significance failed: {type(error).__name__}: {error}', flush=True)
                elapsed = time.monotonic() - started
                last_signature = signature()
                last_slot = slot
                last_run = time.monotonic()
                stamp = datetime.now().astimezone().strftime('%H:%M:%S')
                print(f'{stamp} regenerated rc={code} kill3={kill_code} signi={signi_code} elapsed={elapsed:.2f}s '
                      f'trigger={"+".join(reason)}', flush=True)
        except Exception as error:
            print(f'regenerate failed: {type(error).__name__}: {error}', flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
