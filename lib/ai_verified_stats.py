"""Exact-target settlement with durable publication evidence and pending queues."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import json
import os
import re
import tempfile

from stats_history import (beijing_now, business_start, parse_period, parse_time,
                           period_slot, valid_draw_record)

KILLER_METRIC_KEYS = ('k1', 'k2', 'k3', 'k4', 'he_all', 'he_q3', 'he_h3', 'pat1', 'pat2')
_KILLER_PROOF_FIELDS = ('ref_period', 'target_period', 'evidence_version', 'publication_state',
                        'reference_open_time', 'generated_at', 'published_at', 'kills',
                        'he_all', 'he_q3', 'he_h3', 'p1', 'p2')


class LedgerIntegrityError(ValueError):
    """Stored killer evidence is incomplete or disagrees with its derived indexes."""


def _integrity_error(reason):
    raise LedgerIntegrityError('Killer ledger integrity error: ' + reason)


def _proof_fields(value):
    return {key: deepcopy(value.get(key)) for key in _KILLER_PROOF_FIELDS}


def validate_killer_ledger(stats, now=None):
    """Revalidate v2 evidence and its bookkeeping without repairing any raw data.

    Old settlements resolve their original proof from prediction_seals. New
    settlements also retain a compact exact copy so pure pending-only callers
    remain verifiable after the pending queue has been consumed.
    """
    now = now or beijing_now()
    if not isinstance(stats, dict) or type(stats.get('verified_stats_version')) is not int or stats.get('verified_stats_version') != 2:
        _integrity_error('unsupported verified version')
    rows = stats.get('verified_evaluations', [])
    seals = stats.get('prediction_seals', {})
    daily = stats.get('verified_daily', {})
    if not isinstance(rows, list) or not isinstance(seals, dict) or not isinstance(daily, dict):
        _integrity_error('invalid verified containers')
    derived, verified, targets = {}, [], set()
    for row in rows:
        if not isinstance(row, dict):
            _integrity_error('invalid verified row')
        target, reference = row.get('target_period'), row.get('ref_period')
        parsed = parse_period(target)
        if not parsed or target != parsed[0] or target in targets:
            _integrity_error('invalid or duplicate verified target')
        actual = valid_draw_record(target, row.get('actual'), row.get('actual_open_time'), now)
        if actual is None:
            _integrity_error('invalid verified actual')
        embedded, mapped = row.get('publication'), seals.get(str(reference))
        if embedded is not None and not isinstance(embedded, dict):
            _integrity_error('invalid embedded publication')
        if mapped is not None and not isinstance(mapped, dict):
            _integrity_error('invalid mapped publication')
        if embedded is not None and mapped is not None and _proof_fields(embedded) != _proof_fields(mapped):
            _integrity_error('conflicting publication copies')
        proof = mapped if mapped is not None else embedded
        if (not isinstance(proof, dict) or proof.get('ref_period') != reference
                or type(proof.get('evidence_version')) is not int
                or publication_evidence_error(proof, actual)):
            _integrity_error('missing or invalid original publication')
        expected = prediction_outcome(proof, actual['numbers'])
        outcome = row.get('outcome')
        if (expected is None or not isinstance(outcome, dict) or set(outcome) != set(KILLER_METRIC_KEYS)
                or any(type(outcome.get(key)) is not bool for key in KILLER_METRIC_KEYS)
                or outcome != expected):
            _integrity_error('verified outcome disagrees with original prediction')
        day = business_start(parse_time(actual['opentime'])).strftime('%Y-%m-%d')
        if row.get('biz_date') != day or any(row.get(key) != proof.get(key) for key in ('generated_at', 'published_at')):
            _integrity_error('verified day or publication times disagree')
        bucket = derived.setdefault(day, {'totals': {}, 'hits': {}})
        for key, success in expected.items():
            bucket['totals'][key] = bucket['totals'].get(key, 0) + 1
            bucket['hits'][key] = bucket['hits'].get(key, 0) + int(success)
        targets.add(target)
        verified.append({'entry': row, 'actual': actual, 'outcome': expected})
    for day in set(daily) | set(derived):
        try:
            if date.fromisoformat(day).isoformat() != day:
                _integrity_error('invalid daily identity')
        except (TypeError, ValueError):
            _integrity_error('invalid daily identity')
        bucket = daily.get(day)
        if (not isinstance(bucket, dict) or not isinstance(bucket.get('totals'), dict)
                or not isinstance(bucket.get('hits'), dict)):
            _integrity_error('missing daily bucket')
        expected = derived.get(day, {'totals': {}, 'hits': {}})
        for field in ('totals', 'hits'):
            if set(bucket[field]) - set(KILLER_METRIC_KEYS):
                _integrity_error('unknown daily metric')
            for key in KILLER_METRIC_KEYS:
                value = bucket[field].get(key, 0)
                if type(value) is not int or value < 0 or value != expected[field].get(key, 0):
                    _integrity_error('daily aggregate disagrees with verified evidence')
    for field in ('verified_totals', 'verified_hits'):
        if field in stats:
            bucket = daily.get(stats.get('biz_date'))
            if not isinstance(bucket, dict) or stats[field] != bucket['totals' if field == 'verified_totals' else 'hits']:
                _integrity_error('active daily alias disagrees')
    verified_ids = stats.get('verified_settled_periods', [])
    processed = stats.get('processed_target_periods', [])
    if not isinstance(verified_ids, list) or not isinstance(processed, list):
        _integrity_error('invalid processed indexes')
    if (any(not isinstance(target, str) for target in verified_ids + processed)
            or len(verified_ids) != len(set(verified_ids)) or len(processed) != len(set(processed))
            or set(verified_ids) != targets):
        _integrity_error('missing or duplicate verified index')
    unverified = stats.get('unverified_evaluations', [])
    if not isinstance(unverified, list):
        _integrity_error('invalid unverified history')
    rejected_targets = set()
    for row in unverified:
        target = row.get('target_period') if isinstance(row, dict) else None
        parsed = parse_period(target)
        if not parsed or parsed[0] != target or target in rejected_targets or target in targets:
            _integrity_error('invalid or duplicate unverified target')
        rejected_targets.add(target)
    legacy = stats.get('legacy_verified_aggregates', [])
    if not isinstance(legacy, list):
        _integrity_error('invalid legacy archive')
    legacy_targets = set()
    for archive in legacy:
        if not isinstance(archive, dict) or not isinstance(archive.get('settled_periods', []), list) or not isinstance(archive.get('evaluations', []), list):
            _integrity_error('invalid legacy archive')
        for target in archive.get('settled_periods', []):
            if isinstance(target, str):
                legacy_targets.add(target)
        for row in archive.get('evaluations', []):
            if isinstance(row, dict) and isinstance(row.get('target_period'), str):
                legacy_targets.add(row['target_period'])
    if (not (targets | rejected_targets) <= set(processed)
            or set(processed) - targets - rejected_targets - legacy_targets):
        _integrity_error('processed indexes disagree with retained evidence')
    return {'daily': derived, 'verified': verified}


def next_period(period):
    parsed = parse_period(period)
    if not parsed:
        return None
    text, day, index = parsed
    return text[:8] + f'{index + 1:03}' if index < 288 else (day + timedelta(days=1)).strftime('%Y%m%d') + '001'


def digit(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value <= 9:
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]', value):
        return int(value)
    return None


def pending_items(stats):
    """Deduplicate legacy pointer + queue without changing prediction contents."""
    queue = stats.get('pending_evals', [])
    if not isinstance(queue, list):
        raise ValueError('pending_evals must be a list')
    items = list(queue)
    pointer = stats.get('pending_eval')
    if isinstance(pointer, dict):
        items.append(pointer)
    unique = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Pending prediction must be an object')
        key = str(item.get('target_period') or next_period(item.get('ref_period')) or '')
        if not key:
            key = 'legacy:' + json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in unique:
            unique[key] = item
    return list(unique.values())


def select_business_day(stats, now=None):
    """Archive old aggregates intact; switching days never clears pending work."""
    day = business_start(now or beijing_now()).strftime('%Y-%m-%d')
    if stats.get('verified_stats_version') != 2:
        version = stats.get('verified_stats_version')
        if version is not None and (type(version) is not int or version != 1):
            _integrity_error('unsupported verified version')
        seals = stats.get('prediction_seals', {})
        rows = stats.get('verified_evaluations', [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            embedded = row.get('publication')
            mapped = seals.get(str(row.get('ref_period'))) if isinstance(seals, dict) else None
            if ((isinstance(embedded, dict) and embedded.get('evidence_version') == 2)
                    or (row.get('generated_at') and row.get('published_at')
                        and isinstance(mapped, dict) and mapped.get('evidence_version') == 2)):
                _integrity_error('missing verified version for established v2 evidence')
        # Historical totals lack publication evidence. Preserve their complete
        # original ledger separately, never mix them into the new verified UI.
        original = {'biz_date': stats.get('biz_date'),
                    'totals': deepcopy(stats.get('verified_totals', {})),
                    'hits': deepcopy(stats.get('verified_hits', {})),
                    'evaluations': deepcopy(stats.get('verified_evaluations', [])),
                    'settled_periods': deepcopy(stats.get('verified_settled_periods', [])),
                    'daily_before_migration': deepcopy(stats.get('verified_daily', {})),
                    'evidence_status': 'legacy-unverified-publication-time'}
        if any(original[key] for key in ('totals', 'hits', 'evaluations', 'settled_periods', 'daily_before_migration')):
            stats.setdefault('legacy_verified_aggregates', []).append(original)
        processed = set(stats.get('processed_target_periods', []))
        processed.update(stats.get('verified_settled_periods', []))
        stats['processed_target_periods'] = sorted(processed)
        stats['verified_daily'] = {}
        stats['verified_evaluations'] = []
        stats['verified_settled_periods'] = []
        stats['verified_stats_version'] = 2
    daily = stats.setdefault('verified_daily', {})
    bucket = daily.setdefault(day, {'totals': {}, 'hits': {}})
    stats['biz_date'] = day
    stats['verified_totals'] = bucket['totals']
    stats['verified_hits'] = bucket['hits']
    stats['pending_evals'] = pending_items(stats)
    return day


def publication_evidence_error(pending, record):
    """Legacy timestamps are never fabricated or inferred from a draw."""
    reference = pending.get('ref_period')
    target = pending.get('target_period')
    if not target or target != next_period(reference) or target != record['period']:
        return 'invalid-target-transition'
    generated = parse_time(pending.get('generated_at'))
    published = parse_time(pending.get('published_at'))
    reference_time = parse_time(pending.get('reference_open_time'))
    cutoff = period_slot(target)
    actual_time = parse_time(record['opentime'])
    if pending.get('evidence_version') != 2 or not generated or not published or not reference_time:
        return 'legacy-unverified-publication-time'
    reference_slot = period_slot(reference)
    if (not reference_slot <= reference_time < reference_slot + timedelta(minutes=1)
            or not reference_time <= generated <= published < cutoff
            or not published < actual_time
            or pending.get('publication_state') != 'published'):
        return 'late-or-invalid-publication-evidence'
    return None


def prediction_outcome(pending, actual):
    outcome = {}
    kills_by_position = pending.get('kills')
    if not isinstance(kills_by_position, dict):
        return None
    for index in range(4):
        name = f'k{index + 1}'
        kills = kills_by_position.get(name)
        if not isinstance(kills, list) or len(kills) != 1 or digit(kills[0]) is None:
            return None
        outcome[name] = actual[index] != digit(kills[0])
    for name, value in [('he_all', sum(actual) % 10), ('he_q3', sum(actual[:3]) % 10), ('he_h3', sum(actual[1:]) % 10)]:
        prediction = digit(pending.get(name))
        if prediction is None:
            return None
        outcome[name] = value != prediction
    patterns = {''.join('单' if d % 2 else '双' for d in actual),
                ''.join('奇' if d % 2 else '偶' for d in actual),
                ''.join('大' if d >= 5 else '小' for d in actual)}
    if str(pending.get('p1')).replace('奇', '单').replace('偶', '双') == str(pending.get('p2')).replace('奇', '单').replace('偶', '双'):
        return None
    for name, key in [('pat1', 'p1'), ('pat2', 'p2')]:
        pattern = pending.get(key)
        if not isinstance(pattern, str) or not re.fullmatch(r'(?:[单双]{4}|[奇偶]{4}|[大小]{4})', pattern):
            return None
        outcome[name] = pattern not in patterns
    return outcome


def settle_pending(stats, records, now=None):
    """Settle every available exact target once; retain missing targets forever."""
    now = now or beijing_now()
    if stats.get('verified_stats_version') == 2:
        validate_killer_ledger(stats, now)
    current_day = select_business_day(stats, now)
    available = {}
    conflicts = set()
    for raw in records:
        if not isinstance(raw, dict):
            continue
        record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if record is None:
            continue
        key = record['period']
        if key in available and available[key]['numbers'] != record['numbers']:
            conflicts.add(key)
        available[key] = record
    processed = set(stats.get('processed_target_periods', []))
    processed.update(stats.get('verified_settled_periods', []))
    remaining = []
    changed = False
    for pending in pending_items(stats):
        target = str(pending.get('target_period') or next_period(pending.get('ref_period')) or '')
        if target in processed:
            changed = True
            continue
        record = available.get(target)
        if not record or target in conflicts:
            remaining.append(pending)
            continue
        error = publication_evidence_error(pending, record)
        outcome = prediction_outcome(pending, record['numbers'])
        if outcome is None:
            error = error or 'invalid-prediction-schema'
        opened = parse_time(record['opentime'])
        day = business_start(opened).strftime('%Y-%m-%d')
        entry = {'ref_period': pending.get('ref_period'), 'target_period': target,
                 'actual': record['numbers'], 'actual_open_time': record['opentime'],
                 'biz_date': day, 'generated_at': pending.get('generated_at'),
                 'published_at': pending.get('published_at')}
        if error:
            entry['reason'] = error
            entry['prediction'] = deepcopy(pending)
            stats.setdefault('unverified_evaluations', []).append(entry)
        else:
            entry['outcome'] = outcome
            entry['publication'] = _proof_fields(pending)
            bucket = stats['verified_daily'].setdefault(day, {'totals': {}, 'hits': {}})
            for name, hit in outcome.items():
                bucket['totals'][name] = bucket['totals'].get(name, 0) + 1
                bucket['hits'][name] = bucket['hits'].get(name, 0) + int(hit)
            stats.setdefault('verified_evaluations', []).append(entry)
            stats.setdefault('verified_settled_periods', []).append(target)
        processed.add(target)
        changed = True
    stats['processed_target_periods'] = sorted(processed)
    stats['pending_evals'] = remaining
    pointer = stats.get('pending_eval')
    if isinstance(pointer, dict) and not any(str(item.get('target_period') or next_period(item.get('ref_period')) or '') ==
                           str(pointer.get('target_period') or next_period(pointer.get('ref_period')) or '') for item in remaining):
        stats['pending_eval'] = None
    bucket = stats['verified_daily'][current_day]
    stats['verified_totals'], stats['verified_hits'] = bucket['totals'], bucket['hits']
    validate_killer_ledger(stats, now)
    return changed


def sync_parent_directory(path):
    """Commit the destination entry, including retries after a failed fsync."""
    directory = os.open(Path(path).parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = file.name
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        # fsync(file) does not commit the replacement directory entry. A
        # successful seal write must survive loss of the host, not only exit.
        sync_parent_directory(path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
