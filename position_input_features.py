"""Pure, descriptive positional features for an exact 50-draw input window.

Counts and omissions describe observed draws only. An unseen digit has an
omission of 50 with a censored flag, meaning *at least* 50 draws, not an exact
omission or a probability estimate. Callers establish draw-source provenance;
this module checks the sealed first-four values, identity, order, and times.
"""

from datetime import datetime, timedelta

from stats_history import (TZ, beijing_now, parse_period, valid_draw_record,
                           validate_conflicting_periods)


POSITION_KEYS = ('q', 'b', 's', 'g')
HISTORY_SIZE = 50


def _canonical_period(value):
    parsed = parse_period(value)
    if not isinstance(value, str) or parsed is None or value != parsed[0]:
        raise ValueError('invalid-position-feature-period')
    return parsed


def _next_period(parsed):
    text, day, index = parsed
    if index < 288:
        return text[:8] + f'{index + 1:03}'
    return (day + timedelta(days=1)).strftime('%Y%m%d') + '001'


def _current_time(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('position-feature-now-must-be-timezone-aware')
    return now.astimezone(TZ)


def _real_record(raw, current):
    if not isinstance(raw, dict):
        raise ValueError('invalid-position-feature-history-record')
    period, numbers, opened = raw.get('period'), raw.get('numbers'), raw.get('opentime')
    _canonical_period(period)
    if (not isinstance(numbers, list) or len(numbers) != 4
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in numbers)):
        raise ValueError('invalid-position-feature-first-four')
    # Sealed records use explicit timezone-aware ISO times. Do not infer a
    # timezone for an ambiguous local time or accept bool/epoch coercions.
    try:
        opened_time = datetime.fromisoformat(opened) if isinstance(opened, str) else None
    except ValueError:
        opened_time = None
    record = valid_draw_record(period, numbers, opened, current) if opened_time else None
    if (opened_time is None or opened_time.tzinfo is None
            or opened_time.utcoffset() is None or record is None):
        raise ValueError('invalid-position-feature-real-opening-time')
    return record


def _validate_history(history, reference, now):
    _canonical_period(reference)
    current = _current_time(now)
    if not isinstance(history, list) or len(history) != HISTORY_SIZE:
        raise ValueError('position-feature-history-must-have-exactly-50-draws')

    result, previous = [], None
    for raw in history:
        record = _real_record(raw, current)
        period, numbers = record['period'], record['numbers']
        parsed = _canonical_period(period)
        if previous is not None and _next_period(previous) != period:
            raise ValueError('nonconsecutive-position-feature-history')
        result.append(numbers)
        previous = parsed
    if history[-1]['period'] != reference:
        raise ValueError('position-feature-reference-mismatch')
    return result


def history_from_snapshot(snapshot, *, now=None):
    """Extract one unambiguous exact 50-draw window from a validated snapshot.

    ``recent`` must identify 50 unique canonical first-four draws, all matching
    real ``records``. Conflicting records (including different opening times),
    explicit conflict flags, gaps, and a differing ``latest`` fail closed.
    Returned records are fresh canonical dictionaries, never input aliases.
    """
    if not isinstance(snapshot, dict):
        raise ValueError('invalid-position-feature-snapshot')
    if now is None:
        now = snapshot.get('now')
        if now is None:
            now = beijing_now()
    current = _current_time(now)
    latest = _real_record(snapshot.get('latest'), current)
    reference = latest['period']
    recent = snapshot.get('recent')
    if not isinstance(recent, (list, tuple)) or len(recent) != HISTORY_SIZE:
        raise ValueError('position-feature-history-must-have-exactly-50-draws')
    requested, seen = [], set()
    for item in recent:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('invalid-position-feature-recent-history')
        period, digits = item
        _canonical_period(period)
        if (not isinstance(digits, (list, tuple)) or len(digits) != 4
                or any(type(digit) is not int or not 0 <= digit <= 9 for digit in digits)
                or period in seen):
            raise ValueError('invalid-position-feature-recent-first-four')
        requested.append((period, list(digits)))
        seen.add(period)
    records = snapshot.get('records')
    if not isinstance(records, (list, tuple)):
        raise ValueError('invalid-position-feature-snapshot-records')
    conflicts = validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    available = {}
    for raw in records:
        if (not isinstance(raw, dict) or not isinstance(raw.get('period'), str)
                or raw['period'] not in seen):
            continue
        record = _real_record(raw, current)
        period = record['period']
        if period in available and available[period] != record:
            conflicts.add(period)
        available[period] = record
    chosen = []
    for period, digits in requested:
        record = available.get(period)
        if record is None or record['numbers'] != digits or period in conflicts:
            raise ValueError('position-feature-recent-history-not-real-record')
        chosen.append(record)
    _validate_history(chosen, reference, current)
    if latest != chosen[-1]:
        raise ValueError('position-feature-latest-history-mismatch')
    return chosen


def build_position_features(history, reference, now):
    """Return JSON-safe counts and current omissions from 50 canonical draws.

    ``history`` is oldest first, ends at ``reference``, and contains exactly
    four integer digits and a real opening timestamp per draw. ``now`` must be
    timezone aware. Invalid, incomplete, future, or unordered input raises
    ``ValueError``. This function performs no I/O and never modifies input.
    """
    draws = _validate_history(history, reference, now)
    positions = {}
    for index, key in enumerate(POSITION_KEYS):
        counts = [0] * 10
        omissions = [HISTORY_SIZE] * 10
        censored = [True] * 10
        for numbers in draws:
            counts[numbers[index]] += 1
        for offset, numbers in enumerate(reversed(draws)):
            digit = numbers[index]
            if censored[digit]:
                omissions[digit] = offset
                censored[digit] = False
        positions[key] = {
            'position_1based': index + 1,
            'sample_size': HISTORY_SIZE,
            'counts': counts,
            'omissions': omissions,
            'omission_censored': censored,
        }
    return {
        'version': 1,
        'reference_period': reference,
        'first_period': history[0]['period'],
        'last_period': history[-1]['period'],
        'history_size': HISTORY_SIZE,
        'positions': positions,
    }
