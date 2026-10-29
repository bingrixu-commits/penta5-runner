"""Exact-target, prospective statistics for versioned selected frameworks.

A position or tail metric is keyed by its physical identity, not by the model's
selection order. Unselected or missing targets interrupt streaks without being
invented as wins or losses. Original publication and actual-draw evidence is
retained and revalidated before derived statistics may be returned.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import fcntl
import json

from ai_verified_stats import atomic_json, next_period, publication_evidence_error
from selected_framework_contract import (BASE_METRIC_KEYS, EXTRA_METRIC_KEYS,
                                         FIVE_REVISION, SEVEN_REVISION, METRIC_KEYS,
                                         REVISION as RULE_REVISION,
                                         SUPPORTED_REVISIONS, item_count, outcomes)
from stats_history import (TZ, business_start, load_snapshot, parse_period, parse_time,
                           period_slot, valid_draw_record, validate_conflicting_periods)

ROOT = Path('/root/penta5-runner')
REVISION = 'selected-six-stats-v1'


def _current(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('selected-statistics-clock-must-be-aware')
    return now.astimezone(TZ)


def _publication(seal, reference, now, root):
    from publication_validation_cache import validate_publication
    parsed = parse_period(reference)
    if (not isinstance(reference, str) or not parsed or reference != parsed[0]
            or not isinstance(seal, dict)):
        raise ValueError('invalid-selected-publication-identity')
    checked = validate_publication('selected7', seal, reference, now, published=True, root=root)
    if checked.get('ref_period') != reference:
        raise ValueError('invalid-selected-publication-reference')
    return deepcopy(checked)


def _verified_entry(proof, record, now):
    if not isinstance(record, dict):
        raise ValueError('invalid-selected-actual')
    actual = valid_draw_record(record.get('period'), record.get('numbers'), record.get('opentime'), now)
    if actual is None or actual['period'] != proof['target_period']:
        raise ValueError('invalid-selected-exact-target')
    error = publication_evidence_error(proof, actual)
    if error:
        raise ValueError('invalid-selected-settlement-publication')
    # The saved proof owns its rule. Existing six-item settlements must never
    # acquire a fabricated seventh outcome during a later rules migration.
    result = outcomes(proof['response'], actual['numbers'], revision=proof['revision'])
    expected_count = item_count(proof['revision'])
    extra_count = int('extra_exclusion' in proof['response'])
    if (not isinstance(result, dict) or len(result) != expected_count
            or not set(result) <= set(METRIC_KEYS)
            or len(set(result) & set(BASE_METRIC_KEYS)) != expected_count - extra_count
            or len(set(result) & set(EXTRA_METRIC_KEYS)) != extra_count
            or any(type(hit) is not bool for hit in result.values())):
        raise ValueError('invalid-selected-versioned-outcomes')
    return {'ref_period': proof['ref_period'], 'target_period': proof['target_period'],
            'publication': deepcopy(proof), 'actual': actual,
            'biz_date': business_start(parse_time(actual['opentime'])).strftime('%Y-%m-%d'),
            'outcomes': result}


def _empty(now):
    return {'version': 1, 'revision': REVISION, 'updated_at': now.isoformat(),
            'verified': {}, 'pending': {}, 'conflicts': [], 'rejected': []}


def _restore(previous, now, root):
    ledger = _empty(now)
    if previous is None or previous == {}:
        return ledger
    if (not isinstance(previous, dict) or type(previous.get('version')) is not int
            or previous['version'] != 1 or previous.get('revision') != REVISION
            or not isinstance(previous.get('verified'), dict)
            or not isinstance(previous.get('pending'), dict)):
        raise ValueError('invalid-selected-statistics-ledger')
    ledger['conflicts'] = sorted(validate_conflicting_periods(previous.get('conflicts', ())))
    for target, entry in previous['verified'].items():
        if not isinstance(entry, dict) or not isinstance(entry.get('publication'), dict):
            raise ValueError('corrupt-selected-settlement')
        proof = _publication(entry['publication'], entry.get('ref_period'), now, root)
        checked = _verified_entry(proof, entry.get('actual'), now)
        saved_outcomes = entry.get('outcomes')
        if (target != checked['target_period'] or not isinstance(saved_outcomes, dict)
                or any(type(hit) is not bool for hit in saved_outcomes.values()) or entry != checked):
            raise ValueError('corrupt-selected-settlement-refusing-rewrite')
        ledger['verified'][target] = deepcopy(entry)
    for target, seal in previous['pending'].items():
        if not isinstance(seal, dict):
            raise ValueError('corrupt-selected-pending')
        proof = _publication(seal, seal.get('ref_period'), now, root)
        if target != proof['target_period'] or target in ledger['verified']:
            raise ValueError('corrupt-selected-pending-identity')
        ledger['pending'][target] = proof
    return ledger


def _actuals(records, now):
    if not isinstance(records, (list, tuple)):
        raise ValueError('invalid-selected-actual-history')
    available, conflicts = {}, set()
    for raw in records:
        if not isinstance(raw, dict):
            continue
        record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if record is None:
            continue
        target = record['period']
        if target in available and available[target] != record:
            conflicts.add(target)
        available[target] = record
    return available, conflicts


def refresh_stats(seals, records, now, previous=None, *, root=ROOT, conflicting_periods=()):
    """Add only genuine published predictions settled by the exact real draw."""
    now = _current(now)
    ledger = _restore(previous, now, root)
    if (not isinstance(seals, dict)
            or not isinstance(seals.get('prediction_seals', {}), dict)):
        raise ValueError('invalid-selected-prediction-ledger')
    actuals, observed_conflicts = _actuals(records, now)
    conflicts = (set(ledger['conflicts']) | observed_conflicts
                 | validate_conflicting_periods(conflicting_periods))
    for reference, seal in seals.get('prediction_seals', {}).items():
        try:
            proof = _publication(seal, reference, now, root)
            target = proof['target_period']
            stored = ledger['verified'].get(target)
            existing = stored['publication'] if stored else ledger['pending'].get(target)
            if existing is not None and existing != proof:
                conflicts.add(target)
                raise ValueError('conflicting-selected-publication')
            if stored is None:
                ledger['pending'][target] = proof
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            revision = seal.get('revision') if isinstance(seal, dict) else None
            ledger['rejected'].append({'ref_period': str(reference), 'reason': 'invalid-publication',
                                       'rule_revision': revision if isinstance(revision, str)
                                       and revision in SUPPORTED_REVISIONS else None})
            if isinstance(seal, dict):
                target = seal.get('target_period')
                if isinstance(target, str) and target in (set(ledger['verified']) | set(ledger['pending'])):
                    conflicts.add(target)
    for target, entry in ledger['verified'].items():
        if target in actuals and actuals[target] != entry['actual']:
            conflicts.add(target)
    for target, proof in list(ledger['pending'].items()):
        if target in conflicts or target not in actuals:
            continue
        try:
            ledger['verified'][target] = _verified_entry(proof, actuals[target], now)
            del ledger['pending'][target]
        except (ValueError, TypeError, KeyError):
            conflicts.add(target)
            ledger['rejected'].append({'target_period': target, 'reason': 'invalid-settlement-evidence',
                                       'rule_revision': proof['revision']})
    tracked = set(ledger['verified']) | set(ledger['pending'])
    # An observed draw conflict survives even before this model's seal is
    # visible. A later single source copy cannot resolve that ambiguity.
    ledger['conflicts'] = sorted(conflicts)
    return ledger


def _series_summary(entries, latest_known, key=None, *, keys=None):
    chosen = entries if key is None else [entry for entry in entries if key in entry['outcomes']]
    if key is None and keys is not None:
        # A rule without flexible extras has no extra observations. Empty AND
        # groups must not become fabricated successes during a migration.
        chosen = [entry for entry in chosen if set(entry['outcomes']).intersection(keys)]
    hits = wins = losses = max_wins = max_losses = 0
    previous = None
    for entry in chosen:
        target = entry['target_period']
        if previous is not None and next_period(previous) != target:
            wins = losses = 0
        if key is None:
            values = (entry['outcomes'].values() if keys is None
                      else (hit for metric, hit in entry['outcomes'].items() if metric in keys))
            hit = all(values)
        else:
            hit = entry['outcomes'][key]
        hits += int(hit)
        if hit:
            wins += 1; losses = 0; max_wins = max(max_wins, wins)
        else:
            losses += 1; wins = 0; max_losses = max(max_losses, losses)
        previous = target
    interrupted = bool(chosen and latest_known and chosen[-1]['target_period'] != latest_known)
    if interrupted:
        wins = losses = 0
    total = len(chosen)
    return {'hits': hits, 'total': total, 'percent': round(100 * hits / total, 4) if total else None,
            'max_win_streak': max_wins, 'max_loss_streak': max_losses,
            'current_win_streak': wins, 'current_loss_streak': losses,
            'current_streak_interrupted': interrupted,
            'current_streak_as_of': latest_known}


def make_summary(stats, now, records=(), *, root=ROOT, conflicting_periods=()):
    """Daily and current-rule cumulative rates from the same validated evidence.

    The daily projection keeps its Beijing 05:00 boundary. Cumulative evidence
    never borrows another rule version's outcomes or resets at that boundary.
    Both projections interrupt a series on missing or unselected targets.
    """
    now = _current(now)
    ledger = _restore(stats, now, root)
    day = business_start(now).strftime('%Y-%m-%d')
    actuals, observed_conflicts = _actuals(records, now)
    tracked = set(ledger['verified']) | set(ledger['pending'])
    explicit_conflicts = validate_conflicting_periods(conflicting_periods)
    conflicts = (set(ledger['conflicts']) | observed_conflicts
                 | explicit_conflicts) & tracked
    for target, entry in ledger['verified'].items():
        if target in actuals and actuals[target] != entry['actual']:
            conflicts.add(target)
    def today(target):
        return business_start(period_slot(target)).strftime('%Y-%m-%d') == day
    active_targets = {target for target, entry in ledger['verified'].items()
                      if entry['publication']['revision'] == RULE_REVISION}
    active_targets.update(target for target, proof in ledger['pending'].items()
                          if proof['revision'] == RULE_REVISION)
    entries = sorted((entry for target, entry in ledger['verified'].items()
                      if entry['biz_date'] == day and target not in conflicts
                      and entry['publication']['revision'] == RULE_REVISION),
                     key=lambda entry: (parse_time(entry['actual']['opentime']), entry['target_period']))
    known_targets = {target for target in actuals if today(target)}
    known_targets.update(entry['target_period'] for entry in ledger['verified'].values()
                         if entry['biz_date'] == day)
    # A source may remove both versions of a conflicted actual draw from its
    # clean records. Its explicit closed identity still interrupts a prior run,
    # even when no prediction was published for that target. Future flags do
    # not turn pending targets into already-closed draws.
    known_targets.update(target for target in (explicit_conflicts | set(ledger['conflicts']))
                         if period_slot(target) <= now and today(target))
    latest_known = max(known_targets, key=period_slot, default=None)
    cumulative_entries = sorted((entry for target, entry in ledger['verified'].items()
                                 if target not in conflicts
                                 and entry['publication']['revision'] == RULE_REVISION),
                                key=lambda entry: (parse_time(entry['actual']['opentime']),
                                                   entry['target_period']))
    cumulative_known = set(actuals) | set(ledger['verified'])
    cumulative_known.update(target for target in (explicit_conflicts | set(ledger['conflicts']))
                            if period_slot(target) <= now)
    cumulative_latest = max(cumulative_known, key=period_slot, default=None)
    cumulative = {'rule_revision': RULE_REVISION, 'stats_error': False,
                  'as_of': now.isoformat(),
                  'metrics': {key: _series_summary(cumulative_entries, cumulative_latest, key)
                              for key in BASE_METRIC_KEYS},
                  'overall': _series_summary(cumulative_entries, cumulative_latest),
                  'base_overall': _series_summary(cumulative_entries, cumulative_latest,
                                                  keys=BASE_METRIC_KEYS),
                  'extra_overall': _series_summary(cumulative_entries, cumulative_latest,
                                                   keys=EXTRA_METRIC_KEYS),
                  'latest_settled_period': (cumulative_entries[-1]['target_period']
                                            if cumulative_entries else None),
                  'latest_known_closed_period': cumulative_latest}
    previous_entries = sorted((entry for target, entry in ledger['verified'].items()
                               if target not in conflicts
                               and entry['publication']['revision'] == FIVE_REVISION),
                              key=lambda entry: (parse_time(entry['actual']['opentime']),
                                                 entry['target_period']))
    previous_six = {'rule_revision': FIVE_REVISION, 'biz_date': day, 'stats_error': False,
                    'overall': _series_summary([entry for entry in previous_entries
                                               if entry['biz_date'] == day], latest_known),
                    'cumulative_overall': _series_summary(previous_entries, cumulative_latest)}
    old_seven_entries = sorted((entry for target, entry in ledger['verified'].items()
                               if target not in conflicts
                               and entry['publication']['revision'] == SEVEN_REVISION),
                              key=lambda entry: (parse_time(entry['actual']['opentime']),
                                                 entry['target_period']))
    previous_seven = {'rule_revision': SEVEN_REVISION, 'biz_date': day, 'stats_error': False,
                      'overall': _series_summary([entry for entry in old_seven_entries
                                                 if entry['biz_date'] == day], latest_known),
                      'cumulative_overall': _series_summary(old_seven_entries, cumulative_latest)}
    historical_rules = {}
    for revision in sorted(SUPPORTED_REVISIONS - {RULE_REVISION}):
        previous = [entry for target, entry in ledger['verified'].items()
                    if target not in conflicts and entry['publication']['revision'] == revision]
        previous.sort(key=lambda entry: (parse_time(entry['actual']['opentime']), entry['target_period']))
        historical_rules[revision] = {
            'rule_revision': revision,
            'biz_date': day, 'stats_error': False,
            'metrics': {key: _series_summary([entry for entry in previous if entry['biz_date'] == day], latest_known, key)
                        for key in METRIC_KEYS},
            'cumulative_metrics': {key: _series_summary(previous, cumulative_latest, key)
                                   for key in METRIC_KEYS},
            'overall': _series_summary([entry for entry in previous if entry['biz_date'] == day], latest_known),
            'cumulative_overall': _series_summary(previous, cumulative_latest)}
        if item_count(revision) == 7:
            historical_rules[revision].update(
                base_overall=_series_summary(previous, cumulative_latest, keys=BASE_METRIC_KEYS),
                extra_overall=_series_summary(previous, cumulative_latest, keys=EXTRA_METRIC_KEYS))
    pending = [target for target, proof in ledger['pending'].items()
               if today(target) and target not in conflicts and proof['revision'] == RULE_REVISION]
    rejected = 0
    # Rejections are the current refresh's diagnostics, not durable outcomes.
    # _restore deliberately rebuilds them, so read the caller's diagnostics
    # here and count only explicitly tagged current-rule attempts.
    rejections = stats.get('rejected', ()) if isinstance(stats, dict) else ()
    if not isinstance(rejections, (list, tuple)):
        raise ValueError('invalid-selected-statistics-rejections')
    for item in rejections:
        if not isinstance(item, dict):
            raise ValueError('invalid-selected-statistics-rejection')
        if item.get('rule_revision') != RULE_REVISION:
            continue
        target = item.get('target_period')
        if not target and parse_period(item.get('ref_period')):
            target = next_period(item['ref_period'])
        parsed = parse_period(target)
        rejected += int(bool(parsed and isinstance(target, str) and target == parsed[0] and today(target)))
    return {'biz_date': day, 'rule_revision': RULE_REVISION,
            'metrics': {key: _series_summary(entries, latest_known, key) for key in BASE_METRIC_KEYS},
            'overall': _series_summary(entries, latest_known),
            'base_overall': _series_summary(entries, latest_known, keys=BASE_METRIC_KEYS),
            'extra_overall': _series_summary(entries, latest_known, keys=EXTRA_METRIC_KEYS),
            'latest_settled_period': entries[-1]['target_period'] if entries else None,
            'latest_known_closed_period': latest_known,
            'pending_count': len(pending),
            'overdue_count': sum(period_slot(target) <= now for target in pending),
            'conflict_count': sum(today(target) for target in conflicts & active_targets),
            'rejected_count': rejected, 'stats_error': False, 'cumulative': cumulative,
            'previous_six': previous_six, 'previous_seven': previous_seven,
            'historical_rules': historical_rules}


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError('invalid-selected-statistics-file')
    return value


def refresh_selected_stats(snapshot=None, root=ROOT):
    """Caller owns panel refresh; this function locks only its private ledger."""
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(
        state_path=root / 'data/state.json', cache_path=root / 'data/stats_draw_history.json', persist=False)
    now = _current(snapshot['now'])
    path = root / 'data/selected_framework_hit_stats.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = _read(path)
            ledger = refresh_stats(_read(root / 'data/selected_framework_seals.json'), snapshot['records'],
                now, previous, root=root, conflicting_periods=snapshot.get('conflicting_periods', ()))
            summary = make_summary(ledger, now, snapshot['records'], root=root,
                                   conflicting_periods=snapshot.get('conflicting_periods', ()))
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            # A broken evidence ledger is retained; an error is never reset to
            # attractive zero-sample or synthetic historical success statistics.
            return {'biz_date': business_start(now).strftime('%Y-%m-%d'),
                    'rule_revision': RULE_REVISION, 'metrics': {}, 'overall': {},
                    'base_overall': {}, 'extra_overall': {},
                    'latest_settled_period': None, 'latest_known_closed_period': None,
                    'pending_count': 0, 'overdue_count': 0, 'conflict_count': 0,
                    'rejected_count': 0, 'stats_error': True,
                    'cumulative': {'rule_revision': RULE_REVISION, 'stats_error': True,
                                   'as_of': now.isoformat(), 'metrics': {}, 'overall': {},
                                   'base_overall': {}, 'extra_overall': {},
                                   'latest_settled_period': None,
                                   'latest_known_closed_period': None},
                    'previous_seven': {'rule_revision': SEVEN_REVISION,
                                     'biz_date': business_start(now).strftime('%Y-%m-%d'),
                                     'stats_error': True, 'overall': {},
                                     'cumulative_overall': {}},
                    'previous_six': {'rule_revision': FIVE_REVISION,
                                     'biz_date': business_start(now).strftime('%Y-%m-%d'),
                                     'stats_error': True, 'overall': {},
                                     'cumulative_overall': {}}}
        if ledger != previous:
            atomic_json(path, ledger)
        atomic_json(root / 'data/selected_framework_stats.json', summary)
        return summary
