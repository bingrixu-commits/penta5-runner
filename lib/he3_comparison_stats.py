"""Derived, paired six/five accounting over already validated publications.

This module never creates predictions, changes a ledger, or calls a model.
The caller must first restore the original ledger with its full proof checks.
"""
from datetime import timedelta
from itertools import combinations

from stats_history import (business_start, parse_period, parse_time, period_slot,
                           valid_draw_record, validate_conflicting_periods)

REVISION = 'he3-paired-v1'
VERSIONS = ('he3-refine-v1', 'he3-refine-v2-balanced', 'he3-refine-v3-panel-context')


def previous_period(period):
    parsed = parse_period(period)
    if not isinstance(period, str) or not parsed or parsed[0] != period:
        raise ValueError('invalid-comparison-period')
    text, day, index = parsed
    return (text[:8] + f'{index - 1:03}' if index > 1 else
            (day - timedelta(days=1)).strftime('%Y%m%d') + '288')


def _periods(anchor, count):
    periods = []
    for _ in range(count):
        periods.append(anchor)
        anchor = previous_period(anchor)
    return list(reversed(periods))


def _outcome(entry):
    proof, actual = entry['publication'], entry['actual']
    six, five = proof['original_he3_keep'], proof['response']['he3_keep5']
    if (not isinstance(six, str) or len(six) != 6 or len(set(six)) != 6
            or not isinstance(five, str) or len(five) != 5 or len(set(five)) != 5
            or any(d not in '0123456789' for d in six + five) or not set(five) < set(six)):
        raise ValueError('invalid-paired-subset')
    digits = actual['numbers']
    if (not isinstance(digits, list) or len(digits) != 4
            or any(type(d) is not int or not 0 <= d <= 9 for d in digits)):
        raise ValueError('invalid-paired-actual')
    tails = {sum(digits[i] for i in indexes) % 10 for indexes in combinations(range(4), 3)}
    six_hit, five_hit = bool(tails & set(map(int, six))), bool(tails & set(map(int, five)))
    if type(entry.get('hit')) is not bool or entry['hit'] != five_hit or five_hit and not six_hit:
        raise ValueError('paired-outcome-disagrees')
    if proof['revision'] not in VERSIONS:
        raise ValueError('unknown-paired-revision')
    return six_hit, five_hit, proof['revision']


def _window(periods, ledger, conflicts, observed):
    verified, pending = ledger['verified'], ledger['pending']
    hits6 = hits5 = total = lost = published = conflict_count = pending_count = 0
    maximum_win = maximum_loss = win = loss = 0
    versions = dict.fromkeys(VERSIONS, 0)
    for target in periods:
        if target in conflicts:
            conflict_count += 1
            win = loss = 0
            continue
        if target in verified:
            six_hit, five_hit, revision = _outcome(verified[target])
            published += 1; total += 1; versions[revision] += 1
            hits6 += int(six_hit); hits5 += int(five_hit); lost += int(six_hit and not five_hit)
            if five_hit:
                win += 1; loss = 0; maximum_win = max(maximum_win, win)
            else:
                loss += 1; win = 0; maximum_loss = max(maximum_loss, loss)
        else:
            if target in pending:
                published += 1; pending_count += 1
            win = loss = 0
    assert hits6 - hits5 == lost
    return {'planned_count': len(periods), 'first_period': periods[0] if periods else None,
        'last_period': periods[-1] if periods else None,
        'observed_count': sum(target in observed for target in periods),
        'total': total, 'hits6': hits6, 'hits5': hits5, 'lost_hits': lost,
        'percent6': round(100 * hits6 / total, 4) if total else None,
        'percent5': round(100 * hits5 / total, 4) if total else None,
        'loss_of_six_hits_percent': round(100 * lost / hits6, 4) if hits6 else None,
        'published_count': published, 'unpublished_count': len(periods) - published - conflict_count,
        'pending_count': pending_count, 'conflict_count': conflict_count,
        'max_win_streak5': maximum_win, 'max_loss_streak5': maximum_loss,
        'current_win_streak5': win, 'current_loss_streak5': loss,
        'latest_verified': bool(periods and periods[-1] in verified and periods[-1] not in conflicts),
        'versions': versions}


def build_comparison(ledger, now, records=(), conflicting_periods=()):
    """Use continuous closed target slots, never the last N successful rows."""
    if not isinstance(records, (list, tuple)):
        raise ValueError('invalid-comparison-history')
    conflicts = set(ledger['conflicts']) | validate_conflicting_periods(conflicting_periods)
    observed = {}
    for raw in [*records, *(entry['actual'] for entry in ledger['verified'].values())]:
        if not isinstance(raw, dict):
            continue
        record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if record is not None:
            target = record['period']
            if target in observed and observed[target] != record:
                conflicts.add(target)
            observed[target] = record
    anchor = max(observed, key=period_slot) if observed else None
    day_start = business_start(now)
    daily_periods = []
    if anchor and parse_time(observed[anchor]['opentime']) >= day_start:
        target = anchor
        while period_slot(target) >= day_start:
            daily_periods.append(target)
            target = previous_period(target)
        daily_periods.reverse()
    return {'version': 1, 'revision': REVISION,
        'biz_date': day_start.strftime('%Y-%m-%d'), 'anchor_period': anchor,
        'daily': _window(daily_periods, ledger, conflicts, observed),
        'rolling50': _window(_periods(anchor, 50) if anchor else [], ledger, conflicts, observed),
        'rolling200': _window(_periods(anchor, 200) if anchor else [], ledger, conflicts, observed)}
