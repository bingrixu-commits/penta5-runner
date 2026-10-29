"""Verified per-position Pro exclusions; never changes a published prediction."""
from copy import deepcopy
from fastcopy import json_copy
from datetime import datetime
from pathlib import Path
import fcntl
import json

from ai_verified_stats import atomic_json, next_period, publication_evidence_error
from stats_history import (TZ, business_start, load_snapshot, parse_period, parse_time,
                           period_slot, valid_draw_record, validate_conflicting_periods)

ROOT = Path('/root/penta5-runner')
REVISION = 'pro-four-stats-v1'
KEYS = ('k1', 'k2', 'k3', 'k4')
CURRENT_PREDICTION_REVISION = 'pro-four-v4-isolated-weighted'
LEGACY_PREDICTION_REVISIONS = ('pro-four-v1', 'pro-four-v2-position-features',
                               'pro-four-v3-recent-weighted')


def _current(now):
    if not isinstance(now, datetime):
        raise ValueError('invalid-statistics-time')
    return now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)


def _publication(seal, reference, now, root):
    from publication_validation_cache import validate_publication as cached_publication
    if not isinstance(seal, dict) or not isinstance(reference, str) or not parse_period(reference):
        raise ValueError('invalid-pro-four-publication')
    checked = cached_publication('pro4', seal, reference, now, published=True, root=root)
    if checked.get('ref_period') != reference:
        raise ValueError('invalid-pro-four-reference')
    return json_copy(checked)


def _verified_entry(proof, record, now):
    if not isinstance(record, dict):
        raise ValueError('invalid-actual-draw')
    actual = valid_draw_record(record.get('period'), record.get('numbers'), record.get('opentime'), now)
    if actual is None or actual['period'] != proof['target_period']:
        raise ValueError('invalid-or-nonexact-actual-draw')
    error = publication_evidence_error(proof, actual)
    if error:
        raise ValueError(error)
    digits = actual['numbers']
    outcomes = {key: digits[index] != proof['response'][key][0] for index, key in enumerate(KEYS)}
    return {'ref_period': proof['ref_period'], 'target_period': proof['target_period'],
            'publication': json_copy(proof), 'actual': actual,
            'biz_date': business_start(parse_time(actual['opentime'])).strftime('%Y-%m-%d'),
            'outcomes': outcomes}


def _restore(previous, now, root):
    result = {'version': 1, 'revision': REVISION, 'updated_at': now.isoformat(),
              'verified': {}, 'pending': {}, 'conflicts': [], 'rejected': []}
    if previous is None or previous == {}:
        return result
    if (not isinstance(previous, dict) or type(previous.get('version')) is not int
            or previous['version'] != 1 or previous.get('revision') != REVISION
            or not isinstance(previous.get('verified'), dict)
            or not isinstance(previous.get('pending'), dict)):
        raise ValueError('invalid-pro-four-ledger')
    result['conflicts'] = sorted(validate_conflicting_periods(previous.get('conflicts', [])))
    for target, entry in previous['verified'].items():
        if not isinstance(entry, dict) or not isinstance(entry.get('publication'), dict):
            raise ValueError('corrupt-pro-four-evidence')
        proof = _publication(entry['publication'], entry.get('ref_period'), now, root)
        verified = _verified_entry(proof, entry.get('actual'), now)
        if (target != verified['target_period'] or not isinstance(entry.get('outcomes'), dict)
                or any(type(entry['outcomes'].get(key)) is not bool for key in KEYS)
                or entry != verified):
            raise ValueError('corrupt-pro-four-history-refusing-overwrite')
        result['verified'][target] = deepcopy(entry)
    for target, seal in previous['pending'].items():
        if not isinstance(seal, dict):
            raise ValueError('corrupt-pro-four-pending')
        proof = _publication(seal, seal.get('ref_period'), now, root)
        if target != proof['target_period'] or target in result['verified']:
            raise ValueError('corrupt-pro-four-pending-identity')
        result['pending'][target] = proof
    return result


def refresh_stats(seals, records, now, previous=None, *, root=ROOT, conflicting_periods=()):
    """Settle genuine pre-draw Pro4 publications against the same actual position."""
    now = _current(now)
    ledger = _restore(previous, now, root)
    if (not isinstance(seals, dict) or not isinstance(seals.get('prediction_seals', {}), dict)
            or not isinstance(records, (list, tuple))):
        raise ValueError('invalid-pro-four-statistics-input')
    conflicts = set(ledger['conflicts']) | validate_conflicting_periods(conflicting_periods)
    actuals = {}
    for raw in records:
        if not isinstance(raw, dict):
            continue
        actual = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if actual is None:
            continue
        target = actual['period']
        if target in actuals and actuals[target] != actual:
            conflicts.add(target)
        actuals[target] = actual
    for reference, seal in seals.get('prediction_seals', {}).items():
        try:
            proof = _publication(seal, reference, now, root)
            target = proof['target_period']
            stored = ledger['verified'].get(target)
            existing = stored['publication'] if stored else ledger['pending'].get(target)
            if existing is not None and existing != proof:
                conflicts.add(target)
                raise ValueError('conflicting-pro-four-publication')
            if stored is None:
                ledger['pending'][target] = proof
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            # Never echo raw provider content, nor count an unverifiable seal.
            ledger['rejected'].append({'ref_period': str(reference), 'reason': 'invalid-publication'})
            if isinstance(seal, dict):
                target = seal.get('target_period')
                if target in ledger['verified'] or target in ledger['pending']:
                    conflicts.add(target)
    for target, entry in ledger['verified'].items():
        if target in actuals and entry['actual'] != actuals[target]:
            conflicts.add(target)
    for target, proof in list(ledger['pending'].items()):
        if target in conflicts or target not in actuals:
            continue
        try:
            ledger['verified'][target] = _verified_entry(proof, actuals[target], now)
            del ledger['pending'][target]
        except (ValueError, TypeError, KeyError):
            conflicts.add(target)
            ledger['rejected'].append({'target_period': target, 'reason': 'invalid-settlement-evidence'})
    tracked = set(ledger['verified']) | set(ledger['pending'])
    # An observed draw conflict survives even before this model's seal is
    # visible. A later single source copy cannot resolve that ambiguity.
    ledger['conflicts'] = sorted(conflicts)
    return ledger


def _overall_summary(entries):
    hits = wins = losses = max_wins = max_losses = 0
    previous = None
    for entry in entries:
        target = entry['target_period']
        if previous is not None and next_period(previous) != target:
            wins = losses = 0
        passed = all(entry['outcomes'][key] is True for key in KEYS)
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
    total = len(entries)
    return {'hits': hits, 'total': total, 'percent': round(hits / total * 100, 4) if total else None,
            'max_win_streak': max_wins, 'max_loss_streak': max_losses}


def _entry_summary(entries):
    """Derived counts only; never mutates the mixed-version verified ledger."""
    total = len(entries)
    metrics = {}
    for key in KEYS:
        hits = sum(entry['outcomes'][key] for entry in entries)
        metrics[key] = {'hits': hits, 'total': total,
                        'percent': round(hits / total * 100, 4) if total else None}
    return {'metrics': metrics, 'overall': _overall_summary(entries),
            'latest_settled_period': entries[-1]['target_period'] if entries else None}


def make_summary(stats, now, *, root=ROOT):
    """Current strategy's day/cumulative; older strategy evidence stays separate."""
    now = _current(now)
    ledger = _restore(stats, now, root)
    day = business_start(now).strftime('%Y-%m-%d')
    conflicts = set(ledger['conflicts'])
    all_entries = sorted((entry for target, entry in ledger['verified'].items() if target not in conflicts),
                         key=lambda entry: (parse_time(entry['actual']['opentime']), entry['target_period']))

    def today(target):
        return business_start(period_slot(target)).strftime('%Y-%m-%d') == day

    def for_revision(revision):
        entries = [entry for entry in all_entries if entry['publication']['revision'] == revision]
        current = [entry for entry in entries if entry['biz_date'] == day]
        pending = [target for target, proof in ledger['pending'].items()
                   if proof['revision'] == revision and today(target) and target not in conflicts]
        tracked = {target for target, entry in ledger['verified'].items()
                   if entry['publication']['revision'] == revision}
        tracked.update(target for target, proof in ledger['pending'].items() if proof['revision'] == revision)
        return {'revision': revision, 'biz_date': day, **_entry_summary(current),
                'cumulative': _entry_summary(entries), 'pending_count': len(pending),
                'overdue_count': sum(period_slot(target) <= now for target in pending),
                'conflict_count': sum(today(target) for target in conflicts & tracked),
                'stats_error': False}

    summary = for_revision(CURRENT_PREDICTION_REVISION)
    summary['legacy_history'] = {revision: for_revision(revision)
                                 for revision in LEGACY_PREDICTION_REVISIONS}
    return summary


def _read(path):
    try:
        result = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(result, dict):
        raise ValueError('invalid-pro-four-statistics-file')
    return result


def _content_unchanged(new, old):
    """Compare ledgers ignoring updated_at, which changes every cycle.

    Writing the 54MB ledger every 25s rewrote identical bytes ~190GB/day.
    """
    if not isinstance(old, dict) or set(new) != set(old):
        return False
    for key in new:
        if key == 'updated_at':
            continue
        if new[key] != old[key]:
            return False
    return True


def refresh_pro_stats(snapshot=None, root=ROOT):
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(
        state_path=root / 'data/state.json', cache_path=root / 'data/stats_draw_history.json', persist=False)
    now = _current(snapshot['now'])
    path = root / 'data/pro_position_hit_stats.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            # The ledger re-validates every historical proof on each refresh; without
            # this cache session that is thousands of full validations per cycle.
            # The cache is code-signature guarded, so a source change disables it.
            from publication_validation_cache import session as validation_session
            with validation_session(root):
                previous = _read(path)
                ledger = refresh_stats(_read(root / 'data/pro_position_seals.json'), snapshot['records'],
                                       now, previous, root=root,
                                       conflicting_periods=snapshot.get('conflicting_periods', ()))
                summary = make_summary(ledger, now, root=root)
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            # Preserve the last genuine ledger; do not reset a broken proof to zero.
            return {'biz_date': business_start(now).strftime('%Y-%m-%d'), 'metrics': {}, 'overall': {},
                    'latest_settled_period': None, 'pending_count': 0,
                    'overdue_count': 0, 'conflict_count': 0, 'stats_error': True}
        if _content_unchanged(ledger, previous):
            # Keep byte-identical output: no 54MB rewrite, no updated_at churn.
            ledger['updated_at'] = previous.get('updated_at', ledger['updated_at'])
        else:
            atomic_json(path, ledger)
        atomic_json(root / 'data/pro_position_stats.json', summary)
        return summary
