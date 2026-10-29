"""Strict provider output and bounded recovery without repeating local failures."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import random
import re

from ai_prediction_guard import PublicationRejected, publication_rejection

VERSION = 'provider-recovery-v1'
MINIMUM_ATTEMPT_SECONDS = 20.0


def strict_json_object(text):
    """Reject ambiguous duplicate keys and non-JSON numeric constants."""
    if not isinstance(text, str):
        raise ValueError('provider-content-must-be-text')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate-provider-json-key')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('nonfinite-provider-json-constant')
    def floating(token):
        number = float(token)
        if not math.isfinite(number):
            raise ValueError('nonfinite-provider-json-number')
        return number
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
    if not isinstance(value, dict):
        raise ValueError('provider-content-must-be-json-object')
    return value


def checked_source_time(reference, load_snapshot, clock):
    """Local revalidation failure is never a reason to repeat a paid request."""
    try:
        now = clock()
        rejection = publication_rejection(reference, load_snapshot(persist=False), now)
        if rejection:
            raise PublicationRejected(rejection)
        return now
    except PublicationRejected:
        raise
    except Exception:
        raise PublicationRejected('provider-source-validation-unavailable') from None


def _retry_after(response, now):
    try:
        headers = getattr(response, 'headers', None)
        value = headers.get('Retry-After') if headers is not None else None
        if not isinstance(value, str) or len(value) > 256:
            return None
        value = value.strip()
        if re.fullmatch(r'[0-9]+', value):
            # A hint longer than a day necessarily exceeds this draw's 250s
            # budget; saturating it still prevents an early repeated request.
            return float(min(int(value), 86400))
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            return None
        return max(0.0, (stamp - now).total_seconds())
    except Exception:
        # A malformed hint must not expose header values or break recovery.
        return None


def wait_before_retry(reference, attempt, deadline, *, clock, sleep, log=print,
                      response=None, status=None, validation_failure=False, jitter=None):
    """Keep retries inside the original deadline and honor provider wait hints."""
    if type(attempt) is not int or not 1 <= attempt <= 2:
        raise PublicationRejected('invalid-provider-retry-attempt')
    hinted = None
    if validation_failure:
        delay = 1.0
    else:
        try:
            now = clock()
        except Exception:
            raise PublicationRejected('provider-source-validation-unavailable') from None
        hinted = _retry_after(response, now)
        delay = max(float(2 ** attempt), hinted if hinted is not None else 0.0)
        try:
            extra = random.uniform(0.0, 0.5) if jitter is None else jitter()
            extra = float(extra) if not isinstance(extra, bool) else 0.0
            extra = max(0.0, min(0.5, extra)) if math.isfinite(extra) else 0.0
        except Exception:
            extra = 0.0
        delay += extra
    remaining = deadline.remaining()
    if remaining < delay + MINIMUM_ATTEMPT_SECONDS:
        raise PublicationRejected('provider-retry-budget-exhausted')
    record = {'ref': reference if isinstance(reference, str) and re.fullmatch(r'[0-9]{11}', reference) else None,
              'attempt': attempt, 'http_status': status if type(status) is int and 100 <= status <= 599 else None,
              'reason': 'response-validation' if validation_failure else 'transient-provider-or-network',
              'delay_seconds': round(delay, 3), 'remaining_seconds': round(remaining, 3),
              'server_retry_after_seconds': round(hinted, 3) if hinted is not None else None,
              'retry_policy_version': VERSION}
    try:
        log('AI_RETRY_META ' + json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    except Exception:
        pass
    sleep(delay)
    deadline.check()
    return delay
