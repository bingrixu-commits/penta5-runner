"""Server cron entry: freeze one reference, await its actual draw, run once."""
from datetime import datetime
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_ai_ready_job import (ReadyJobRejected, _source_state,
                                      expected_reference)
from stat50_compare import history_from_snapshot
from stats_history import TZ, beijing_now, load_snapshot
from ai_call_control import AICallsDisabled,require_ai_enabled


# Starting at the slot boundary instead of after sleep15 should preserve the
# previous latest readiness time: 0+70s equals the old 15+55s. Shared jobs keep
# their existing 55-second wait; only this selected-six entry uses 70 seconds.
SELECTED_WAIT_SECONDS = 70.0
_INCOMPLETE_HISTORY = frozenset(('history-must-have-exactly-50-draws',
                                'history-reference-mismatch', 'nonconsecutive-history'))


def _history_ready(snapshot, reference, now):
    """Wait for an incomplete local window; never wait through corrupt evidence."""
    try:
        history_from_snapshot(snapshot, reference, now)
    except ValueError as error:
        if (len(error.args) == 1 and isinstance(error.args[0], str)
                and error.args[0] in _INCOMPLETE_HISTORY):
            return False
        raise ReadyJobRejected('selected-history-invalid') from error
    except (TypeError, KeyError, OverflowError) as error:
        raise ReadyJobRejected('selected-history-invalid') from error
    return True


def _clock(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReadyJobRejected('invalid-selected-clock')
    return value.astimezone(TZ)


def _timer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReadyJobRejected('invalid-selected-timer')
    return float(value)


def wait_reference(*, loader=load_snapshot, clock=beijing_now,
                   monotonic=time.monotonic, sleep=time.sleep, log=print):
    from model_retirement import is_retired
    if is_retired('selected7'):raise ReadyJobRejected('selected-seven-retired')
    require_ai_enabled(root=ROOT,clock=clock)
    started_at = _clock(clock()); reference = expected_reference(started_at)
    started = previous = _timer(monotonic()); previous_at = started_at
    try:
        log('SELECTED_FETCH_START ref=%s at=%s' % (reference, started_at.isoformat()))
    except Exception:
        pass  # The frozen timer already includes any logger delay.
    while True:
        require_ai_enabled(root=ROOT,clock=clock)
        now, tick = _clock(clock()), _timer(monotonic())
        if now < previous_at or tick < previous:
            raise ReadyJobRejected('selected-clock-moved-backward')
        if expected_reference(now) != reference:
            raise ReadyJobRejected('selected-slot-advanced')
        if tick - started > SELECTED_WAIT_SECONDS:
            raise ReadyJobRejected('selected-source-timeout')
        snapshot = loader(persist=False)
        checked, elapsed = _clock(clock()), _timer(monotonic())
        if checked < now or elapsed < tick:
            raise ReadyJobRejected('selected-clock-moved-backward')
        if expected_reference(checked) != reference:
            raise ReadyJobRejected('selected-slot-advanced')
        if elapsed - started > SELECTED_WAIT_SECONDS:
            raise ReadyJobRejected('selected-source-timeout')
        ready = (_source_state(snapshot, reference, checked) == 'ready'
                 and _history_ready(snapshot, reference, checked))
        # Full-window validation may itself take time. Do not launch a paid
        # request if it crossed the frozen slot or consumed the shared wait.
        completed, completed_tick = _clock(clock()), _timer(monotonic())
        if completed < checked or completed_tick < elapsed:
            raise ReadyJobRejected('selected-clock-moved-backward')
        if expected_reference(completed) != reference:
            raise ReadyJobRejected('selected-slot-advanced')
        if completed_tick - started > SELECTED_WAIT_SECONDS:
            raise ReadyJobRejected('selected-source-timeout')
        previous, previous_at = completed_tick, completed
        if ready:
            require_ai_enabled(root=ROOT,clock=clock)
            try:
                log('SELECTED_READY ref=%s wait_seconds=%.3f' % (reference, completed_tick-started))
            except Exception:
                pass  # Logging must not change launch or create a repeated call.
            return reference
        remaining = SELECTED_WAIT_SECONDS - (completed_tick - started)
        if remaining <= 0:
            raise ReadyJobRejected('selected-source-timeout')
        sleep(min(1.0, remaining))


def main():
    from model_retirement import is_retired
    if is_retired('selected7'):return 0
    import fcntl
    from selected_framework_model import main as run_model
    with (ROOT / 'data/selected_framework_ready.lock').open('a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 0
        try:
            reference = wait_reference()
            return run_model(reference=reference)
        except AICallsDisabled:
            print('AI API manually stopped; no request launched.',flush=True)
            return 0
        except ReadyJobRejected as error:
            print('Selected source readiness rejected; code=' + str(error), flush=True)
            return 1
        except Exception as error:
            print('Selected source readiness failed; error_type=' + type(error).__name__, flush=True)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
