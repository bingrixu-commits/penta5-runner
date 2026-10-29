"""Daily position rates for genuine pre-draw stat50 candidates, without AI pairing."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from ai_prediction_guard import read_stats
from ai_verified_stats import atomic_json, next_period, publication_evidence_error, sync_parent_directory
from stat50_compare import EXPERIMENT_ID, _archive, _digest, _stat_proof, _validate_saved
from stats_history import (TZ, business_start, load_snapshot, parse_period, parse_time,
                           period_slot, valid_draw_record, validate_conflicting_periods)

ROOT = Path('/root/penta5-runner')
REVISION = 'stat50-position-stats-v1'
MODELS = ('frequency', 'markov')
KEYS = ('k1', 'k2', 'k3', 'k4')
MAX_TARGETS = 288
REJECTION_CODES = {'invalid-publication', 'conflicting-publication', 'invalid-settlement-evidence'}


def _current(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('stat50-position-clock-must-be-aware')
    return now.astimezone(TZ)


def _day(now):
    return business_start(now).strftime('%Y-%m-%d')


def _today(target, day):
    parsed = parse_period(target)
    return (isinstance(target, str) and parsed is not None and target == parsed[0]
            and _day(period_slot(target)) == day)


def _publication(seal, reference, now, root):
    parsed = parse_period(reference)
    if not isinstance(reference, str) or parsed is None or reference != parsed[0]:
        raise ValueError('invalid-stat50-position-reference')
    history = _validate_saved(seal, reference, now, published=True)
    _archive(root, 'framework', reference, seal.get('model_context_audit'), history,
             parse_time(seal['generated_at']), now)
    return deepcopy(seal)


def _verified_entry(proof, record, now):
    if not isinstance(record, dict):
        raise ValueError('invalid-stat50-position-actual')
    actual = valid_draw_record(record.get('period'), record.get('numbers'), record.get('opentime'), now)
    if actual is None or actual['period'] != proof['target_period']:
        raise ValueError('stat50-position-nonexact-actual')
    error = publication_evidence_error(proof, actual)
    if error:
        raise ValueError(error)
    outcomes = {model: {key: actual['numbers'][index] != proof['response'][model][index]
                        for index, key in enumerate(KEYS)} for model in MODELS}
    entry = {'ref_period': proof['ref_period'], 'target_period': proof['target_period'],
             'biz_date': _day(parse_time(actual['opentime'])), 'publication': deepcopy(proof),
             'stat_publication': _stat_proof(proof), 'actual': actual, 'outcomes': outcomes}
    entry['entry_sha256'] = _digest(entry)
    return entry


def _bounded(ledger, day):
    targets = set(ledger['verified']) | set(ledger['pending']) | set(ledger['conflicts']) | set(ledger['rejected'])
    if len(targets) > MAX_TARGETS or any(not _today(target, day) for target in targets):
        raise ValueError('stat50-position-daily-target-bound')
    if set(ledger['pending_sha256']) != set(ledger['pending']):
        raise ValueError('stat50-position-pending-hash-index')


def _restore(previous, now, root):
    day = _day(now)
    ledger = {'version': 1, 'experiment_id': EXPERIMENT_ID, 'revision': REVISION,
              'biz_date': day, 'business_start': business_start(now).isoformat(),
              'updated_at': now.isoformat(), 'verified': {}, 'pending': {}, 'pending_sha256': {},
              'conflicts': [], 'rejected': {}}
    if previous is None or previous == {}:
        return ledger
    if (not isinstance(previous, dict) or type(previous.get('version')) is not int
            or previous['version'] != 1 or previous.get('experiment_id') != EXPERIMENT_ID
            or previous.get('revision') != REVISION or previous.get('biz_date') != day
            or previous.get('business_start') != ledger['business_start']
            or not isinstance(previous.get('verified'), dict) or not isinstance(previous.get('pending'), dict)
            or not isinstance(previous.get('pending_sha256'), dict)
            or not isinstance(previous.get('rejected'), dict)):
        raise ValueError('invalid-stat50-position-daily-ledger')
    ledger['conflicts'] = sorted(validate_conflicting_periods(previous.get('conflicts', [])))
    ledger['rejected'] = deepcopy(previous['rejected'])
    ledger['pending_sha256'] = deepcopy(previous['pending_sha256'])
    if any(code not in REJECTION_CODES for code in ledger['rejected'].values()):
        raise ValueError('invalid-stat50-position-rejection')
    _bounded(dict(ledger, verified=previous['verified'], pending=previous['pending']), day)
    for target, entry in previous['verified'].items():
        if not isinstance(entry, dict) or not isinstance(entry.get('publication'), dict):
            raise ValueError('corrupt-stat50-position-evidence')
        proof = _publication(entry['publication'], entry.get('ref_period'), now, root)
        expected = _verified_entry(proof, entry.get('actual'), now)
        outcomes = entry.get('outcomes')
        if (target != expected['target_period'] or expected['biz_date'] != day or entry != expected
                or not isinstance(outcomes, dict) or set(outcomes) != set(MODELS)
                or any(not isinstance(outcomes[model], dict) or set(outcomes[model]) != set(KEYS)
                       or any(type(outcomes[model][key]) is not bool for key in KEYS) for model in MODELS)):
            raise ValueError('corrupt-stat50-position-history-refusing-overwrite')
        ledger['verified'][target] = deepcopy(entry)
    for target, seal in previous['pending'].items():
        if not isinstance(seal, dict):
            raise ValueError('corrupt-stat50-position-pending')
        proof = _publication(seal, seal.get('ref_period'), now, root)
        if (target != proof['target_period'] or target in ledger['verified']
                or ledger['pending_sha256'].get(target) != _digest(proof)):
            raise ValueError('corrupt-stat50-position-pending-identity')
        ledger['pending'][target] = proof
    return ledger


def refresh_stats(seals, records, now, previous=None, *, root=ROOT, conflicting_periods=()):
    """Pure daily settlement; verified and pending proofs survive source-window loss."""
    now = _current(now)
    ledger = _restore(previous, now, root)
    day = ledger['biz_date']
    if (not isinstance(seals, dict) or not isinstance(seals.get('prediction_seals', {}), dict)
            or not isinstance(records, (list, tuple))):
        raise ValueError('invalid-stat50-position-input')
    actuals = {}
    source_conflicts = validate_conflicting_periods(conflicting_periods)
    conflicts = set(ledger['conflicts'])
    for raw in records:
        if not isinstance(raw, dict):
            continue
        actual = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if actual is None:
            continue
        target = actual['period']
        if target in actuals and actuals[target] != actual:
            source_conflicts.add(target)
        actuals[target] = actual
    for reference, seal in seals.get('prediction_seals', {}).items():
        parsed = parse_period(reference)
        if not isinstance(reference, str) or parsed is None or reference != parsed[0]:
            continue
        target = next_period(reference)
        if not _today(target, day):
            continue  # Only the current business-day file can be updated.
        if (isinstance(seal, dict) and seal.get('publication_state') != 'published'
                and target not in ledger['verified'] and target not in ledger['pending']):
            continue  # Prepared/expired predictions cannot supply any score.
        try:
            proof = _publication(seal, reference, now, root)
            stored = ledger['verified'].get(target)
            old = stored['publication'] if stored else ledger['pending'].get(target)
            if old is not None and old != proof:
                conflicts.add(target)
                ledger['rejected'][target] = 'conflicting-publication'
                continue
            if stored is None:
                ledger['pending'][target] = proof
                ledger['pending_sha256'][target] = _digest(proof)
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            ledger['rejected'][target] = 'invalid-publication'
            if target in ledger['verified'] or target in ledger['pending']:
                conflicts.add(target)
    for target in set(ledger['verified']) | set(ledger['pending']):
        stored = ledger['verified'].get(target)
        proof = stored['publication'] if stored else ledger['pending'][target]
        if target in source_conflicts:
            conflicts.add(target)
        if stored is not None and target in actuals and stored['actual'] != actuals[target]:
            conflicts.add(target)
        # The 50 source records must still agree with any currently observable
        # actuals. A conflicting source cannot silently validate a candidate.
        if any(row['period'] in source_conflicts or row['period'] in actuals
               and actuals[row['period']] != row for row in proof['history']):
            conflicts.add(target)
    for target, proof in list(ledger['pending'].items()):
        if target in conflicts or target not in actuals:
            continue
        try:
            entry = _verified_entry(proof, actuals[target], now)
            if entry['biz_date'] != day:
                raise ValueError('stat50-position-actual-business-day-mismatch')
            ledger['verified'][target] = entry
            del ledger['pending'][target]
            del ledger['pending_sha256'][target]
        except (ValueError, TypeError, KeyError):
            conflicts.add(target)
            ledger['rejected'][target] = 'invalid-settlement-evidence'
    tracked = set(ledger['verified']) | set(ledger['pending']) | set(ledger['rejected'])
    ledger['conflicts'] = sorted(conflicts & tracked)
    _bounded(ledger, day)
    return ledger


def make_summary(stats, now, *, root=ROOT):
    now = _current(now)
    ledger = _restore(stats, now, root)
    conflicts = set(ledger['conflicts'])
    entries = sorted((entry for target, entry in ledger['verified'].items() if target not in conflicts),
                     key=lambda entry: (parse_time(entry['actual']['opentime']), entry['target_period']))
    total = len(entries)
    models = {}
    for model in MODELS:
        metrics = {}
        for key in KEYS:
            hits = sum(entry['outcomes'][model][key] for entry in entries)
            metrics[key] = {'hits': hits, 'total': total,
                            'percent': round(100 * hits / total, 4) if total else None}
        models[model] = {'metrics': metrics}
    return {'version': 1, 'experiment_id': EXPERIMENT_ID, 'revision': REVISION,
            'biz_date': ledger['biz_date'], 'business_start': ledger['business_start'],
            'updated_at': now.isoformat(), 'models': models,
            'latest_settled_period': entries[-1]['target_period'] if entries else None,
            'pending_count': sum(target not in conflicts for target in ledger['pending']),
            'conflict_count': len(conflicts), 'rejected_count': len(ledger['rejected']), 'stats_error': False}


def refresh_stat50_position_stats(snapshot=None, root=ROOT):
    """Own lock, bounded daily evidence and disposable summary; no model calls."""
    import fcntl
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(
        state_path=root/'data/state.json', cache_path=root/'data/stats_draw_history.json', persist=False)
    now = _current(snapshot['now'])
    directory = root/'data/stat50_position_verified'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    sync_parent_directory(directory)
    path = directory/f'{_day(now)}.json'
    with (root/'data/stat50_position_stats.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = read_stats(path)
            ledger = refresh_stats(read_stats(root/'data/stat50_compare_seals.json'), snapshot['records'], now,
                                   previous, root=root, conflicting_periods=snapshot.get('conflicting_periods', ()))
            summary = make_summary(ledger, now, root=root)
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            # Corruption never turns prior genuine history into a new zero day.
            error_summary = make_summary(_restore(None, now, root), now, root=root)
            error_summary['stats_error'] = True
            return error_summary
        atomic_json(path, ledger)
        atomic_json(root/'data/stat50_position_stats.json', summary)
        return summary
