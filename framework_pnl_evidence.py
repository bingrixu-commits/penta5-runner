"""Read-only evidence events for a hypothetical first-four financial replay.

No event represents a real bet.  A complete publication is checked once for a
given validator fingerprint; subsequent refreshes can reuse its immutable
embedded evidence.  Missing outcomes block the chronological ladder rather
than letting later results determine an earlier stake.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path

from ai_verified_stats import next_period, publication_evidence_error
from framework_coverage import build_coverage
from stats_history import (TZ, business_start, parse_period, parse_time, period_slot,
                           valid_draw_record, validate_conflicting_periods)


_MODELS = {
    'selected': ('selected_framework_model', 'selected_framework_contract',
                 'selected-seven-v6-three-positions', 'selected-six-stats-v1'),
    'six': ('selected_six_model', 'selected_six_contract',
            'selected-six-independent-v2-minimax', 'selected-six-independent-stats-v1'),
}
_DEPENDENCIES = (
    'framework_pnl_evidence.py', 'framework_coverage.py', 'stats_history.py',
    'selected_framework_model.py', 'selected_framework_contract.py',
    'selected_request_options.py', 'selected_strategy_validation.py', 'ai_strategy_validation.py',
    'selected_six_request_options.py',
    'selected_framework_view.py', 'selected_framework_attack.py',
    'selected_six_model.py', 'selected_six_contract.py', 'selected_six_view.py',
    'selected_single_view.py', 'strategy_features.py', 'position_input_features.py',
    'stat50_compare.py', 'ai_prediction_guard.py', 'ai_verified_stats.py',
    'ai_model_context.py', 'killer_ai_client.py', 'framework_ai_client.py',
    'scripts/run_ai_ready_job.py',
)
_STATUSES = frozenset(('settled', 'pending', 'skip', 'conflict', 'blocked'))


def _packed(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('invalid-pnl-evidence-json') from error


def _digest(value):
    return sha256(_packed(value)).hexdigest()


def validation_fingerprint():
    """Read validator source files only; never read credentials or live data."""
    directory = Path(__file__).resolve().parent
    hashes = {name: sha256((directory / name).read_bytes()).hexdigest()
              for name in _DEPENDENCIES}
    return _digest({'version': 'pnl-publication-validation-v1', 'files': hashes})


def _aware(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(TZ)


def _period_at(slot):
    midnight = slot.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = int((slot - midnight).total_seconds() // 60)
    if minutes == 0:
        return (midnight - timedelta(days=1)).strftime('%Y%m%d') + '288'
    return midnight.strftime('%Y%m%d') + f'{minutes // 5:03d}'


def _reference(target):
    return _period_at(period_slot(target) - timedelta(minutes=5))


def _validate_native(model, proof, reference, now):
    return import_module(_MODELS[model][0]).validate_publication(
        proof, reference, now, published=True)


def _containers(model, snapshot, source, hit, previous):
    if model not in _MODELS:
        raise ValueError('invalid-pnl-evidence-model')
    if (not isinstance(snapshot, dict) or _aware(snapshot.get('now')) is None
            or not isinstance(snapshot.get('records'), (list, tuple))):
        raise ValueError('invalid-pnl-evidence-snapshot')
    if not isinstance(source, dict) or not isinstance(source.get('prediction_seals'), dict):
        raise ValueError('invalid-pnl-evidence-source-ledger')
    if hit is not None and (not isinstance(hit, dict)
            or type(hit.get('version')) is not int or hit['version'] != 1
            or hit.get('revision') != _MODELS[model][3]
            or not isinstance(hit.get('verified'), dict)
            or not isinstance(hit.get('pending'), dict)):
        raise ValueError('invalid-pnl-evidence-hit-ledger')
    if not isinstance(previous, (list, tuple)):
        raise ValueError('invalid-pnl-evidence-previous-events')
    prior = {}
    for event in previous:
        target = event.get('target_period') if isinstance(event, dict) else None
        parsed = parse_period(target)
        if (not isinstance(target, str) or not parsed or parsed[0] != target
                or event.get('status') not in _STATUSES or target in prior):
            raise ValueError('invalid-pnl-evidence-previous-event')
        prior[target] = event
    return prior


def _actuals(snapshot, now):
    available, conflicts = {}, validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    for raw in snapshot['records']:
        if not isinstance(raw, dict):
            continue
        actual = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if actual is None:
            continue
        target = actual['period']
        if target in available and available[target] != actual:
            conflicts.add(target)
        available[target] = actual
    return available, conflicts


def _checked_proof(model, proof, reference, target, now, prior, fingerprint):
    if (not isinstance(proof, dict) or proof.get('revision') != _MODELS[model][2]
            or proof.get('ref_period') != reference or proof.get('target_period') != target):
        raise ValueError('wrong-current-publication-identity')
    published = _aware(proof.get('published_at'))
    # A snapshot is frozen.  A newer publication can be considered next pass,
    # without declaring an already saved proof invalid because the clock lags.
    if published is not None and published > now:
        return None
    proof_hash = _digest(proof)
    token = _digest({'validator': fingerprint, 'model': model, 'proof_sha256': proof_hash})
    if (prior is None or prior.get('validation_token') != token
            or prior.get('proof_sha256') != proof_hash
            or prior.get('publication') != proof):
        _validate_native(model, proof, reference, now)
    if (published is None or published >= period_slot(target) - timedelta(seconds=70)
            or published > now):
        raise ValueError('publication-not-before-platform-close')
    coverage = build_coverage(model, proof['response'], proof['revision'])
    return {'publication': deepcopy(proof), 'published_at': proof['published_at'],
            'proof_sha256': proof_hash, 'validation_token': token,
            'coverage_sha256': coverage.coverage_sha256,
            'number_count': coverage.number_count}, coverage


def build_events(model, snapshot, source_ledger, hit_ledger=None, previous_events=(), *, biz_date=None):
    """Return ordered current-day events without writing or making requests.

    Published predictions are included only for the current formal revision.
    Zero prediction with a known actual is a skipped round.  An unknown actual
    or a conflict stops later stake assignment until the evidence is resolved.
    """
    prior = _containers(model, snapshot, source_ledger, hit_ledger, previous_events)
    now = _aware(snapshot['now'])
    start = business_start(now)
    if biz_date is not None:
        try:
            requested = datetime.strptime(biz_date, '%Y-%m-%d').replace(tzinfo=TZ, hour=5)
        except (TypeError, ValueError) as error:
            raise ValueError('invalid-pnl-evidence-business-date') from error
        if requested.strftime('%Y-%m-%d') != biz_date or requested > start:
            raise ValueError('invalid-pnl-evidence-business-date')
        start = requested
    end = start + timedelta(days=1)
    revision = _MODELS[model][2]
    actuals, conflicts = _actuals(snapshot, now)
    hit = hit_ledger or {'verified': {}, 'pending': {}, 'conflicts': []}
    conflicts |= validate_conflicting_periods(hit.get('conflicts', ()))
    fingerprint = validation_fingerprint()

    # Include only the finite current-day target interval evidenced by an
    # actual, a conflict already due, or a publication visible in this snapshot.
    candidates = set(actuals) | set(conflicts)
    for reference, proof in source_ledger['prediction_seals'].items():
        if isinstance(reference, str) and parse_period(reference) and isinstance(proof, dict):
            published = _aware(proof.get('published_at'))
            if (proof.get('revision') == revision and published is not None and published <= now
                    and proof.get('publication_state') == 'published'):
                candidates.add(next_period(reference))
    for target, entry in hit['verified'].items():
        if isinstance(entry, dict) and isinstance(entry.get('publication'), dict):
            if entry['publication'].get('revision') == revision:
                candidates.add(target)
    for target, proof in hit['pending'].items():
        if isinstance(proof, dict) and proof.get('revision') == revision:
            published = _aware(proof.get('published_at'))
            if published is not None and published <= now:
                candidates.add(target)
    for target, old in prior.items():
        published = _aware(old.get('published_at'))
        actual = old.get('actual')
        opened = _aware(actual.get('opentime')) if isinstance(actual, dict) else None
        if (published is not None and published <= now) or (opened is not None and opened <= now):
            candidates.add(target)
    bounded = [period_slot(target) for target in candidates
               if isinstance(target, str) and parse_period(target)
               and start <= period_slot(target) < end
               and (period_slot(target) <= now or target not in conflicts)]
    if not bounded:
        return []
    last = max(bounded)
    # A forged target cannot make the simulator assign bets multiple slots into
    # the future.  The native validator will additionally check its reference.
    last = min(last, period_slot(_period_at(now.replace(second=0, microsecond=0,
                                minute=(now.minute // 5) * 5))) + timedelta(minutes=5))
    events, stopped, last_settled_actual_time = [], False, None
    slot = start
    while slot <= last:
        target = _period_at(slot)
        reference = _reference(target)
        old = prior.get(target)
        event = {'target_period': target, 'ref_period': reference, 'status': 'skip',
                 'published_at': None, 'actual_open_time': None,
                 'proof_sha256': None, 'coverage_sha256': None,
                 'validation_token': None}
        reason = None
        selected_proof = None
        entry = hit['verified'].get(target)
        pending = hit['pending'].get(target)
        raw = source_ledger['prediction_seals'].get(reference)
        sources = []
        for value in (raw, entry.get('publication') if isinstance(entry, dict) else None,
                      pending, old.get('publication') if isinstance(old, dict) else None):
            if isinstance(value, dict) and value.get('revision') == revision:
                # Future publications are deferred rather than allowed to
                # contradict a current frozen snapshot's earlier evidence.
                stamp = _aware(value.get('published_at'))
                if stamp is None or stamp <= now:
                    sources.append(value)
        if sources:
            selected_proof = sources[0]
            if any(_packed(value) != _packed(selected_proof) for value in sources[1:]):
                reason = 'conflicting-publication-copies'
            elif raw is not None and (not isinstance(raw, dict) or raw.get('revision') != revision):
                reason = 'conflicting-current-source-publication'
        actual = actuals.get(target)
        actual_copies = [actual] if actual is not None else []
        for value in (entry.get('actual') if isinstance(entry, dict) else None,
                      old.get('actual') if isinstance(old, dict) else None):
            if value is not None:
                checked = (valid_draw_record(value.get('period'), value.get('numbers'),
                                             value.get('opentime'), now)
                           if isinstance(value, dict) else None)
                if checked is None:
                    opened = _aware(value.get('opentime')) if isinstance(value, dict) else None
                    if opened is None or opened <= now:
                        reason = reason or 'invalid-retained-actual'
                elif checked['period'] != target:
                    reason = reason or 'conflicting-actual-identity'
                else:
                    actual_copies.append(checked)
        if actual_copies:
            actual = actual_copies[0]
            if any(value != actual for value in actual_copies[1:]):
                reason = reason or 'conflicting-actual-copies'
            event['actual'] = deepcopy(actual)
            event['actual_open_time'] = actual['opentime']
        if target in conflicts or (old is not None and old.get('status') == 'conflict'):
            reason = reason or 'conflicting-evidence'
        if entry is not None and pending is not None:
            reason = reason or 'duplicate-settled-and-pending-target'

        coverage = None
        if selected_proof is not None and reason is None:
            try:
                checked = _checked_proof(model, selected_proof, reference, target, now, old, fingerprint)
                if checked is not None:
                    fields, coverage = checked
                    event.update(fields)
            except (ValueError, TypeError, KeyError, OverflowError):
                # A new invalid prediction is an unplaced round; corruption of
                # a previously accepted proof must halt, never erase its cost.
                if old is not None and isinstance(old.get('publication'), dict):
                    reason = 'invalid-previously-accepted-publication'
                elif isinstance(entry, dict) or pending is not None:
                    reason = 'invalid-statistics-publication'
                else:
                    event['reason'] = 'ineligible-publication'
        if coverage is not None and actual is not None and reason is None:
            try:
                error = publication_evidence_error(event['publication'], actual)
                if error:
                    raise ValueError('settlement-publication-evidence')
                native = import_module(_MODELS[model][1]).outcomes(
                    event['publication']['response'], actual['numbers'], revision=revision)
                expected_count = 7 if model == 'selected' else 6
                if (len(native) != expected_count or any(type(v) is not bool for v in native.values())
                        or all(native.values()) != coverage.covers_actual(actual['numbers'])):
                    raise ValueError('coverage-native-outcome-disagreement')
                if isinstance(entry, dict):
                    expected_entry = {'ref_period': reference, 'target_period': target,
                                      'publication': event['publication'], 'actual': actual,
                                      'biz_date': business_start(parse_time(actual['opentime'])).strftime('%Y-%m-%d'),
                                      'outcomes': native}
                    if entry != expected_entry:
                        raise ValueError('statistics-entry-disagreement')
                event['hit'] = all(native.values())
            except (ValueError, TypeError, KeyError, OverflowError):
                reason = 'invalid-settlement-evidence'

        chronology_blocked = (not stopped and coverage is not None
                              and last_settled_actual_time is not None
                              and last_settled_actual_time >= _aware(event['published_at']))

        if reason is not None:
            event['status'], event['reason'] = 'conflict', reason
            # Preserve the offending complete evidence for diagnosis and a
            # durable stop even if a later source omits the conflicting copy.
            if selected_proof is not None:
                event.setdefault('publication', deepcopy(selected_proof))
            stopped = True
        elif stopped:
            event['status'], event['reason'] = 'blocked', 'earlier-unresolved-evidence'
            event.pop('hit', None)
        elif chronology_blocked:
            event['status'], event['reason'] = 'blocked', 'previous-outcome-not-available-at-publication'
            event.pop('hit', None)
            stopped = True
        elif coverage is not None:
            if actual is None:
                event['status'], event['reason'] = 'pending', 'waiting-for-exact-actual'
                stopped = True
            else:
                event['status'] = 'settled'
                last_settled_actual_time = _aware(actual['opentime'])
        elif actual is not None:
            event['status'] = 'skip'
            event.setdefault('reason', 'no-eligible-current-publication')
        else:
            event['status'], event['reason'] = 'blocked', 'missing-exact-actual'
            stopped = True
        events.append(event)
        slot += timedelta(minutes=5)
    return events
