"""Prospective operation cutoff; immutable historical proof rules stay separate."""
from datetime import datetime, timedelta
import math

from ai_prediction_guard import PublicationRejected
from ai_request_deadline import REQUEST_BUDGET_SECONDS
from ai_verified_stats import next_period
from stats_history import beijing_now, parse_period, period_slot


PLATFORM_CLOSE_SECONDS = 70
PUBLICATION_RESERVE_SECONDS = 10
ERROR_CODES = frozenset({
    'operation-window-closed', 'operation-window-insufficient-budget',
    'operation-window-invalid-clock', 'operation-window-invalid-reference',
})


def close_at(reference):
    if (not isinstance(reference, str) or not parse_period(reference)
            or parse_period(reference)[0] != reference):
        raise PublicationRejected('operation-window-invalid-reference')
    slot = period_slot(next_period(reference))
    if slot is None:
        raise PublicationRejected('operation-window-invalid-reference')
    return slot - timedelta(seconds=PLATFORM_CLOSE_SECONDS)


def _seconds(value):
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise PublicationRejected('operation-window-invalid-clock')
    return value


class OperationWindow:
    def __init__(self, reference, clock=beijing_now):
        self.reference, self.clock = reference, clock
        self.close = close_at(reference)

    def _now(self):
        value = self.clock()
        if (not isinstance(value, datetime) or value.tzinfo is None
                or value.utcoffset() is None or value < period_slot(self.reference)):
            raise PublicationRejected('operation-window-invalid-clock')
        return value

    def require_open(self, reserve=0):
        if self._now() + timedelta(seconds=_seconds(reserve)) >= self.close:
            raise PublicationRejected('operation-window-closed')

    def request_budget(self, minimum=20, reserve=PUBLICATION_RESERVE_SECONDS):
        remaining = (self.close - self._now()).total_seconds() - _seconds(reserve)
        if remaining < _seconds(minimum) or remaining <= 0:
            raise PublicationRejected('operation-window-insufficient-budget')
        return min(REQUEST_BUDGET_SECONDS, remaining)

    @property
    def request_cutoff(self):
        return self.close - timedelta(seconds=PUBLICATION_RESERVE_SECONDS)
