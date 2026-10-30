#!/usr/bin/env python3
"""Server-side promotion gate for the A0 joint-form shadow.

Reads the shadow ledger; decides whether the live burn-in is good enough to
replace the official pattern selection. Never changes predictions itself.
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))
from stats_history import beijing_now

BURN_IN = 72          # >= 6 hours of live periods
STRONG = 288          # >= 1 day of live periods
MIN_LIVE_DELTA_PP = 0.0
MIN_BACKFILL_DELTA_PP = 2.0
MIN_STRONG_DELTA_PP = 1.5
STALE_MINUTES = 25


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _atomic(path, value):
    path = Path(path)
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


def _model_stats(records):
    live = [r for r in records.values() if isinstance(r, dict) and r.get('source') == 'live' and isinstance(r.get('evaluation'), dict)]
    backfill = [r for r in records.values() if isinstance(r, dict) and r.get('source') == 'backfill' and isinstance(r.get('evaluation'), dict)]
    def counts(rows):
        n = len(rows)
        ai = sum(1 for r in rows if r['evaluation'].get('ai_joint'))
        opt = sum(1 for r in rows if r['evaluation'].get('opt_joint'))
        return {'n': n, 'ai_hits': ai, 'opt_hits': opt,
                'ai_rate': round(ai / n, 6) if n else 0.0,
                'opt_rate': round(opt / n, 6) if n else 0.0,
                'delta_pp': round((opt - ai) / n * 100, 3) if n else 0.0}
    return counts(live), counts(backfill)


def main(argv=None):
    parser = argparse.ArgumentParser(description="A0 promotion gate")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root)
    shadow = _read(root / 'data/joint_form_shadow.json', {}) or {}
    now = beijing_now()
    status = {'version': 1, 'checked_at': now.isoformat(), 'status': 'waiting',
              'gate': {'burn_in_periods': BURN_IN, 'strong_periods': STRONG,
                       'min_live_delta_pp': MIN_LIVE_DELTA_PP,
                       'min_backfill_delta_pp': MIN_BACKFILL_DELTA_PP,
                       'min_strong_delta_pp': MIN_STRONG_DELTA_PP},
              'models': {}}
    updated = shadow.get('updated_at') if isinstance(shadow, dict) else None
    try:
        updated_at = datetime.fromisoformat(updated) if isinstance(updated, str) else None
    except (TypeError, ValueError):
        updated_at = None
    fresh = updated_at is not None and (now - updated_at) <= timedelta(minutes=STALE_MINUTES)
    if not fresh:
        status['status'] = 'waiting-shadow'
        _atomic(root / 'data/joint_form_promotion.json', status)
        print(json.dumps({'status': status['status'], 'fresh': False}, ensure_ascii=False))
        return 0
    ok = True
    strong = True
    for model in ('framework', 'killer'):
        records = ((shadow.get('models') or {}).get(model) or {}).get('records') or {}
        live, backfill = _model_stats(records)
        ready = (live['n'] >= BURN_IN and live['delta_pp'] >= MIN_LIVE_DELTA_PP
                 and backfill['n'] >= 500 and backfill['delta_pp'] >= MIN_BACKFILL_DELTA_PP)
        strong_ready = (live['n'] >= STRONG and live['delta_pp'] >= MIN_STRONG_DELTA_PP)
        ok = ok and ready
        strong = strong and strong_ready
        status['models'][model] = {'live': live, 'backfill': backfill,
                                   'ready': ready, 'strong_ready': strong_ready}
    if all(status['models'][m]['live']['n'] < BURN_IN for m in status['models']):
        status['status'] = 'burn-in'
    elif ok and strong:
        status['status'] = 'ready-strong'
    elif ok:
        status['status'] = 'ready'
    else:
        status['status'] = 'not-met'
    status['live_periods'] = min(status['models'][m]['live']['n'] for m in status['models'])
    status['strong'] = strong
    auto_path = root / 'data' / 'joint_form_auto_promote.json'
    auto_state = _read(auto_path, {}) or {}
    if isinstance(auto_state, dict) and auto_state.get('manual_disable') is True:
        status['auto_promoted'] = False
    elif isinstance(auto_state, dict) and auto_state.get('enabled') is True:
        status['auto_promoted'] = True
    elif status['status'] in ('ready', 'ready-strong'):
        _atomic(auto_path, {'version': 1, 'enabled': True, 'promoted_at': now.isoformat(),
                            'gate_status': status['status'], 'source': 'joint_form_promotion_gate'})
        status['auto_promoted'] = True
        with (root / 'data' / 'joint_form_promotion.log').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': 'auto-promote-enabled', 'at': now.isoformat(),
                                     'gate_status': status['status']}, ensure_ascii=False) + '\n')
    else:
        status['auto_promoted'] = False
    _atomic(root / 'data/joint_form_promotion.json', status)
    log_path = root / 'data/joint_form_promotion.log'
    try:
        previous = _read(root / 'data/joint_form_promotion_state.json', {}) or {}
    except Exception:
        previous = {}
    if previous.get('status') != status['status']:
        with log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(status, ensure_ascii=False) + '\n')
        _atomic(root / 'data/joint_form_promotion_state.json', {'status': status['status'], 'checked_at': status['checked_at']})
    print(json.dumps({'status': status['status'], 'live_periods': status.get('live_periods'),
                      'models': {m: {'live_n': status['models'][m]['live']['n'],
                                     'live_delta_pp': status['models'][m]['live']['delta_pp'],
                                     'backfill_delta_pp': status['models'][m]['backfill']['delta_pp'],
                                     'ready': status['models'][m]['ready']} for m in status['models']}},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
