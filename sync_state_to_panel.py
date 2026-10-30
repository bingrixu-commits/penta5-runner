"""Refresh once per new draw period (plus heartbeat / business-day boundary).

2026-10-30: the old trigger was (state.json mtime, size, current minute); the
minute component changed every poll, so with a full refresh now taking 80-250s
the process was effectively always running (72 refreshes in the 3.7h before
07:00). The primary trigger is now "latestRecord.period changed": the backend
runner updates data/state.json within ~1 minute of every draw, and the refresh
starts only after the period has settled (SETTLE_SECONDS after the draw) so the
AI jobs' publications are already on disk. A 10-minute heartbeat and the 05:00
business-day switch are the fallbacks. Prediction cards are written to
panel.html by each job itself, so they do not depend on this refresher.
"""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
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

ROOT = Path('/root/penta5-runner')
STATE_PATH = ROOT / 'data' / 'state.json'
DRAW_PATH = ROOT / 'data' / 'stats_draw_history.json'
PANEL_PATH = ROOT / 'panel.html'
# 2026-10-30: 完整刷新在 10-28 已达 148s,贴近旧上限 150s 后持续超时;
# 数据体量(seals 90MB / selected_six 279MB)只增不减,放宽到 600s。
REFRESH_TIMEOUT_SECONDS = 600
# 实测开奖节拍: 每 300s 一期; 每期开奖后 180s 再刷新, 此时各 AI 任务/stat50
# 的封存都已落盘, 一轮刷新即可覆盖整期。预测卡片由各任务自己写面板, 不等这里。
PERIOD_SECONDS = 300
# 2026-10-30: 180→75s。开奖数据在 draw+~3s 已进 state.json，180s 结算点导致
# "/root/run_stats.py" 在 draw+180s 才启动、draw+285~309s 才写完面板（关盘=draw+230s），
# 统计/建议类区块因此总在关盘后才更新。75s 起跑后整轮约在 draw+170~200s 完成，
# 赶在关盘前；冲突/重试逻辑不变。
SETTLE_SECONDS = 45
HEARTBEAT_SECONDS = 600
RETRY_SECONDS = 120
POLL_SECONDS = 5
TZ = ZoneInfo('Asia/Shanghai')

_state_cache = {'key': None, 'period': None}


def refresh_log(status, started, *, trigger, returncode=None):
    """Emit fixed operational fields only; never print subprocess arguments."""
    checked_at = datetime.now(TZ).isoformat()
    elapsed = max(0.0, time.monotonic() - started)
    message = (f'Statistics sync refresh {status}: checked_at={checked_at}; '
               f'elapsed_seconds={elapsed:.3f}; trigger={trigger}')
    if returncode is not None:
        message += f'; returncode={returncode}'
    print(message, flush=True)


def _parse_period(value):
    if not isinstance(value, str) or len(value) != 11 or not value.isdigit():
        return None
    try:
        day = datetime.strptime(value[:8], '%Y%m%d').replace(tzinfo=TZ)
        index = int(value[-3:])
    except (ValueError, OverflowError):
        return None
    # Observed draw phase: 每期开奖时刻 = 当日 00:00 + index*300s + 12s.
    return day + timedelta(minutes=5 * index, seconds=12)


def latest_period():
    """Latest draw period from state.json (backend updates it within ~1 min).

    The 13MB state file is parsed only when its mtime/size changes; every 5s
    poll therefore costs one stat() while still catching every new draw.
    """
    try:
        stat = STATE_PATH.stat()
    except OSError:
        return _state_cache['period']
    key = (stat.st_mtime_ns, stat.st_size)
    if key == _state_cache['key']:
        return _state_cache['period']
    period = None
    try:
        document = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        raw = (document.get('latestRecord') or {}).get('period')
        period = str(raw) if raw else None
    except (OSError, ValueError, TypeError, KeyError):
        period = None
    if period is None:
        # Fallback: the small draw-history cache (may lag by one refresh).
        try:
            records = json.loads(DRAW_PATH.read_text(encoding='utf-8')).get('records') or []
            raw = records[-1].get('period') if isinstance(records[-1], dict) else None
            period = str(raw) if raw else None
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            period = None
    _state_cache['key'] = key
    _state_cache['period'] = period
    return period


def settled(period, now):
    slot = _parse_period(period)
    return slot is not None and now >= slot + timedelta(seconds=SETTLE_SECONDS)


def business_day(now):
    return (now - timedelta(hours=5)).strftime('%Y-%m-%d')


def startup_needed():
    """A restarted process refreshes immediately only when the panel is stale."""
    try:
        return time.time() - PANEL_PATH.stat().st_mtime > 300
    except OSError:
        return True


def run_refresh(trigger):
    started = time.monotonic()
    try:
        result = subprocess.run(['python3', '/root/run_stats.py'],
                                timeout=REFRESH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        refresh_log('timeout', started, trigger=trigger)
        return False
    if result.returncode == 0:
        refresh_log('success', started, trigger=trigger)
        return True
    refresh_log('failed', started, trigger=trigger, returncode=result.returncode)
    return False


def main():
    with open(ROOT / 'data' / 'stats-sync.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another statistics synchronizer is already running.', flush=True)
            return
        now = datetime.now(TZ)
        last_period = latest_period()
        last_day = business_day(now)
        last_run = time.monotonic()
        next_retry_at = 0.0
        print('Statistics sync started: one refresh per settled draw + 10min heartbeat.', flush=True)
        if startup_needed():
            ok = run_refresh('startup')
            last_period = latest_period() or last_period
            last_day = business_day(datetime.now(TZ))
            last_run = time.monotonic()
            if not ok:
                next_retry_at = last_run + RETRY_SECONDS
        while True:
            try:
                now = datetime.now(TZ)
                period = latest_period()
                trigger = None
                if next_retry_at and time.monotonic() >= next_retry_at:
                    trigger = 'retry'
                    next_retry_at = 0.0
                elif period and period != last_period and settled(period, now):
                    trigger = 'new-draw=' + period
                elif business_day(now) != last_day:
                    trigger = 'business-day=' + business_day(now)
                elif time.monotonic() - last_run >= HEARTBEAT_SECONDS:
                    trigger = 'heartbeat'
                if trigger:
                    ok = run_refresh(trigger)
                    last_period = latest_period() or last_period
                    last_day = business_day(datetime.now(TZ))
                    last_run = time.monotonic()
                    if not ok:
                        next_retry_at = last_run + RETRY_SECONDS
            except Exception as error:
                checked_at = datetime.now(TZ).isoformat()
                print(f'Statistics sync error: checked_at={checked_at}; '
                      f'error_type={type(error).__name__}', flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
