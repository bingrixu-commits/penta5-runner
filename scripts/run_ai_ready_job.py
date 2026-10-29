"""Start a fixed AI job only after this cron slot's real draw is available.

The expected reference is frozen at entry. A delayed source may catch up, but
this launcher never switches to the next draw or creates a historical request.
"""
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_prediction_guard import publication_rejection
from ai_call_control import AICallsDisabled,require_ai_enabled
from stats_history import (TZ, beijing_now, load_snapshot, parse_period,
                           period_slot, valid_draw_record)

WAIT_SECONDS = 55.0
KINDS = ('framework', 'killer')


class ReadyJobRejected(ValueError):
    """A fixed, non-sensitive rejection reason; no source body is included."""


def _now(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReadyJobRejected('invalid-ready-job-clock')
    return value.astimezone(TZ)


def expected_reference(now):
    """Slot 001 starts 00:05; midnight belongs to the prior day's 288."""
    now = _now(now)
    slot = (now.hour * 60 + now.minute) // 5
    if slot == 0:
        return (now - timedelta(days=1)).strftime('%Y%m%d') + '288'
    return now.strftime('%Y%m%d') + f'{slot:03d}'


def _monotonic(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReadyJobRejected('invalid-ready-job-timer')
    return float(value)


def _source_state(snapshot, expected, now):
    """Return ready/wait; malformed, future, conflicting or advanced data fail."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('latest'), dict):
        raise ReadyJobRejected('missing-ready-job-source')
    latest = snapshot['latest']
    reference = latest.get('period')
    parsed = parse_period(reference)
    if not isinstance(reference, str) or not parsed or reference != parsed[0]:
        raise ReadyJobRejected('invalid-ready-job-reference')
    digits = latest.get('numbers')
    if (not isinstance(digits, (list, tuple)) or len(digits) != 4
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in digits)):
        raise ReadyJobRejected('invalid-ready-job-first-four')
    actual = valid_draw_record(reference, digits, latest.get('opentime'), now)
    if actual is None:
        raise ReadyJobRejected('invalid-ready-job-open-time')
    records = snapshot.get('records')
    if not isinstance(records, (list, tuple)):
        raise ReadyJobRejected('missing-ready-job-actual-history')
    matching = []
    for record in records:
        if not isinstance(record, dict) or record.get('period') != reference:
            continue
        checked = valid_draw_record(reference, record.get('numbers'), record.get('opentime'), now)
        if checked is None or checked != actual:
            raise ReadyJobRejected('conflicting-ready-job-actual-history')
        matching.append(checked)
    if not matching:
        raise ReadyJobRejected('missing-ready-job-actual-history')
    conflicts = snapshot.get('conflicting_periods', ())
    if not isinstance(conflicts, (list, tuple, set, frozenset)) or reference in conflicts or expected in conflicts:
        raise ReadyJobRejected('conflicting-ready-job-source')
    if period_slot(reference) > period_slot(expected):
        raise ReadyJobRejected('ready-job-reference-advanced')
    if reference != expected:
        return 'wait'
    rejection = publication_rejection(expected, snapshot, now)
    if rejection:
        raise ReadyJobRejected('ready-job-publication-rejected')
    return 'ready'


def run_ready_job(kind, *, root=ROOT, loader=load_snapshot, clock=beijing_now,
                  monotonic=time.monotonic, sleep=time.sleep, execv=os.execv):
    if kind not in KINDS:
        raise ReadyJobRejected('unknown-ready-job-kind')
    require_ai_enabled(root=root,clock=clock)
    launched_at = _now(clock())
    expected = expected_reference(launched_at)
    started = previous = _monotonic(monotonic())
    while True:
        require_ai_enabled(root=root,clock=clock)
        now = _now(clock())
        elapsed_clock = _monotonic(monotonic())
        if now < launched_at or elapsed_clock < previous:
            raise ReadyJobRejected('ready-job-clock-moved-backward')
        previous = elapsed_clock
        if expected_reference(now) != expected:
            raise ReadyJobRejected('ready-job-slot-advanced')
        if elapsed_clock - started > WAIT_SECONDS:
            raise ReadyJobRejected('ready-job-source-timeout')
        snapshot = loader(persist=False)
        # Loading may take time or move a source to the next reference. Recheck
        # both clocks after it returns, before examining or launching a job.
        checked_at = _now(clock())
        checked_timer = _monotonic(monotonic())
        if checked_at < now or checked_timer < previous:
            raise ReadyJobRejected('ready-job-clock-moved-backward')
        previous = checked_timer
        if expected_reference(checked_at) != expected:
            raise ReadyJobRejected('ready-job-slot-advanced')
        if checked_timer - started > WAIT_SECONDS:
            raise ReadyJobRejected('ready-job-source-timeout')
        state = _source_state(snapshot, expected, checked_at)
        if state == 'ready':
            require_ai_enabled(root=root,clock=clock)
            script = 'run_ai_job.py'
            argv = [sys.executable, '-u', str(Path(root) / 'scripts' / script)]
            argv.append(kind)
            execv(sys.executable, argv)
            return expected  # Injected test execs return; a real exec never does.
        remaining = WAIT_SECONDS - (checked_timer - started)
        if remaining <= 0:
            raise ReadyJobRejected('ready-job-source-timeout')
        sleep(min(1.0, remaining))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in KINDS:
        print('AI source readiness rejected; code=unknown-ready-job-kind')
        return 2
    try:
        run_ready_job(args[0])
    except AICallsDisabled:
        print('AI API manually stopped; no request launched.')
        return 0
    except ReadyJobRejected as error:
        print('AI source readiness rejected; code=' + str(error))
        return 1
    except Exception as error:
        print('AI source readiness failed; error_type=' + type(error).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
