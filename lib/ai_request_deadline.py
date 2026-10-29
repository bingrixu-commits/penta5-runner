"""Bound thinking-mode requests by elapsed time and the real publication cutoff.

The Linux interval timer covers the complete response read/JSON decode, including
responses kept alive by whitespace. It is deliberately unavailable in worker
threads or when another component owns the process timer: a socket inactivity
timeout alone cannot enforce this contract.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import math
import signal
import threading

from ai_prediction_guard import PublicationRejected
from ai_verified_stats import next_period
from stats_history import period_slot


REQUEST_BUDGET_SECONDS = 250.0
MAX_REQUEST_BUDGET_SECONDS = 270.0
MAX_TOKENS = 32768
REQUEST_POLICY_VERSION = 'high-32k-deadline-v1'
EXTENDED_REQUEST_POLICY_VERSION = 'high-32k-slot-budget-v2'
PUBLICATION_RESERVE_SECONDS = 10.0
_EXHAUSTED = 'provider-wall-clock-deadline-exhausted'


def _finite_seconds(value):
    if isinstance(value, bool):
        raise PublicationRejected('invalid-provider-wall-clock-timeout')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise PublicationRejected('invalid-provider-wall-clock-timeout') from None
    if not math.isfinite(result):
        raise PublicationRejected('invalid-provider-wall-clock-timeout')
    return result


class RequestDeadline:
    """One deadline shared by all attempts, leaving time for durable publication.

    The default budget remains 250 seconds. An explicit caller budget may use
    up to 270 seconds, always clamped to target-slot minus ten seconds.
    Elapsed monotonic time limits it even if the wall clock moves back;
    the current wall clock can only shorten it. Previously observed remaining
    time is also retained, so a later backwards wall step cannot restore time.
    """

    def __init__(self, reference, clock, monotonic, *, budget_seconds=REQUEST_BUDGET_SECONDS,
                 cutoff_at=None):
        if isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float)):
            raise PublicationRejected('invalid-provider-request-budget')
        try:
            requested = float(budget_seconds)
        except (OverflowError, ValueError, TypeError):
            raise PublicationRejected('invalid-provider-request-budget') from None
        if not math.isfinite(requested) or not 0 < requested <= MAX_REQUEST_BUDGET_SECONDS:
            raise PublicationRejected('invalid-provider-request-budget')
        self.requested_budget_seconds = requested
        self.policy_version = (REQUEST_POLICY_VERSION if self.requested_budget_seconds == REQUEST_BUDGET_SECONDS
                               else EXTENDED_REQUEST_POLICY_VERSION)
        self._clock = clock
        self._monotonic = monotonic
        target = next_period(reference)
        slot = period_slot(target)
        if slot is None:
            raise PublicationRejected('invalid-reference-period')
        self.cutoff = slot - timedelta(seconds=PUBLICATION_RESERVE_SECONDS)
        if cutoff_at is not None:
            if (not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None
                    or cutoff_at.utcoffset() is None
                    or not period_slot(reference) <= cutoff_at <= self.cutoff):
                raise PublicationRejected('invalid-provider-request-cutoff')
            self.cutoff = cutoff_at
        self._started = _finite_seconds(monotonic())
        self._last_monotonic = self._started
        initial_wall_remaining = self._wall_remaining()
        self.budget_seconds = max(0.0, min(self.requested_budget_seconds,
                                           initial_wall_remaining))
        self._monotonic_cutoff = self._started + self.budget_seconds

    def _wall_remaining(self):
        try:
            remaining = (self.cutoff - self._clock()).total_seconds()
        except (TypeError, ValueError, OverflowError, AttributeError):
            raise PublicationRejected('invalid-request-wall-clock') from None
        if not math.isfinite(remaining):
            raise PublicationRejected('invalid-request-wall-clock')
        return remaining

    def remaining(self):
        current = _finite_seconds(self._monotonic())
        if current < self._last_monotonic:
            raise PublicationRejected('invalid-request-monotonic-clock')
        self._last_monotonic = current
        # Convert each wall observation to a monotonic cutoff. Retaining only
        # its remaining duration would incorrectly stop that shortened budget
        # from elapsing after a subsequent backwards wall step.
        self._monotonic_cutoff = min(
            self._monotonic_cutoff,
            current + self._wall_remaining(),
        )
        return max(0.0, self._monotonic_cutoff - current)

    def check(self):
        if self.remaining() <= 0.0:
            raise PublicationRejected(_EXHAUSTED)


@contextmanager
def request_timeout(seconds):
    """Interrupt a synchronous Linux request without replacing another timer.

    Enter before the paid call; include response.json() and response.close() in
    the body. The previous handler is restored on normal return, exceptions,
    and expiry. Existing armed timers are never touched. A final deadline check
    remains necessary because Python delivers signals at interpreter boundaries.
    """
    duration = _finite_seconds(seconds)
    if duration <= 0.0:
        raise PublicationRejected(_EXHAUSTED)
    required = ('SIGALRM', 'ITIMER_REAL', 'getitimer', 'setitimer',
                'getsignal', 'signal')
    if (threading.current_thread() is not threading.main_thread()
            or any(not hasattr(signal, name) for name in required)):
        raise PublicationRejected('provider-wall-clock-timer-unavailable')
    try:
        old_timer = signal.getitimer(signal.ITIMER_REAL)
        old_handler = signal.getsignal(signal.SIGALRM)
    except (OSError, ValueError, RuntimeError):
        raise PublicationRejected('provider-wall-clock-timer-unavailable') from None
    if old_timer[0] != 0.0 or old_timer[1] != 0.0:
        raise PublicationRejected('provider-wall-clock-timer-already-armed')

    def expired(_signum, _frame):
        raise PublicationRejected(_EXHAUSTED)

    handler_attempted = False
    active_error = None
    try:
        # Treat setup errors as nonretryable infrastructure failures, including
        # failures reported after the underlying operation had a side effect.
        handler_attempted = True
        try:
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, duration, 0.0)
        except PublicationRejected:
            raise
        except (OSError, ValueError, RuntimeError, TypeError, OverflowError):
            raise PublicationRejected('provider-wall-clock-timer-unavailable') from None
        yield
    except BaseException as error:
        active_error = error
        raise
    finally:
        if handler_attempted:
            # Disable our timer before reinstalling the prior handler. The
            # previous timer was verified unarmed, so this restores its state.
            # Always attempt both restorations, and preserve an already active
            # deadline/body exception if restoring the timer itself also fails.
            cleanup_errors = []
            try:
                signal.setitimer(signal.ITIMER_REAL, 0.0, 0.0)
            except BaseException as error:
                cleanup_errors.append(error)
            try:
                signal.signal(signal.SIGALRM, old_handler)
            except BaseException as error:
                cleanup_errors.append(error)
            if active_error is None and cleanup_errors:
                error = cleanup_errors[0]
                if isinstance(error, (PublicationRejected, KeyboardInterrupt, SystemExit)):
                    raise error
                # A valid response must not trigger another paid call solely
                # because timer cleanup reported a failure after disabling it.
                raise PublicationRejected('provider-wall-clock-timer-unavailable') from None
