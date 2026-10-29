"""Verified five-tail accounting; never changes a published prediction."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from itertools import combinations
from pathlib import Path
import fcntl
import json
import os

from ai_verified_stats import atomic_json, publication_evidence_error
from ai_prediction_guard import read_stats
from stats_history import (TZ, business_start, load_snapshot, parse_period, parse_time,
                           period_slot, valid_draw_record, validate_conflicting_periods)

ROOT = Path('/root/penta5-runner')
REVISION = 'he3-five-stats-v1'

CACHE_VERSION = 1
CACHE_ENV = 'PENTA5_HE3_STATS_CACHE'
# Any of these modules can change what a refresh validates or renders. Kept
# explicit instead of a package-wide mtime so an unrelated deploy cannot leave
# a stale summary behind, while unrelated files never force a recomputation.
CODE_FILES = ('he3_refine_stats.py', 'he3_refine_view.py', 'he3_refine.py',
              'stat50_compare.py', 'framework_hit_stats.py', 'ai_prediction_guard.py',
              'ai_verified_stats.py', 'stats_history.py', 'he3_comparison_stats.py',
              'ai_model_context.py', 'strategy_features.py', 'position_input_features.py')


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


def _stat_signature(path):
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return [info.st_mtime_ns, info.st_size]


def _input_signatures(root):
    return {'seals': _stat_signature(root / 'data/he3_refine_seals.json'),
            'ledger': _stat_signature(root / 'data/he3_refine_hit_stats.json'),
            'parent_seals': _stat_signature(root / 'data/r1_framework_seals.json')}


def _code_signature():
    base = Path(__file__).resolve().parent
    return {name: _stat_signature(base / name) for name in CODE_FILES}


def _digest_value(value):
    return sha256(json.dumps(value, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def _record_periods(records):
    periods = set()
    for row in records:
        if isinstance(row, dict):
            period = row.get('period')
            if isinstance(period, str) and period:
                periods.add(period)
    return periods


def _record_fingerprint(records, boundary):
    """Fingerprint the draw snapshot, bounded at the last already-settled period.

    Settlement can only change a pending target and the comparison anchor always
    sits at the newest real draw, so records up to ``boundary`` are immutable
    history while the small tail holds anything newer. Splitting the hash keeps
    the historical part stable across new draws (the selected_six pattern); the
    tail keeps the total fingerprint exact so a new draw -- which moves the
    anchor and may settle a pending publication -- always misses the cache.
    """
    historical, tail = [], []
    for row in records:
        period = str(row.get('period') or '') if isinstance(row, dict) else ''
        if boundary is not None and period and period <= boundary:
            historical.append(row)
        else:
            tail.append(row)
    periods = [str(row.get('period')) for row in records
               if isinstance(row, dict) and row.get('period')]
    return {'boundary': boundary, 'historical': _digest_value(historical),
            'tail': _digest_value(tail), 'count': len(records),
            'max_period': max(periods, default=None)}


def _evidence_times(seals, parent_seals, records):
    """Newest input timestamp that any validation compares against ``now``.

    Every such check rejects evidence dated after ``now``. When all input
    timestamps are already in the past, moving the wall clock forward cannot
    change a validation outcome, so a cached summary stays exact.
    """
    latest = None

    def consider(value):
        nonlocal latest
        moment = value if isinstance(value, datetime) else parse_time(value)
        if moment is not None and (latest is None or moment > latest):
            latest = moment

    if isinstance(seals, dict):
        for seal in seals.get('prediction_seals', {}).values():
            if not isinstance(seal, dict):
                continue
            consider(seal.get('generated_at'))
            history = seal.get('history')
            if isinstance(history, (list, tuple)):
                for row in history:
                    if isinstance(row, dict):
                        consider(row.get('opentime'))
    if isinstance(parent_seals, dict):
        for seal in parent_seals.get('prediction_seals', {}).values():
            if isinstance(seal, dict):
                consider(seal.get('published_at'))
    for row in records:
        if isinstance(row, dict):
            consider(row.get('opentime'))
    return latest


def _write_summary_stamp(stamp_path, summary_path, snapshot, now, root, seals,
                         parent_seals, ledger, summary):
    """Persist the input fingerprint for the summary that was just written."""
    try:
        records = snapshot.get('records') or ()
        boundary = None
        for target in ledger.get('verified', {}):
            text = str(target)
            if boundary is None or text > boundary:
                boundary = text
        latest = _evidence_times(seals, parent_seals, records)
        if latest is not None and latest > now:
            # Future-dated evidence is rejected now but would flip valid once
            # the clock passes it: never cache through that boundary.
            return
        conflicts = set(ledger.get('conflicts', ()))
        day = business_start(now).strftime('%Y-%m-%d')
        pending_targets = sorted(str(target) for target in ledger.get('pending', {}))
        pending_slots = []
        for target in ledger.get('pending', {}):
            slot = period_slot(target)
            if slot is None:
                return
            if target not in conflicts and business_start(slot).strftime('%Y-%m-%d') == day:
                pending_slots.append(slot.isoformat())
        archives = {}
        for seal in seals.get('prediction_seals', {}).values():
            if not isinstance(seal, dict):
                continue
            audit = seal.get('model_context_audit')
            relative = audit.get('input_archive') if isinstance(audit, dict) else None
            if not isinstance(relative, str) or relative in archives:
                continue
            archives[relative] = _stat_signature(root / 'data' / relative)
        encoded = summary_path.read_bytes()
        stamp = {'version': CACHE_VERSION, 'revision': REVISION, 'biz_date': day,
                 'inputs': _input_signatures(root), 'code': _code_signature(),
                 'records': _record_fingerprint(records, boundary),
                 'conflicting': _digest_value(snapshot.get('conflicting_periods', ())),
                 'pending_targets': pending_targets, 'pending_slots': pending_slots,
                 'pending_count': len(pending_slots),
                 'overdue_count': summary.get('overdue_count'),
                 'latest_settled_period': summary.get('latest_settled_period'),
                 'archives': archives, 'summary_sha256': sha256(encoded).hexdigest()}
        atomic_json(stamp_path, stamp)
    except (OSError, ValueError, TypeError, KeyError):
        # A cache that cannot describe this run must not block the refresh.
        return


def _cached_summary(stamp_path, summary_path, snapshot, now, root):
    """Return the stored summary only when every input that shapes it is unchanged.

    Deliberately conservative: a missing/corrupt stamp, any changed stat or hash
    of the seal/ledger/parent/archive inputs, a changed code mtime, a pending
    publication that now has an actual draw, a clock-dependent output field that
    moved, or an unreadable summary all fall back to the full, fully validated
    recomputation.
    """
    try:
        stamp = json.loads(stamp_path.read_text(encoding='utf-8'))
        if not isinstance(stamp, dict) or stamp.get('version') != CACHE_VERSION:
            return None
        if stamp.get('revision') != REVISION:
            return None
        if stamp.get('biz_date') != business_start(now).strftime('%Y-%m-%d'):
            return None
        if stamp.get('inputs') != _input_signatures(root):
            return None
        if stamp.get('code') != _code_signature():
            return None
        if stamp.get('conflicting') != _digest_value(snapshot.get('conflicting_periods', ())):
            return None
        records = stamp.get('records')
        if not isinstance(records, dict):
            return None
        if _record_fingerprint(snapshot.get('records') or (), records.get('boundary')) != records:
            return None
        pending = stamp.get('pending_targets')
        slots = stamp.get('pending_slots')
        if (not isinstance(pending, list) or not isinstance(slots, list)
                or len(pending) < len(slots)):
            return None
        if set(pending).intersection(_record_periods(snapshot.get('records') or ())):
            # A draw now exists for a still-pending publication: the next full
            # run would settle it, so the stored summary is already stale.
            return None
        overdue = 0
        for slot in slots:
            moment = parse_time(slot)
            if moment is not None and moment <= now:
                overdue += 1
        if overdue != stamp.get('overdue_count'):
            return None
        archives = stamp.get('archives')
        if not isinstance(archives, dict):
            return None
        for relative, signature in archives.items():
            if _stat_signature(root / 'data' / relative) != signature:
                return None
        encoded = summary_path.read_bytes()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if sha256(encoded).hexdigest() != stamp.get('summary_sha256'):
        return None
    try:
        summary = json.loads(encoded.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    if (not isinstance(summary, dict) or summary.get('stats_error') is not False
            or summary.get('biz_date') != stamp.get('biz_date')
            or summary.get('pending_count') != stamp.get('pending_count')
            or summary.get('overdue_count') != stamp.get('overdue_count')
            or summary.get('latest_settled_period') != stamp.get('latest_settled_period')):
        return None
    return summary




def refresh_he3_stats(snapshot=None, root=ROOT):
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(
        state_path=root / 'data/state.json', cache_path=root / 'data/stats_draw_history.json', persist=False)
    now = _current(snapshot['now'])
    path = root / 'data/he3_refine_hit_stats.json'
    summary_path = root / 'data/he3_refine_stats.json'
    stamp_path = root / 'data/he3_refresh.stamp.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # --- 2026-10-30 performance: reuse the previous summary while no input
    # changed. One refresh re-reads a 61MB seal ledger and a 61MB proof ledger,
    # opens the 2300 archived model inputs and revalidates every historical
    # publication twice (~45-65s per cycle, PENTA5_STATS_TIMING stage
    # he3_extra). A cache hit only returns the stored summary; every miss still
    # runs the full parent/hash/history/archive/settlement validation.
    # Disable with PENTA5_HE3_STATS_CACHE=0.
    if os.environ.get(CACHE_ENV, '1') != '0':
        cached = _cached_summary(stamp_path, summary_path, snapshot, now, root)
        if cached is not None:
            return cached
    with Path(str(path) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = _read(path)
            seals = _read(root / 'data/he3_refine_seals.json')
            # Scope this snapshot to one refresh only. Every child still runs
            # the full parent/hash/history/archive/settlement validation.
            parent_seals = read_stats(root / 'data/r1_framework_seals.json')
            ledger = refresh_stats(seals, snapshot['records'], now, previous, root=root,
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
        atomic_json(summary_path, summary)
        # Write the stamp last: a concurrent reader must never see it matching a
        # half-updated ledger/summary pair.
        _write_summary_stamp(stamp_path, summary_path, snapshot, now, root, seals,
                             parent_seals, ledger, summary)
        return summary
