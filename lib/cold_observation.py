"""Pure, validated daily cold-code observations; never generates predictions."""
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import product
import json

from stats_history import (TZ, beijing_now, business_start, parse_time, period_slot,
                           valid_draw_record, validate_conflicting_periods)

VERSION = 1
REVISION = 'cold-observation-v5-100-crossday'
WINDOW_SIZE = 100
POSITION_KEYS = ('q', 'b', 's', 'g')
TAIL_INDEXES = {
    'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
    'qsg': (0, 2, 3), 'bsg': (1, 2, 3),
}
DIGITS = tuple(str(value) for value in range(10))
SHAPE_VALUES = {
    'parity': tuple(''.join(value) for value in product(('单', '双'), repeat=4)),
    'size': tuple(''.join(value) for value in product(('大', '小'), repeat=4)),
}
ERROR_CODES = frozenset(('cold-invalid-snapshot', 'cold-invalid-time',
                         'cold-invalid-record', 'cold-invalid-conflicts',
                         'cold-conflicting-records', 'cold-history-gap'))


def _history_hash(records):
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return sha256(encoded).hexdigest()


def _base(now, reference=None, error=None):
    start = business_start(now)
    return {
        'version': VERSION, 'revision': REVISION,
        'stats_error': error is not None, 'error_code': error,
        'biz_date': start.strftime('%Y-%m-%d'),
        'business_start': start.isoformat(), 'updated_at': now.isoformat(),
        'reference_period': reference, 'first_period': None, 'last_period': None,
        'sample_count': 0, 'window_size': WINDOW_SIZE,
        'history_sha256': _history_hash([]),
        'positions': {}, 'tails': {}, 'shapes': {},
    }


def _group(values, samples, periods):
    """Omission is measured within this observation window, never extrapolated."""
    counts = dict.fromkeys(values, 0)
    last_indexes = {}
    for index, value in enumerate(samples):
        counts[value] += 1
        last_indexes[value] = index
    size = len(samples)
    items = []
    for value in values:
        index = last_indexes.get(value)
        items.append({
            'value': value, 'count': counts[value],
            'current_omission': size if index is None else size - index - 1,
            'omission_lower_bound': index is None,
            'last_seen_period': None if index is None else periods[index],
        })
    # Stable sort preserves declared value order when count and omission tie.
    ranked = sorted(items, key=lambda item: (item['count'], -item['current_omission']))
    return {
        'latest': samples[-1] if samples else None,
        'cold': [item['value'] for item in ranked[:1]] if size else [],
        'items': items,
    }


def build_observation(snapshot):
    """Observe the last at most 100 real draws across natural days.

    The window is a rolling 100-period span that does not reset at the 05:00
    business-day boundary, so the card stays fully populated right after
    midnight instead of only late in the day. Source rows are revalidated and
    normalized without modifying the snapshot. Conflicts and gaps in the
    selected window produce an explicit empty error result; older conflicts
    outside that window do not contaminate it. Identical first-four copies
    collapse; their earliest validated opening time is retained so input
    ordering cannot change the evidence hash.
    """
    if not isinstance(snapshot, dict):
        return _base(beijing_now(), error='cold-invalid-snapshot')
    now = snapshot.get('now')
    if now is None:
        now = beijing_now()
    elif not isinstance(now, datetime):
        return _base(beijing_now(), error='cold-invalid-time')
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    rows = snapshot.get('records')
    if not isinstance(rows, (list, tuple)):
        return _base(now, error='cold-invalid-snapshot')
    try:
        conflicts = validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    except ValueError:
        return _base(now, error='cold-invalid-conflicts')
    if any(period_slot(period) > now for period in conflicts):
        return _base(now, error='cold-invalid-conflicts')

    records = {}
    for row in rows:
        if not isinstance(row, dict):
            return _base(now, error='cold-invalid-record')
        record = valid_draw_record(row.get('period'), row.get('numbers'),
                                   row.get('opentime'), now)
        if record is None:
            return _base(now, error='cold-invalid-record')
        period = record['period']
        previous = records.get(period)
        if previous is not None:
            if previous['numbers'] != record['numbers']:
                conflicts.add(period)
            elif parse_time(record['opentime']) < parse_time(previous['opentime']):
                records[period] = record
        else:
            records[period] = record

    ordered = sorted(records.values(), key=lambda row: (row['opentime'], row['period']))
    unconflicted = [row for row in ordered if row['period'] not in conflicts]
    reference = unconflicted[-1]['period'] if unconflicted else None
    result = _base(now, reference)
    slots = {period_slot(row['period']): row['period'] for row in ordered}
    conflict_slots = {period_slot(period) for period in conflicts}
    last_slot = max([*slots, *conflict_slots], default=None)
    selected = []
    if last_slot is not None:
        step = timedelta(minutes=5)
        expected = [last_slot - step * offset for offset in range(WINDOW_SIZE)][::-1]
        # A rejected identity can be absent from snapshot.records. Keep its slot
        # in the candidate window, so removing a conflict cannot conceal a gap.
        if any(slot in conflict_slots for slot in expected):
            return _base(now, reference, 'cold-conflicting-records')
        if any(slot not in slots for slot in expected):
            return _base(now, reference, 'cold-history-gap')
        selected = [records[slots[slot]] for slot in expected]

    periods = [row['period'] for row in selected]
    numbers = [row['numbers'] for row in selected]
    result.update({
        'first_period': periods[0] if periods else None,
        'last_period': periods[-1] if periods else None,
        'sample_count': len(selected), 'history_sha256': _history_hash(selected),
        'positions': {
            key: _group(DIGITS, [str(digits[index]) for digits in numbers], periods)
            for index, key in enumerate(POSITION_KEYS)
        },
        'tails': {
            key: _group(DIGITS, [str(sum(digits[index] for index in indexes) % 10)
                                for digits in numbers], periods)
            for key, indexes in TAIL_INDEXES.items()
        },
        'shapes': {
            'parity': _group(SHAPE_VALUES['parity'],
                             [''.join('单' if digit % 2 else '双' for digit in digits)
                              for digits in numbers], periods),
            'size': _group(SHAPE_VALUES['size'],
                           [''.join('大' if digit >= 5 else '小' for digit in digits)
                            for digits in numbers], periods),
        },
    })
    return result
