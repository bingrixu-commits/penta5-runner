"""Pure, exact-target hit accounting for published framework predictions.

This module never reads/writes files, calls models, or changes a sealed card.
The caller owns locking and persistence of data/r1_framework_hit_stats.json.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from itertools import combinations

from ai_prediction_guard import validate_framework_response
from ai_verified_stats import next_period, publication_evidence_error
from stats_history import (TZ, business_start, parse_period, parse_time,
                           period_slot, valid_draw_record, validate_conflicting_periods)

METRIC_KEYS = ('q_kill1', 'b_kill1', 'he_h3_kill', 'he_q3_kill',
               'pattern_ds', 'pattern_dx', 'he2_keep', 'he3_keep')
VERSION = 1
ROLLING_WINDOW = 100
_PROOF_FIELDS = ('ref_period', 'target_period', 'evidence_version',
                 'publication_state', 'reference_open_time', 'generated_at', 'published_at')


def _current(now):
    if not isinstance(now, datetime):
        raise ValueError('now must be a datetime')
    return now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)


def _empty(now):
    return {'version': VERSION, 'updated_at': now.isoformat(),
            'verified': {}, 'pending': {}, 'conflicts': [], 'rejected': []}


def _stored_conflicts(previous):
    """Retain ambiguity even after one conflicting source copy disappears.

    Older ledgers carried conflict diagnostics only in ``rejected``. Import
    those explicit conflict identities once; ordinary unpublished/invalid new
    predictions remain retryable and are not manufactured into conflicts.
    """
    conflicts = validate_conflicting_periods(previous.get('conflicts', ()))
    rejected = previous.get('rejected', [])
    if not isinstance(rejected, list):
        raise ValueError('Invalid framework rejection history')
    target_reasons = {'settled-draw-conflict', 'conflicting-actual-draws'}
    reference_reasons = {'settled-target-publication-conflict', 'pending-target-publication-conflict'}
    for row in rejected:
        if not isinstance(row, dict) or not isinstance(row.get('reason'), str):
            raise ValueError('Invalid framework rejection history')
        if row['reason'] in target_reasons:
            conflicts.update(validate_conflicting_periods([row.get('target_period')]))
        elif row['reason'] in reference_reasons:
            reference = row.get('ref_period')
            parsed = parse_period(reference)
            if not isinstance(reference, str) or not parsed or reference != parsed[0]:
                raise ValueError('Invalid framework rejected publication identity')
            conflicts.add(next_period(reference))
    return conflicts


def _publication(seal, now):
    """Validate a pending proof without manufacturing a placeholder actual draw."""
    if not isinstance(seal, dict):
        raise ValueError('invalid-seal-object')
    reference, target = seal.get('ref_period'), seal.get('target_period')
    if not parse_period(reference) or target != next_period(reference):
        raise ValueError('invalid-target-transition')
    if seal.get('evidence_version') != 2 or seal.get('publication_state') != 'published':
        raise ValueError('legacy-or-unpublished-evidence')
    opened = parse_time(seal.get('reference_open_time'))
    generated = parse_time(seal.get('generated_at'))
    published = parse_time(seal.get('published_at'))
    reference_slot, cutoff = period_slot(reference), period_slot(target)
    if (not opened or not generated or not published
            or not reference_slot <= opened < reference_slot + timedelta(minutes=1)
            or not opened <= generated <= published < cutoff or published > now):
        raise ValueError('invalid-or-future-publication-evidence')
    response = validate_framework_response(seal.get('response'))
    result = {name: deepcopy(seal.get(name)) for name in _PROOF_FIELDS}
    result['response'] = response
    return result


def _outcomes(response, digits):
    pair_tails = {sum(digits[index] for index in indexes) % 10
                  for indexes in combinations(range(4), 2)}
    triple_tails = {sum(digits[index] for index in indexes) % 10
                    for indexes in combinations(range(4), 3)}
    parity = ''.join('单' if value % 2 else '双' for value in digits)
    size = ''.join('大' if value >= 5 else '小' for value in digits)
    return {
        'q_kill1': digits[0] != response['q_kill1'],
        'b_kill1': digits[1] != response['b_kill1'],
        'he_h3_kill': sum(digits[1:]) % 10 != response['he_h3_kill'],
        'he_q3_kill': sum(digits[:3]) % 10 != response['he_q3_kill'],
        'pattern_ds': parity != response['pattern_ds'],
        'pattern_dx': size != response['pattern_dx'],
        'he2_keep': bool(pair_tails.intersection(map(int, response['he2_keep']))),
        'he3_keep': bool(triple_tails.intersection(map(int, response['he3_keep']))),
    }


def _verified_entry(proof, record, now):
    checked = valid_draw_record(record.get('period'), record.get('numbers'), record.get('opentime'), now)
    if checked is None or checked['period'] != proof['target_period']:
        raise ValueError('invalid-or-nonexact-actual-draw')
    evidence_error = publication_evidence_error(proof, checked)
    if evidence_error:
        raise ValueError(evidence_error)
    return {'ref_period': proof['ref_period'], 'target_period': proof['target_period'],
            'biz_date': business_start(parse_time(checked['opentime'])).strftime('%Y-%m-%d'),
            'publication': deepcopy(proof), 'actual': checked,
            'outcomes': _outcomes(proof['response'], checked['numbers'])}


def _restore(previous, now):
    ledger = _empty(now)
    if previous is None or previous == {}:
        return ledger
    if (not isinstance(previous, dict) or type(previous.get('version')) is not int
            or previous['version'] != VERSION
            or not isinstance(previous.get('verified'), dict)
            or not isinstance(previous.get('pending'), dict)):
        raise ValueError('Unsupported framework hit ledger; legacy totals cannot be imported')
    ledger['conflicts'] = sorted(_stored_conflicts(previous))
    for target, entry in previous['verified'].items():
        if not isinstance(entry, dict):
            raise ValueError('Corrupt verified framework hit entry')
        proof = _publication(entry.get('publication'), now)
        actual = entry.get('actual')
        if not isinstance(actual, dict) or target != proof['target_period']:
            raise ValueError('Corrupt verified framework target identity')
        verified = _verified_entry(proof, actual, now)
        if (entry.get('ref_period') != verified['ref_period']
                or entry.get('target_period') != verified['target_period']
                or entry.get('biz_date') != verified['biz_date']
                or entry.get('outcomes') != verified['outcomes']
                or any(type(entry.get('outcomes', {}).get(key)) is not bool for key in METRIC_KEYS)):
            raise ValueError('Corrupt verified framework evidence; refusing to rewrite old history')
        ledger['verified'][target] = deepcopy(entry)
    for target, seal in previous['pending'].items():
        proof = _publication(seal, now)
        if target != proof['target_period'] or target in ledger['verified']:
            raise ValueError('Corrupt framework pending identity; refusing to discard stored evidence')
        ledger['pending'][target] = proof
    return ledger


def refresh_stats(seals, records, now, previous=None, *, conflicting_periods=()):
    """Return a new ledger, preserving verified/pending history beyond source windows.

    ``seals`` is the entire r1_framework_seals.json object, whose
    prediction_seals maps reference periods to immutable framework publications.
    ``records`` is a sequence of stats_history records. Inputs are never mutated.
    """
    now = _current(now)
    ledger = _restore(previous, now)
    if not isinstance(seals, dict):
        raise ValueError('Framework seals must be an object')
    publications = seals.get('prediction_seals', {})
    if not isinstance(publications, dict):
        raise ValueError('prediction_seals must be an object')
    if not isinstance(records, (list, tuple)):
        raise ValueError('Actual draw records must be a sequence')
    actuals = {}
    conflicts = set(ledger['conflicts']) | validate_conflicting_periods(conflicting_periods)
    for raw in records:
        if not isinstance(raw, dict):
            continue
        record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if record is None:
            continue
        period = record['period']
        if period in actuals and actuals[period]['numbers'] != record['numbers']:
            conflicts.add(period)
        actuals[period] = record
    for reference, seal in publications.items():
        try:
            proof = _publication(seal, now)
            if str(reference) != proof['ref_period']:
                raise ValueError('invalid-seal-map-reference')
            target = proof['target_period']
            stored = ledger['verified'].get(target)
            if stored is not None:
                if stored['publication'] != proof:
                    conflicts.add(target)
                    raise ValueError('settled-target-publication-conflict')
                continue
            existing = ledger['pending'].get(target)
            if existing is not None and existing != proof:
                conflicts.add(target)
                raise ValueError('pending-target-publication-conflict')
            ledger['pending'][target] = proof
        except (ValueError, TypeError, KeyError) as error:
            ledger['rejected'].append({'ref_period': str(reference), 'reason': str(error)})
            # A new invalid/unpublished card is not a prediction. A rejected
            # replacement of already retained evidence is an ambiguous target.
            parsed = parse_period(reference) if isinstance(reference, str) else None
            if parsed and reference == parsed[0]:
                target = next_period(reference)
                if target in ledger['verified'] or target in ledger['pending']:
                    conflicts.add(target)
    for target, entry in ledger['verified'].items():
        actual = actuals.get(target)
        if target in conflicts or (actual and actual['numbers'] != entry['actual']['numbers']):
            conflicts.add(target)
            ledger['rejected'].append({'target_period': target, 'reason': 'settled-draw-conflict'})
    for target, proof in list(ledger['pending'].items()):
        if target in conflicts:
            ledger['rejected'].append({'target_period': target, 'reason': 'conflicting-actual-draws'})
            continue
        actual = actuals.get(target)
        if actual is None:
            continue
        try:
            ledger['verified'][target] = _verified_entry(proof, actual, now)
            del ledger['pending'][target]
        except (ValueError, TypeError, KeyError) as error:
            conflicts.add(target)
            ledger['rejected'].append({'target_period': target, 'reason': str(error)})
    # Keep unpredicted conflicting draw identities as well: they interrupt a
    # current streak and must not become clean history after source truncation.
    ledger['conflicts'] = sorted(conflicts)
    return ledger


def _overall_summary(entries):
    """Eight verified outcomes must all pass; unknown periods break a run."""
    total = hits = wins = losses = max_wins = max_losses = 0
    previous = None
    for entry in entries:
        target = entry['target_period']
        if previous is not None and next_period(previous) != target:
            wins = losses = 0
        passed = all(entry['outcomes'][key] is True for key in METRIC_KEYS)
        total += 1
        hits += int(passed)
        if passed:
            wins += 1
            losses = 0
            max_wins = max(max_wins, wins)
        else:
            losses += 1
            wins = 0
            max_losses = max(max_losses, losses)
        previous = target
    return {'hits': hits, 'total': total,
            'percent': round(hits / total * 100, 4) if total else None,
            'max_win_streak': max_wins, 'max_loss_streak': max_losses,
            'current_win_streak': wins, 'current_loss_streak': losses}


def _rolling_summary(entries, window=ROLLING_WINDOW):
    """Rolling view over the most recent verified periods of the same policy scope.

    The daily block answers "how did today go"; a 14-period day is far too small
    to judge an 8-item conjunction, so the card also carries a rolling view over
    the latest ``window`` verified periods.  Outcomes are recomputed from the
    stored evidence only; the ledger is never rewritten.
    """
    selected = list(entries[-window:]) if window > 0 else []
    metrics = {key: {'hits': 0, 'total': 0, 'percent': None} for key in METRIC_KEYS}
    for entry in selected:
        for key in METRIC_KEYS:
            metrics[key]['total'] += 1
            metrics[key]['hits'] += int(entry['outcomes'][key])
    for metric in metrics.values():
        if metric['total']:
            metric['percent'] = round(metric['hits'] / metric['total'] * 100, 1)
    overall = _overall_summary(selected)
    return {'window_target': window, 'window': len(selected), 'available': bool(selected),
            'metrics': metrics, 'overall': overall}


def make_summary(ledger, now, records=None, *, conflicting_periods=(), allowed_targets=None, strategy_id=None):
    """Summarize only actual opens in the requested Beijing 05:00 business day."""
    now = _current(now)
    checked = _restore(ledger, now)
    historical_policy_count = 0
    if allowed_targets is not None:
        if not isinstance(allowed_targets, (set, frozenset)):
            raise ValueError('Invalid framework policy targets')
        active_day = business_start(now).strftime('%Y-%m-%d')
        historical_policy_count = sum(entry['biz_date'] == active_day
            for target, entry in checked['verified'].items() if target not in allowed_targets)
        # Derived view only: the original multi-policy evidence ledger remains
        # intact, including outcomes that belong to earlier policies.
        checked['verified'] = {target: entry for target, entry in checked['verified'].items()
                               if target in allowed_targets}
        checked['pending'] = {target: entry for target, entry in checked['pending'].items()
                              if target in allowed_targets}
    day = business_start(now).strftime('%Y-%m-%d')
    observed = {}
    if records is not None:
        if not isinstance(records, (list, tuple)):
            raise ValueError('Actual draw records must be a sequence')
        for raw in records:
            if not isinstance(raw, dict):
                continue
            record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
            if record:
                observed.setdefault(record['period'], set()).add(tuple(record['numbers']))
    tracked = set(checked['verified']) | set(checked['pending'])
    known_conflicts = set(checked['conflicts']) | validate_conflicting_periods(conflicting_periods)
    known_conflicts.update(target for target, actuals in observed.items() if len(actuals) > 1)
    conflicts = known_conflicts & tracked
    for target, entry in checked['verified'].items():
        actuals = observed.get(target)
        if actuals and tuple(entry['actual']['numbers']) not in actuals:
            conflicts.add(target)
    entries = sorted((entry for entry in checked['verified'].values()
                      if entry['biz_date'] == day and entry['target_period'] not in conflicts),
                     key=lambda entry: (parse_time(entry['actual']['opentime']), entry['target_period']))
    rolling_entries = sorted((entry for target, entry in checked['verified'].items()
                              if target not in conflicts),
                             key=lambda entry: entry['target_period'])
    rolling = _rolling_summary(rolling_entries)
    metrics = {key: {'hits': 0, 'total': 0, 'percent': None} for key in METRIC_KEYS}
    for entry in entries:
        for key in METRIC_KEYS:
            metrics[key]['total'] += 1
            metrics[key]['hits'] += int(entry['outcomes'][key])
    for metric in metrics.values():
        if metric['total']:
            metric['percent'] = round(metric['hits'] / metric['total'] * 100, 1)
    latest = entries[-1] if entries else None
    overall = _overall_summary(entries)
    known_targets = {target for target in observed
                     if business_start(period_slot(target)).strftime('%Y-%m-%d') == day}
    known_targets.update(entry['target_period'] for entry in checked['verified'].values()
                         if entry['biz_date'] == day)
    known_targets.update(target for target in known_conflicts
                         if period_slot(target) <= now
                         and business_start(period_slot(target)).strftime('%Y-%m-%d') == day)
    latest_known = max(known_targets, key=period_slot, default=None)
    if latest is not None and latest_known and latest['target_period'] != latest_known:
        # A later closed slot without a usable result breaks the current run.
        # It is neither a win nor a loss and cannot change historical totals.
        overall.update(current_win_streak=0, current_loss_streak=0,
                       current_streak_interrupted=True, current_streak_as_of=latest_known)
    return {'biz_date': day, 'metrics': metrics, 'overall': overall, 'rolling': rolling,
            'strategy_id': strategy_id,
            'historical_policy_count': historical_policy_count,
            'latest_settled_period': latest['target_period'] if latest else None,
            'conflict_count': sum(business_start(period_slot(target)).strftime('%Y-%m-%d') == day
                                  for target in conflicts),
            'pending_count': sum(business_start(period_slot(target)).strftime('%Y-%m-%d') == day
                                 for target in checked['pending'] if target not in conflicts)}
