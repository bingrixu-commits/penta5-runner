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
MIN_SAMPLE = 200
MISSING_LOOKBACK = 100
PENDING_GRACE_MINUTES = 20
FALLBACK_WINDOW_HOURS = 24
FAILURE_WINDOW_HOURS = 2
DRIFT_STALE_MINUTES = 45
DRIFT_PATH = 'data/framework_hitrate_cumulative.json'


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


def _age_minutes(value, now):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None and now.tzinfo is not None:
        moment = moment.replace(tzinfo=now.tzinfo)
    elif moment.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=moment.tzinfo)
    return round((now - moment).total_seconds() / 60.0, 1)


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


def _draw_reference(root):
    document = _read_json(root / 'data' / 'stats_draw_history.json', {}) or {}
    records = document.get('records') if isinstance(document, dict) else None
    if not isinstance(records, list):
        return []
    periods = set()
    for row in records:
        if not isinstance(row, dict):
            continue
        period = _period(row.get('period'))
        if period:
            periods.add(period)
    return sorted(periods)[-MISSING_LOOKBACK:]


def _killer_reference(root):
    killer = _read_json(root / 'data/r1_stats.json', {}) or {}
    evaluations = killer.get('verified_evaluations')
    if not isinstance(evaluations, list):
        evaluations = []
    return sorted({str(row.get('target_period')) for row in evaluations
                   if isinstance(row, dict) and _period(row.get('target_period'))
                   })[-MISSING_LOOKBACK:]


def _ai_control(root):
    document = _read_json(root / 'data' / 'ai_api_control.json', {}) or {}
    introduced = _stamp_iso(document.get('introduced_at')) if isinstance(document, dict) else None
    transitions = []
    raw = document.get('transitions') if isinstance(document, dict) else None
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            at = _stamp_iso(item.get('at'))
            if at is None:
                continue
            transitions.append((at, bool(item.get('enabled'))))
    transitions.sort(key=lambda pair: pair[0])
    return introduced, transitions


def _switch_off(introduced, transitions, moment):
    """AI 总闸在该期 slot 时刻是否处于关闭；introduced_at 之前视为 enabled。"""
    if introduced is None or moment is None or moment < introduced:
        return False
    enabled = None
    for at, state in transitions:
        if at > moment:
            break
        enabled = state
    return enabled is False


def _classified_missing(root, now, watermark):
    reference = _draw_reference(root)
    reference_source = 'draw_history'
    if not reference:
        reference = _killer_reference(root)
        reference_source = 'killer-fallback'
    introduced, transitions = _ai_control(root)
    deadlines = now - timedelta(minutes=PENDING_GRACE_MINUTES)
    models = {}
    for key, relative in (
            ('framework', 'data/r1_framework_hit_stats.json'),
            ('six', 'data/selected_six_hit_stats.json'),
            ('he3', 'data/he3_refine_hit_stats.json')):
        have = _verified_periods(root, relative)
        missing = [period for period in reference if period not in have]
        switch_off, unexplained = [], []
        for period in missing:
            # 该期的预测在参考期(该期 slot 前 5 分钟)发起、在该期 slot 开奖；
            # 两个时刻任一处于「总闸关闭」都归因为总闸，避免关闸窗口尾部边界的
            # 缺期被误报成 unexplained。
            stamp = _stamp(period)
            attempt = stamp - timedelta(minutes=5) if stamp is not None else None
            if _switch_off(introduced, transitions, stamp) or _switch_off(introduced, transitions, attempt):
                switch_off.append(period)
            else:
                unexplained.append(period)
        alert = []
        for period in unexplained:
            stamp = _stamp(period)
            if stamp is None or stamp > deadlines:
                continue
            if watermark is not None and stamp < watermark:
                continue
            alert.append(period)
        status = 'fail' if len(alert) > 5 else 'warn' if alert else 'ok'
        models[key] = {'missing_count': len(missing), 'alert_count': len(alert),
                       'switch_off_count': len(switch_off),
                       'unexplained_count': len(unexplained),
                       'missing_periods': missing[-8:],
                       'switch_off_periods': switch_off[-8:],
                       'alert_periods': alert[-8:], 'status': status}
    return {'reference_count': len(reference), 'reference_source': reference_source,
            'models': models}


def _missing_legacy(root, now, watermark):
    killer = _read_json(root / 'data' / 'r1_stats.json', {}) or {}
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
    return {'reference_count': len(reference), 'reference_source': 'killer-legacy',
            'models': models}


def _missing(root, now, watermark):
    try:
        return _classified_missing(root, now, watermark)
    except Exception:
        return _missing_legacy(root, now, watermark)


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

    drift = {'status': 'unknown', 'generated_at': None, 'age_minutes': None,
             'stale': None, 'rows': []}
    cumulative = _read_json(root / DRIFT_PATH, {}) or {}
    drift_payload = cumulative.get('drift') if isinstance(cumulative, dict) else None
    if isinstance(drift_payload, dict) and isinstance(drift_payload.get('rows'), list):
        drift_rows = []
        for row in drift_payload['rows']:
            if not isinstance(row, dict):
                continue
            windows = row.get('windows') or {}
            cusum = row.get('cusum') or {}
            item = {'key': row.get('key'), 'label': row.get('label'), 'n': row.get('n'),
                    'status': str(row.get('status') or 'unknown'),
                    'verdict': row.get('verdict'),
                    'z200': (windows.get(200) or windows.get('200') or {}).get('z'),
                    'z300': (windows.get(300) or windows.get('300') or {}).get('z'),
                    'z500': (windows.get(500) or windows.get('500') or {}).get('z'),
                    'cusum_peak': cusum.get('z_peak')}
            drift_rows.append(item)
            if item['status'] == 'fail':
                raise_status('fail')
            elif item['status'] == 'warn':
                raise_status('warn')
        generated = drift_payload.get('generated_at') or cumulative.get('generated_at')
        age = _age_minutes(generated, checked_at)
        stale = age is None or age > DRIFT_STALE_MINUTES
        row_statuses = [row['status'] for row in drift_rows]
        if 'fail' in row_statuses:
            aggregate = 'fail'
        elif 'warn' in row_statuses or stale:
            aggregate = 'warn'
        else:
            aggregate = 'ok'
        drift = {'status': aggregate, 'generated_at': generated,
                 'age_minutes': age, 'stale': stale, 'rows': drift_rows}
        raise_status(aggregate)

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
             'drift': drift,
             'hitrate': rows, 'six': six, 'missing': missing,
             'balance': {'last_balance': last_balance, 'status': balance_status}}
    _atomic_json(state_path, state)
    if overall != 'ok' or previous.get('status') != overall:
        with (root / 'data' / LOG_NAME).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(state, ensure_ascii=False) + '\n')
    print(json.dumps({'status': overall, 'checked_at': state['checked_at'],
                      'drift': {'status': drift['status'], 'age_minutes': drift['age_minutes'],
                                'non_ok': [row['key'] for row in drift['rows']
                                           if row['status'] not in ('ok', 'insufficient', 'high', 'unknown')]},
                      'hitrate_non_ok': [row['key'] for row in rows if row['status'] not in ('ok', 'insufficient', 'unknown')],
                      'six': {'fallbacks_24h': six['fallbacks_24h'], 'failures_2h': six['final_failures_2h']},
                      'missing_alert': {key: value['alert_count'] for key, value in (missing.get('models') or {}).items()},
                      'missing_split': {key: {'switch_off': value.get('switch_off_count', 0),
                                              'unexplained': value.get('unexplained_count', 0),
                                              'alert': value['alert_count']}
                                        for key, value in (missing.get('models') or {}).items()},
                      'missing_reference': missing.get('reference_source'),
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
