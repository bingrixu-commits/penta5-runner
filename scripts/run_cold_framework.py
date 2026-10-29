"""Start at the slot boundary and wait at most 70 seconds for real cold data."""
from datetime import datetime
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_call_control import AICallsDisabled, require_ai_enabled
from ai_prediction_guard import PublicationRejected
import cold_framework as core
from stats_history import TZ, beijing_now, load_snapshot, period_slot, valid_draw_record

WAIT_SECONDS = 70.0


class ReadyRejected(ValueError):
    pass


def _clock(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReadyRejected('cold-ready-invalid-clock')
    return value.astimezone(TZ)


def _timer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReadyRejected('cold-ready-invalid-timer')
    return float(value)


def wait_reference(*, root=ROOT, loader=None, clock=beijing_now,
                   monotonic=time.monotonic, sleep=time.sleep, log=print):
    root = Path(root)
    require_ai_enabled(root=root, clock=clock)
    if loader is None:
        loader = lambda **kwargs: load_snapshot(state_path=root / 'data/state.json',
                                               cache_path=root / 'data/stats_draw_history.json', **kwargs)
    started_at = previous_at = _clock(clock())
    reference = core.expected_reference(started_at)
    started = previous = _timer(monotonic())
    while True:
        require_ai_enabled(root=root, clock=clock)
        now, tick = _clock(clock()), _timer(monotonic())
        if now < previous_at or tick < previous:
            raise ReadyRejected('cold-ready-clock-moved-backward')
        if core.expected_reference(now) != reference:
            raise ReadyRejected('cold-ready-reference-advanced')
        if tick - started > WAIT_SECONDS:
            raise ReadyRejected('cold-ready-source-timeout')
        source = loader(persist=False)
        checked, elapsed = _clock(clock()), _timer(monotonic())
        if checked < now or elapsed < tick:
            raise ReadyRejected('cold-ready-clock-moved-backward')
        if core.expected_reference(checked) != reference:
            raise ReadyRejected('cold-ready-reference-advanced')
        if elapsed - started > WAIT_SECONDS:
            raise ReadyRejected('cold-ready-source-timeout')
        if not isinstance(source, dict) or not isinstance(source.get('records'), (list, tuple)):
            raise ReadyRejected('cold-ready-invalid-source')
        latest = source.get('latest')
        ready = False
        if latest is not None:
            if not isinstance(latest, dict):
                raise ReadyRejected('cold-ready-invalid-source')
            actual = valid_draw_record(latest.get('period'), latest.get('numbers'), latest.get('opentime'), checked)
            if actual is None:
                raise ReadyRejected('cold-ready-invalid-latest')
            if period_slot(actual['period']) > period_slot(reference):
                raise ReadyRejected('cold-ready-source-advanced')
            if actual['period'] == reference:
                try:
                    context = core.candidate_context(source, checked)
                except (ValueError, TypeError, KeyError) as error:
                    raise ReadyRejected('cold-ready-invalid-candidates') from error
                ready = context['reference_period'] == reference
        completed, completed_tick = _clock(clock()), _timer(monotonic())
        if completed < checked or completed_tick < elapsed:
            raise ReadyRejected('cold-ready-clock-moved-backward')
        if core.expected_reference(completed) != reference:
            raise ReadyRejected('cold-ready-reference-advanced')
        if completed_tick - started > WAIT_SECONDS:
            raise ReadyRejected('cold-ready-source-timeout')
        previous_at, previous = completed, completed_tick
        if ready:
            require_ai_enabled(root=root, clock=clock)
            try:
                log('COLD_FRAMEWORK_READY ref=%s wait_seconds=%.3f' % (reference, completed_tick - started))
            except Exception:
                pass
            return reference
        remaining = WAIT_SECONDS - (completed_tick - started)
        if remaining <= 0:
            raise ReadyRejected('cold-ready-source-timeout')
        sleep(min(1.0, remaining))


def main():
    try:
        require_ai_enabled(root=ROOT, clock=beijing_now)
        import fcntl
        from cold_framework_model import main as run_model
        with (ROOT / 'data/cold_framework_ready.lock').open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            reference = wait_reference()
            return run_model(reference=reference)
    except AICallsDisabled:
        print('AI API manually stopped; cold framework request not launched.', flush=True)
        return 0
    except (ReadyRejected, PublicationRejected):
        print('Cold framework source readiness rejected.', flush=True)
        return 1
    except Exception as error:
        print('Cold framework source readiness failed; error_type=' + type(error).__name__, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
