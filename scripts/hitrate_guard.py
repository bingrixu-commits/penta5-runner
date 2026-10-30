#!/usr/bin/env python3
"""Server-side hit-rate / missed-period guard for the five-minute AI pipeline.

Runs from cron only; no local dependency. It never writes predictions, never
calls any provider and never changes AI switches. It reads already published
evidence, keeps a bounded status file and appends a log line only on a status
transition (or when anything is warn/fail). A persisted watermark means only
gaps that happen after the guard was first installed are alerted.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from stats_history import beijing_now, period_slot

STATE_NAME = 'hitrate_guard.json'
LOG_NAME = 'hitrate_guard.log'
MIN_SAMPLE = 100
MISSING_LOOKBACK = 100
PENDING_GRACE_MINUTES = 20
FALLBACK_WINDOW_HOURS = 24
FAILURE_WINDOW_HOURS = 2


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False, encoding='utf-8')
    temporary = handle.name
    try:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _period(value):
    text = str(value or '')
    return text if re.fullmatch(r'[0-9]{11}', text) else None


def _stamp(reference):
    try:
        return period_slot(reference)
    except Exception:
        return None


def _fallback_events(root, now, watermark=None):
    current = _period((_read_json(root / 'data/state.json', {}) or {}).get('latestRecord', {}).get('period'))
    fallback_cutoff = (now - timedelta(hours=FALLBACK_WINDOW_HOURS))
    failure_cutoff = (now - timedelta(hours=FAILURE_WINDOW_HOURS))
    fallbacks = 0
    failures = 0
    last_fallback = None
    for name in ('selected_six_cron.log', 'selected_six_cron.log.1'):
        try:
            lines = (root / 'data' / name).read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for line in lines:
            if 'SELECTED_SIX_LOCAL_MINIMAX_FALLBACK' in line:
                match = re.search(r'SELECTED_SIX_LOCAL_MINIMAX_FALLBACK ref=([0-9]{11})', line)
                reference = _period(match.group(1)) if match else None
                stamp = _stamp(reference) if reference else None
                if stamp is not None and stamp >= fallback_cutoff and (watermark is None or stamp >= watermark):
                    fallbacks += 1
                    if last_fallback is None or reference > last_fallback:
                        last_fallback = reference
                continue
            if 'SELECTED_SIX_INDEPENDENT_FAILURE' in line:
                match = re.search(r'SELECTED_SIX_INDEPENDENT_FAILURE ref=([0-9]{11})', line)
                reference = _period(match.group(1)) if match else None
                stamp = _stamp(reference) if reference else None
                if stamp is not None and stamp >= failure_cutoff and (watermark is None or stamp >= watermark):
                    failures += 1
    return {'fallbacks_24h': fallbacks, 'final_failures_2h': failures,
            'last_fallback_ref': last_fallback, 'current_reference': current}


def _verified_periods(root, relative):
    document = _read_json(root / relative, {}) or {}
    body = document.get('verified')
    if isinstance(body, dict):
        return set(body)
    return set()


def _missing(root, now, watermark):
    killer = _read_json(root / 'data/r1_stats.json', {}) or {}
    evaluations = killer.get('verified_evaluations')
    if not isinstance(evaluations, list):
        evaluations = []
    periods = sorted({str(row.get('target_period')) for row in evaluations
                      if isinstance(row, dict) and _period(row.get('target_period'))})
    reference = periods[-MISSING_LOOKBACK:]
    deadlines = now - timedelta(minutes=PENDING_GRACE_MINUTES)
    models = {}
    for key, relative in (
            ('framework', 'data/r1_framework_hit_stats.json'),
            ('six', 'data/selected_six_hit_stats.json'),
            ('he3', 'data/he3_refine_hit_stats.json')):
        have = _verified_periods(root, relative)
        missing = [period for period in reference if period not in have]
        alert = []
        for period in missing:
            stamp = _stamp(period)
            if stamp is None or stamp > deadlines:
                continue
            if watermark is not None and stamp < watermark:
                continue
            alert.append(period)
        status = 'fail' if len(alert) > 5 else 'warn' if alert else 'ok'
        models[key] = {'missing_count': len(missing), 'alert_count': len(alert),
                       'missing_periods': missing[-8:], 'status': status}
    return {'reference_count': len(reference), 'models': models}


def _hitrate_status(row):
    try:
        expected = float(row.get('expected'))
        rate = float(row.get('rate'))
        n = int(row.get('n'))
    except (TypeError, ValueError):
        return {'status': 'unknown', 'z': None}
    if n < MIN_SAMPLE:
        return {'status': 'insufficient', 'z': None}
    standard_error = (expected * (1.0 - expected) / n) ** 0.5
    z = (rate - expected) / standard_error if standard_error else 0.0
    status = 'fail' if z <= -3.0 else 'warn' if z <= -2.0 else 'ok'
    return {'status': status, 'z': round(z, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='penta5 hit-rate / missed-period guard')
    parser.add_argument('--root', default=str(ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root)
    checked_at = beijing_now()
    rank = {'ok': 0, 'insufficient': 0, 'unknown': 0, 'warn': 1, 'fail': 2}
    overall = 'ok'
    def raise_status(status):
        nonlocal overall
        if rank.get(status, 0) > rank.get(overall, 0):
            overall = status

    rows = []
    baseline = _read_json(root / 'data/hitrate_baseline.json', {}) or {}
    for row in baseline.get('rows') or []:
        if not isinstance(row, dict) or not isinstance(row.get('key'), str):
            continue
        extra = _hitrate_status(row)
        rows.append({'key': row['key'], 'label': row.get('label'), 'n': row.get('n'),
                     'rate': row.get('rate'), 'expected': row.get('expected'),
                     'delta': row.get('delta'), 'verdict': row.get('verdict'), **extra})
    for row in rows:
        raise_status(row['status'])

    state_path = root / 'data' / STATE_NAME
    previous = _read_json(state_path, {}) or {}
    watermark = previous.get('watermark')
    if not isinstance(watermark, str):
        watermark = checked_at.isoformat()

    six = _fallback_events(root, checked_at, _stamp_iso(watermark))
    six_status = 'warn' if six['final_failures_2h'] else 'ok'
    raise_status(six_status)
    six['status'] = six_status
    missing = _missing(root, checked_at, _stamp_iso(watermark))
    for result in (missing.get('models') or {}).values():
        raise_status(result['status'])

    balance = _read_json(root / 'data/ai_balance_guard.json', {}) or {}
    last_balance = balance.get('last_balance')
    balance_status = 'ok'
    if isinstance(last_balance, (int, float)):
        balance_status = 'fail' if last_balance < 1 else 'warn' if last_balance < 2 else 'ok'
    raise_status(balance_status)

    state = {'version': 1, 'checked_at': checked_at.isoformat(), 'watermark': watermark,
             'status': overall,
             'latest_reference': baseline.get('as_of_period'),
             'hitrate': rows, 'six': six, 'missing': missing,
             'balance': {'last_balance': last_balance, 'status': balance_status}}
    _atomic_json(state_path, state)
    if overall != 'ok' or previous.get('status') != overall:
        with (root / 'data' / LOG_NAME).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(state, ensure_ascii=False) + '\n')
    print(json.dumps({'status': overall, 'checked_at': state['checked_at'],
                      'hitrate_non_ok': [row['key'] for row in rows if row['status'] not in ('ok', 'insufficient', 'unknown')],
                      'six': {'fallbacks_24h': six['fallbacks_24h'], 'failures_2h': six['final_failures_2h']},
                      'missing_alert': {key: value['alert_count'] for key, value in (missing.get('models') or {}).items()},
                      'balance': last_balance}, ensure_ascii=False))
    return 0


def _stamp_iso(value):
    try:
        moment = datetime.fromisoformat(value)
        return moment if moment.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


if __name__ == '__main__':
    raise SystemExit(main())
