"""Pure prospective publication coverage, separate from prediction hit rates.

One durable request tombstone is one task. A task is successful only when its
original seal validates as published before the target opens. Open, nonterminal
tasks are excluded from the completed-task publication percentage. Historical
failure causes are never guessed from gaps or from a later status record.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json

from ai_verified_stats import next_period
from selected_framework_contract import (REVISION, LEDGER_REVISION, SUPPORTED_REVISIONS,
                                         MODEL, build_input_doc)
from stats_history import TZ, business_start, parse_period, period_slot

VERSION = 'selected-six-coverage-v1'
_STATES = frozenset(('waiting', 'requesting', 'published', 'preserved', 'failed', 'rejected'))
_SEAL_STATES = frozenset(('prepared', 'published', 'expired-unpublished', 'late-unverified'))
_TIMEOUT_CODES = frozenset(('provider-wall-clock-deadline-exhausted', 'publication-deadline-passed',
    'selected-seven-platform-close-passed', 'selected-seven-insufficient-close-budget',
    'selected-six-reference-advanced', 'reference-period-advanced', 'target-already-opened',
    'insufficient-time-for-killer-request', 'killer-retry-budget-exhausted'))
_UNKNOWN_CODES = frozenset(('publication-rejected', 'inference-or-evidence-error'))
_OPERATION_WINDOW_CODES = frozenset(('selected-seven-operation-window-passed',
                                    'selected-seven-insufficient-operation-budget'))


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _reference(value):
    parsed = parse_period(value)
    if not isinstance(value, str) or not parsed or value != parsed[0]:
        raise ValueError('invalid-coverage-reference')
    return value


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('invalid-coverage-time')
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, OverflowError):
        raise ValueError('invalid-coverage-time') from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('invalid-coverage-time')
    return moment.astimezone(TZ)


def _error_category(code, cause=None):
    # Import only when needed: the model owns the application-code whitelist.
    from selected_framework_model import _DIAGNOSTIC_CODES
    safe = _DIAGNOSTIC_CODES | _UNKNOWN_CODES | {'provider-insufficient-balance'}
    if code is not None and (not isinstance(code, str) or code not in safe):
        raise ValueError('invalid-coverage-failure-code')
    if cause is not None and (not isinstance(cause, str) or cause not in safe):
        raise ValueError('invalid-coverage-cause-code')
    if code == 'provider-insufficient-balance' or cause == 'provider-insufficient-balance':
        return 'balance'
    if code in _OPERATION_WINDOW_CODES or cause in _OPERATION_WINDOW_CODES:
        return 'operation-window'
    if code in _TIMEOUT_CODES or cause in _TIMEOUT_CODES:
        return 'timeout'
    return 'unknown' if code is None or code in _UNKNOWN_CODES else 'other'


def _failure(raw, started, now):
    if not isinstance(raw, dict) or set(raw) != {'code', 'error_type', 'cause_code', 'failed_at'}:
        raise ValueError('invalid-coverage-failure')
    failed = _time(raw['failed_at'])
    if not started <= failed <= now:
        raise ValueError('invalid-coverage-failure-time')
    from selected_framework_model import _DIAGNOSTIC_TYPES
    if raw['error_type'] not in _DIAGNOSTIC_TYPES | {'Exception'}:
        raise ValueError('invalid-coverage-failure-type')
    return _error_category(raw['code'], raw['cause_code'])


def _status(raw, now):
    if raw is None:
        return None
    if (not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1
            or raw.get('revision') not in SUPPORTED_REVISIONS or raw.get('requested_model') != MODEL
            or raw.get('status') not in _STATES):
        raise ValueError('invalid-coverage-status')
    reference = _reference(raw.get('reference_period'))
    updated = _time(raw.get('updated_at'))
    if raw.get('target_period') != next_period(reference) or not period_slot(reference) <= updated <= now:
        raise ValueError('invalid-coverage-status-time-or-target')
    category = _error_category(raw.get('error_code'), raw.get('cause_code'))
    return {'reference': reference, 'revision': raw['revision'],
            'status': raw['status'], 'updated': updated, 'category': category}


def _empty(now, *, error):
    day = business_start(now).strftime('%Y-%m-%d')
    names = ('attempted_count', 'published_count', 'in_progress_count', 'requesting_count',
             'unpublished_count', 'closed_unpublished_count', 'failed_before_deadline_count',
             'completed_count', 'failed_count')
    value = None if error else 0
    return {'version': VERSION, 'biz_date': day, 'rule_revision': REVISION, 'coverage_error': error,
            **{key: value for key in names}, 'publication_percent': None,
            'all_attempt_publication_percent': None, 'operation_window_count': value,
            'failure_counts': {key: value for key in ('timeout', 'balance', 'other', 'unknown')}}


def make_coverage(ledger, now, *, status=None):
    """Return an evidence-checked 05:00 coverage summary without file writes.

    Corrupt/missing input yields unavailable counts (None), never a synthetic
    zero-sample or 100% result. Genuine versioned empty ledgers yield real zeros.
    Published seals without a matching durable attempt are an evidence error.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('coverage-clock-must-be-aware')
    now = now.astimezone(TZ)
    result = _empty(now, error=False)
    try:
        if (not isinstance(ledger, dict) or type(ledger.get('version')) is not int
                or ledger['version'] != 1 or ledger.get('revision') != LEDGER_REVISION
                or not isinstance(ledger.get('request_attempts'), dict)
                or not isinstance(ledger.get('prediction_seals'), dict)):
            raise ValueError('invalid-coverage-ledger')
        attempts, seals = ledger['request_attempts'], ledger['prediction_seals']
        current_status = _status(status, now)
        checked = {}
        for reference, attempt in attempts.items():
            reference = _reference(reference)
            if not isinstance(attempt, dict) or attempt.get('revision') not in SUPPORTED_REVISIONS:
                raise ValueError('invalid-coverage-attempt')
            started = _time(attempt.get('started_at'))
            target = next_period(reference)
            cutoff = period_slot(target)
            if not period_slot(reference) <= started < cutoff or started > now:
                raise ValueError('invalid-coverage-attempt-time')
            document = attempt.get('input_doc')
            if not isinstance(document, dict) or document.get('reference_period') != reference:
                raise ValueError('invalid-coverage-attempt-input')
            reconstructed = build_input_doc(reference, document.get('history'), revision=attempt['revision'])
            if document != reconstructed or attempt.get('input_sha256') != _digest(document):
                raise ValueError('invalid-coverage-attempt-input-hash')
            failure = _failure(attempt['failure'], started, now) if 'failure' in attempt else None
            checked[reference] = {'started': started, 'target': target, 'cutoff': cutoff,
                                  'failure': failure, 'input_sha256': attempt['input_sha256'],
                                  'revision': attempt['revision']}
        published = set()
        from selected_framework_model import validate_publication
        from publication_validation_cache import validate_publication as validate_cached_publication
        for reference, seal in seals.items():
            reference = _reference(reference)
            if (reference not in checked or not isinstance(seal, dict)
                    or seal.get('publication_state') not in _SEAL_STATES
                    or seal.get('revision') != checked[reference]['revision']
                    or seal.get('input_sha256') != checked[reference]['input_sha256']):
                raise ValueError('invalid-coverage-seal-attempt-link')
            if seal['publication_state'] == 'published':
                validate_cached_publication('selected7', seal, reference, now, published=True)
                published.add(reference)
                if checked[reference]['failure'] is not None:
                    raise ValueError('conflicting-coverage-published-failure')
            else:
                # Verify original immutable body while retaining its nonpublished
                # state in the caller. An expired/prepared card is never success.
                prepared = deepcopy(seal);prepared['publication_state'] = 'prepared'
                validate_publication(prepared, reference, now, published=False)
        for reference, evidence in checked.items():
            # A changed keep-set size is a distinct experiment. Retain and
            # validate all old evidence, but never mix it into current coverage.
            if evidence['revision'] != REVISION:
                continue
            if business_start(evidence['cutoff']).strftime('%Y-%m-%d') != result['biz_date']:
                continue
            result['attempted_count'] += 1
            if reference in published:
                result['published_count'] += 1
                continue
            category = evidence['failure']
            active_status = (current_status if current_status and current_status['reference'] == reference
                             and current_status['revision'] == evidence['revision']
                             and current_status['updated'] >= evidence['started'] else None)
            if category is None and active_status and active_status['status'] in ('failed', 'rejected'):
                category = active_status['category']
            closed = evidence['cutoff'] <= now
            if category is not None or closed:
                result['unpublished_count'] += 1
                if category is not None:
                    result['failed_count'] += 1
                result['closed_unpublished_count' if closed else 'failed_before_deadline_count'] += 1
                # Keep the original four mutually exclusive buckets compatible.
                # This optional diagnostic is a subset of "other", never an API
                # timeout and never an additional failure in the denominator.
                if category == 'operation-window':
                    result['operation_window_count'] += 1
                result['failure_counts']['other' if category == 'operation-window'
                                         else category or 'unknown'] += 1
            else:
                result['in_progress_count'] += 1
                if active_status and active_status['status'] == 'requesting':
                    result['requesting_count'] += 1
        result['completed_count'] = result['published_count'] + result['unpublished_count']
        if result['completed_count']:
            result['publication_percent'] = round(100 * result['published_count'] / result['completed_count'], 4)
        if result['attempted_count']:
            result['all_attempt_publication_percent'] = round(100 * result['published_count'] / result['attempted_count'], 4)
        assert result['attempted_count'] == result['completed_count'] + result['in_progress_count']
        assert result['unpublished_count'] == sum(result['failure_counts'].values())
        return result
    except (ValueError, TypeError, KeyError, OSError, OverflowError, AssertionError):
        return _empty(now, error=True)
