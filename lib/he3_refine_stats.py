"""Verified five-tail accounting; never changes a published prediction."""
from copy import deepcopy
from datetime import datetime
from itertools import combinations
from pathlib import Path
import fcntl
import json

from ai_verified_stats import atomic_json, publication_evidence_error
from ai_prediction_guard import read_stats
from stats_history import (TZ, business_start, load_snapshot, parse_period, parse_time,
                           period_slot, valid_draw_record, validate_conflicting_periods)

ROOT = Path('/root/penta5-runner')
REVISION = 'he3-five-stats-v1'


def _current(now):
    if not isinstance(now, datetime):
        raise ValueError('invalid-statistics-time')
    return now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)


def _publication(seal, reference, now, root, parent_seals):
    from he3_refine import _validate_saved
    if not isinstance(seal, dict) or not isinstance(reference, str) or not parse_period(reference):
        raise ValueError('invalid-refinement-publication')
    checked = _validate_saved(seal, reference, now, root=Path(root), published=True,
                              parent_seals=parent_seals)
    if checked.get('ref_period') != reference:
        raise ValueError('invalid-refinement-reference')
    return deepcopy(checked)


def _verified_entry(proof, record, now):
    if not isinstance(record, dict):
        raise ValueError('invalid-actual-draw')
    actual = valid_draw_record(record.get('period'), record.get('numbers'), record.get('opentime'), now)
    if actual is None or actual['period'] != proof['target_period']:
        raise ValueError('invalid-or-nonexact-actual-draw')
    error = publication_evidence_error(proof, actual)
    if error:
        raise ValueError(error)
    keep = proof['response']['he3_keep5']
    if (not isinstance(keep, str) or len(keep) != 5 or len(set(keep)) != 5
            or any(char not in '0123456789' for char in keep)):
        raise ValueError('invalid-five-tail-selection')
    digits = actual['numbers']
    tails = {sum(digits[i] for i in indexes) % 10 for indexes in combinations(range(4), 3)}
    return {'ref_period': proof['ref_period'], 'target_period': proof['target_period'],
            'publication': deepcopy(proof), 'actual': actual,
            'biz_date': business_start(parse_time(actual['opentime'])).strftime('%Y-%m-%d'),
            'hit': bool(tails.intersection(map(int, keep)))}


def _restore(previous, now, root, parent_seals):
    result = {'version': 1, 'revision': REVISION, 'updated_at': now.isoformat(),
              'verified': {}, 'pending': {}, 'conflicts': [], 'rejected': []}
    if previous is None or previous == {}:
        return result
    if (not isinstance(previous, dict) or type(previous.get('version')) is not int
            or previous['version'] != 1 or previous.get('revision') != REVISION
            or not isinstance(previous.get('verified'), dict)
            or not isinstance(previous.get('pending'), dict)):
        raise ValueError('invalid-five-tail-ledger')
    result['conflicts'] = sorted(validate_conflicting_periods(previous.get('conflicts', [])))
    for target, entry in previous['verified'].items():
        if not isinstance(entry, dict) or not isinstance(entry.get('publication'), dict):
            raise ValueError('corrupt-five-tail-evidence')
        proof = _publication(entry['publication'], entry.get('ref_period'), now, root, parent_seals)
        verified = _verified_entry(proof, entry.get('actual'), now)
        if (target != verified['target_period'] or type(entry.get('hit')) is not bool
                or entry != verified):
            raise ValueError('corrupt-five-tail-history-refusing-overwrite')
        result['verified'][target] = deepcopy(entry)
    for target, seal in previous['pending'].items():
        if not isinstance(seal, dict):
            raise ValueError('corrupt-five-tail-pending')
        proof = _publication(seal, seal.get('ref_period'), now, root, parent_seals)
        if target != proof['target_period'] or target in result['verified']:
            raise ValueError('corrupt-five-tail-pending-identity')
        result['pending'][target] = proof
    return result


def refresh_stats(seals, records, now, previous=None, *, root=ROOT, conflicting_periods=(),
                  _parent_seals=None):
    """Settle genuine pre-draw publications exactly once against actual first four."""
    now = _current(now)
    parent_seals = (_parent_seals if _parent_seals is not None else
                    read_stats(Path(root) / 'data/r1_framework_seals.json'))
    ledger = _restore(previous, now, root, parent_seals)
    if (not isinstance(seals, dict) or not isinstance(seals.get('prediction_seals', {}), dict)
            or not isinstance(records, (list, tuple))):
        raise ValueError('invalid-five-tail-statistics-input')
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
            proof = _publication(seal, reference, now, root, parent_seals)
            target = proof['target_period']
            stored = ledger['verified'].get(target)
            existing = stored['publication'] if stored else ledger['pending'].get(target)
            if existing is not None and existing != proof:
                conflicts.add(target)
                raise ValueError('conflicting-five-tail-publication')
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


def make_summary(stats, now, *, root=ROOT, _parent_seals=None, _records=(), _conflicting_periods=()):
    now = _current(now)
    parent_seals = (_parent_seals if _parent_seals is not None else
                    read_stats(Path(root) / 'data/r1_framework_seals.json'))
    ledger = _restore(stats, now, root, parent_seals)
    from he3_comparison_stats import build_comparison
    comparison = build_comparison(ledger, now, _records, _conflicting_periods)
    day = business_start(now).strftime('%Y-%m-%d')
    conflicts = set(ledger['conflicts'])
    entries = sorted((entry for target, entry in ledger['verified'].items()
                      if entry['biz_date'] == day and target not in conflicts),
                     key=lambda entry: (parse_time(entry['actual']['opentime']), entry['target_period']))
    hits, total = sum(entry['hit'] for entry in entries), len(entries)
    def today(target):
        return business_start(period_slot(target)).strftime('%Y-%m-%d') == day
    pending = [target for target in ledger['pending'] if today(target) and target not in conflicts]
    return {'biz_date': day, 'hits': hits, 'total': total,
            'percent': round(hits / total * 100, 4) if total else None,
            'latest_settled_period': entries[-1]['target_period'] if entries else None,
            'pending_count': len(pending),
            'overdue_count': sum(period_slot(target) <= now for target in pending),
            'conflict_count': sum(today(target) for target in conflicts), 'stats_error': False,
            'comparison': comparison}


def _read(path):
    try:
        result = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(result, dict):
        raise ValueError('invalid-five-tail-statistics-file')
    return result


def refresh_he3_stats(snapshot=None, root=ROOT):
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(
        state_path=root / 'data/state.json', cache_path=root / 'data/stats_draw_history.json', persist=False)
    now = _current(snapshot['now'])
    path = root / 'data/he3_refine_hit_stats.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = _read(path)
            # Scope this snapshot to one refresh only. Every child still runs
            # the full parent/hash/history/archive/settlement validation.
            parent_seals = read_stats(root / 'data/r1_framework_seals.json')
            ledger = refresh_stats(_read(root / 'data/he3_refine_seals.json'), snapshot['records'],
                                   now, previous, root=root,
                                   conflicting_periods=snapshot.get('conflicting_periods', ()),
                                   _parent_seals=parent_seals)
            summary = make_summary(ledger, now, root=root, _parent_seals=parent_seals,
                                   _records=snapshot['records'],
                                   _conflicting_periods=snapshot.get('conflicting_periods', ()))
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            # Preserve the last genuine ledger; do not reset a broken proof to zero.
            return {'biz_date': business_start(now).strftime('%Y-%m-%d'), 'hits': 0, 'total': 0,
                    'percent': None, 'latest_settled_period': None, 'pending_count': 0,
                    'overdue_count': 0, 'conflict_count': 0, 'stats_error': True}
        if ledger != previous:
            atomic_json(path, ledger)
        atomic_json(root / 'data/he3_refine_stats.json', summary)
        return summary
