"""Pure daily simulated accounting over prevalidated immutable framework events.

Amounts are integer CNY cents. The hypothetical group count is fixed at 5,000;
each group's stake is 1.50/3.00/6.00/12.00 CNY. A hit returns 9,900 times that
single-group stake TOTAL, inclusive of principal. Actual coverage determines
the hit only; its number count does not price this fixed-count assumption.
This is an observation ledger, not an order/payment client. Each framework
resets its own sequence at 05:00 Asia/Shanghai. Unresolved events block later
assignment. The original per-valid-code v1 engine is preserved separately.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from stats_history import parse_period, period_slot


CONFIG = {'revision': 'framework-pnl-v2-fixed5000', 'stake_unit': 'per-group-fixed-5000',
          'group_count': 5000, 'steps_cents': [150, 300, 600, 1200],
          'gross_multiplier': 9900, 'currency': 'CNY'}
CONFIG_SHA256 = sha256(json.dumps(CONFIG, sort_keys=True, separators=(',', ':'),
                                ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
_STAKES = (150, 300, 600, 1200)
_GROUP_COUNT = 5000
_GROSS_MULTIPLIER = 9900
_MODELS = frozenset(('selected', 'six'))
_STATUSES = frozenset(('settled', 'pending', 'skip', 'conflict', 'blocked'))
_TZ = ZoneInfo('Asia/Shanghai')


def _moment(value, label):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('invalid-framework-pnl-' + label) from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('invalid-framework-pnl-' + label)
    return value.astimezone(_TZ)


def _day_start(moment):
    start = moment.replace(hour=5, minute=0, second=0, microsecond=0)
    return start if moment >= start else start - timedelta(days=1)


def _period(value, label):
    parsed = parse_period(value)
    if not isinstance(value, str) or not parsed or value != parsed[0]:
        raise ValueError('invalid-framework-pnl-' + label)
    return value


def _next_period(reference):
    parsed = parse_period(reference)
    if parsed[2] == 288:
        return (parsed[1] + timedelta(days=1)).strftime('%Y%m%d') + '001'
    return reference[:8] + f'{parsed[2] + 1:03d}'


def _validate_event(source, now, start):
    if not isinstance(source, dict):
        raise ValueError('invalid-framework-pnl-event')
    row = deepcopy(source)
    target = _period(row.get('target_period'), 'target-period')
    reference = _period(row.get('ref_period'), 'reference-period')
    if _next_period(reference) != target:
        raise ValueError('invalid-framework-pnl-reference-target-pair')
    status = row.get('status')
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError('invalid-framework-pnl-event-status')
    if 'reason' in row and not isinstance(row['reason'], str):
        raise ValueError('invalid-framework-pnl-event-reason')
    opened = row.get('actual_open_time')
    opened = _moment(opened, 'actual-open-time') if opened is not None else None
    if opened is not None and opened > now:
        raise ValueError('invalid-framework-pnl-future-actual-open-time')
    belongs = opened if opened is not None else period_slot(target)
    if not start <= belongs < start + timedelta(days=1):
        raise ValueError('invalid-framework-pnl-event-business-day')
    published = row.get('published_at')
    if published is not None and _moment(published, 'published-at') > now:
        raise ValueError('invalid-framework-pnl-future-publication')
    count = row.get('number_count')
    if count is not None and (type(count) is not int or not 0 <= count <= 10000):
        raise ValueError('invalid-framework-pnl-number-count')
    if 'hit' in row and row['hit'] is not None and type(row['hit']) is not bool:
        raise ValueError('invalid-framework-pnl-hit')
    if status in ('settled', 'pending'):
        if count is None:
            raise ValueError('missing-framework-pnl-number-count')
        if published is None:
            raise ValueError('missing-framework-pnl-publication')
    if status == 'settled' and (opened is None or type(row.get('hit')) is not bool):
        raise ValueError('invalid-framework-pnl-settled-event')
    if status == 'pending' and row.get('hit') is not None:
        raise ValueError('invalid-framework-pnl-pending-hit')
    return row


def simulate_day(model, events, now):
    """Return a daily simulated P&L ledger without changing any input object.

    Eligible event proofs/coverage are validated by the evidence layer. This
    function independently rejects malformed money inputs, wrong-day entries,
    duplicate/out-of-order periods, and non-adjacent reference/target identities.
    Each group's stake is 1.50/3.00/6.00/12.00 CNY. Cost is fixed at 5,000 times
    that stake; a hit returns 9,900 times that stake TOTAL, inclusive of principal.
    The observed actual valid-code count still determines eligibility/hit but
    does not replace the declared 5,000-group costing assumption.
    """
    if not isinstance(model, str) or model not in _MODELS:
        raise ValueError('invalid-framework-pnl-model')
    if not isinstance(now, datetime):
        raise ValueError('invalid-framework-pnl-now')
    moment = _moment(now, 'now')
    start = _day_start(moment)
    if not isinstance(events, (list, tuple)):
        raise ValueError('invalid-framework-pnl-events')
    validated = [_validate_event(source, moment, start) for source in events]
    targets = [row['target_period'] for row in validated]
    if len(set(targets)) != len(targets) or targets != sorted(targets):
        raise ValueError('duplicate-or-unordered-framework-pnl-periods')

    totals = {'settled_count': 0, 'win_count': 0, 'loss_count': 0,
              'cost_cents': 0, 'return_cents': 0, 'net_cents': 0,
              'max_drawdown_cents': 0, 'four_loss_cycles': 0}
    rows, step, pending_count, skipped_count, blocked_count = [], 0, 0, 0, 0
    stopped, peak, latest_settled = False, 0, None
    for row in validated:
        original_status = row['status']
        row.update({'step': None, 'stake_cents': None, 'cost_cents': 0,
                    'return_cents': 0, 'net_cents': 0, 'committed_cost_cents': 0,
                    'cumulative_net_cents': totals['net_cents'], 'reset_reason': None})
        # Zero unique codes means no position was simulated. Do not interpret an
        # empty ticket as a loss or advance the sequence on an absent wager.
        if original_status in ('settled', 'pending') and row['number_count'] == 0:
            row.setdefault('source_status', original_status)
            row['status'], row['accounting_reason'] = 'skip', 'no_candidates'
            row.setdefault('reason', 'no_candidates')
            original_status = 'skip'
        if original_status == 'skip':
            skipped_count += 1
        else:
            if original_status == 'pending':
                pending_count += 1
            if stopped or original_status in ('blocked', 'conflict'):
                row.setdefault('source_status', original_status)
                row['status'] = 'conflict' if original_status == 'conflict' else 'blocked'
                if original_status not in ('blocked', 'conflict'):
                    row['accounting_reason'] = 'earlier-event-unresolved'
                    row.setdefault('reason', 'earlier-event-unresolved')
                blocked_count += 1
                stopped = True
            else:
                row['step'], row['stake_cents'] = step, _STAKES[step]
                if original_status == 'pending':
                    row['committed_cost_cents'] = _GROUP_COUNT * _STAKES[step]
                    stopped = True
                else:
                    row['cost_cents'] = _GROUP_COUNT * _STAKES[step]
                    row['return_cents'] = _GROSS_MULTIPLIER * _STAKES[step] if row['hit'] else 0
                    row['net_cents'] = row['return_cents'] - row['cost_cents']
                    totals['settled_count'] += 1
                    totals['win_count' if row['hit'] else 'loss_count'] += 1
                    for field in ('cost_cents', 'return_cents', 'net_cents'):
                        totals[field] += row[field]
                    row['cumulative_net_cents'] = totals['net_cents']
                    peak = max(peak, totals['net_cents'])
                    totals['max_drawdown_cents'] = max(totals['max_drawdown_cents'],
                                                       peak - totals['net_cents'])
                    latest_settled = row['target_period']
                    if row['hit']:
                        step, row['reset_reason'] = 0, 'hit'
                    elif step == 3:
                        step, row['reset_reason'] = 0, 'four-losses'
                        totals['four_loss_cycles'] += 1
                    else:
                        step += 1
        rows.append(row)

    return {'model': model, 'biz_date': start.date().isoformat(), 'events': rows,
            'totals': totals, 'pending_count': pending_count, 'skipped_count': skipped_count,
            'blocked_count': blocked_count, 'current_step': step, 'current_stake_cents': _STAKES[step],
            'latest_settled_period': latest_settled, 'stats_error': False}
