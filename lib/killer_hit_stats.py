"""Read-only daily killer summary, independent of inference and its long lock."""
from copy import deepcopy
from datetime import datetime

from ai_verified_stats import next_period, pending_items, settle_pending, validate_killer_ledger
from stats_history import (TZ, business_start, parse_period, parse_time, period_slot,
                           valid_draw_record, validate_conflicting_periods)

METRIC_KEYS = ('k1', 'k2', 'k3', 'k4', 'he_all', 'he_q3', 'he_h3', 'pat1', 'pat2')


def summarize_killer(stats, records, now, *, conflicting_periods=(), strategy_id=None):
    """Derive the current 05:00 business-day view without altering the raw ledger.

    Unpersisted exact-target settlements are recalculated on a private copy each
    minute, so retries cannot accumulate or race with an in-flight AI job.
    Underlying old-day history/pending work remains intact in the original data.
    """
    if not isinstance(stats, dict) or not isinstance(records, (list, tuple)):
        raise ValueError('Killer statistics must be an object and records a sequence')
    if not isinstance(now, datetime):
        raise ValueError('now must be a datetime')
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    day = business_start(now).strftime('%Y-%m-%d')
    source_conflicts = validate_conflicting_periods(conflicting_periods)
    working = deepcopy(stats)
    settlement_records = []
    for record in records:
        parsed = parse_period(record.get('period')) if isinstance(record, dict) else None
        if not parsed or parsed[0] not in source_conflicts:
            settlement_records.append(record)
    settle_pending(working, settlement_records, now=now)
    validated = validate_killer_ledger(working, now)
    bucket = validated['daily'].get(day, {'totals': {}, 'hits': {}})
    historical_policy_count = 0
    allowed_references = None
    if strategy_id is not None:
        if not isinstance(strategy_id, str):
            raise ValueError('Invalid killer strategy identity')
        allowed_references = set()
        for reference, seal in working.get('prediction_seals', {}).items():
            audit = seal.get('model_context_audit') if isinstance(seal, dict) else None
            body = audit.get('body') if isinstance(audit, dict) else None
            strategy = body.get('strategy') if isinstance(body, dict) else None
            if isinstance(strategy, dict) and strategy.get('strategy_id') == strategy_id:
                allowed_references.add(reference)
        filtered = []
        bucket = {'totals': {}, 'hits': {}}
        for verified in validated['verified']:
            entry = verified['entry']
            if entry['ref_period'] not in allowed_references:
                historical_policy_count += int(entry['biz_date'] == day)
                continue
            filtered.append(verified)
            if entry['biz_date'] == day:
                for key, hit in verified['outcome'].items():
                    bucket['totals'][key] = bucket['totals'].get(key, 0) + 1
                    bucket['hits'][key] = bucket['hits'].get(key, 0) + int(hit)
        validated = dict(validated, verified=filtered)
    totals, hits = bucket['totals'], bucket['hits']
    if not isinstance(totals, dict) or not isinstance(hits, dict):
        raise ValueError('Invalid verified killer counts')
    metrics = {}
    for key in METRIC_KEYS:
        total, matched = totals.get(key, 0), hits.get(key, 0)
        if (type(total) is not int or type(matched) is not int or total < 0
                or matched < 0 or matched > total or (total > 0 and key not in hits)):
            raise ValueError('Invalid verified killer count for ' + key)
        metrics[key] = {'hits': matched, 'total': total,
                        'percent': round(matched / total * 100, 1) if total else None}
    observed, settled = {}, {}
    for raw in records:
        if not isinstance(raw, dict):
            continue
        record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if record:
            observed.setdefault(record['period'], set()).add(tuple(record['numbers']))
    for verified in validated['verified']:
        evaluation, record = verified['entry'], verified['actual']
        if business_start(parse_time(record['opentime'])).strftime('%Y-%m-%d') == day:
            settled.setdefault(record['period'], (evaluation, record))
    tracked = set(settled)
    tracked.update(prediction.get('target_period') or next_period(prediction.get('ref_period'))
                   for prediction in pending_items(working)
                   if allowed_references is None or prediction.get('ref_period') in allowed_references)
    conflicts = {target for target, actuals in observed.items() if target in tracked and len(actuals) > 1}
    conflicts.update(source_conflicts & tracked)
    for target, (evaluation, record) in settled.items():
        actuals = observed.get(target)
        if actuals and tuple(record['numbers']) not in actuals:
            conflicts.add(target)
    for target in conflicts & set(settled):
        outcome = settled[target][0].get('outcome')
        if not isinstance(outcome, dict) or any(type(outcome.get(key)) is not bool for key in METRIC_KEYS):
            raise ValueError('Cannot exclude conflicting killer target without its original outcome')
        for key in METRIC_KEYS:
            metric = metrics[key]
            metric['total'] -= 1
            metric['hits'] -= int(outcome[key])
            if not 0 <= metric['hits'] <= metric['total']:
                raise ValueError('Conflicting killer target has inconsistent saved counts')
            metric['percent'] = round(metric['hits'] / metric['total'] * 100, 1) if metric['total'] else None
    current_settled = [record for target, (_, record) in settled.items() if target not in conflicts]
    latest = max(current_settled, key=lambda row: (parse_time(row['opentime']), row['period']), default=None)
    pending_count = 0
    for prediction in pending_items(working):
        if allowed_references is not None and prediction.get('ref_period') not in allowed_references:
            continue
        target = prediction.get('target_period') or next_period(prediction.get('ref_period'))
        slot = period_slot(target)
        if slot and business_start(slot).strftime('%Y-%m-%d') == day:
            pending_count += 1
    return {'biz_date': day, 'metrics': metrics, 'strategy_id': strategy_id,
            'historical_policy_count': historical_policy_count,
            'latest_settled_period': latest['period'] if latest else None,
            'conflict_count': sum(business_start(period_slot(target)).strftime('%Y-%m-%d') == day
                                  for target in conflicts),
            'pending_count': pending_count}
