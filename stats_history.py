"""Validated draw history for daily and rolling statistics (Beijing time)."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import re
import tempfile

TZ = ZoneInfo('Asia/Shanghai')
STATE_PATH = Path('/root/penta5-runner/data/state.json')
CACHE_PATH = Path('/root/penta5-runner/data/stats_draw_history.json')


def parse_period(value):
    """Official YYYYMMDD001..288 identity; reject coercions and Unicode digits."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not re.fullmatch(r'[0-9]{11}', text):
        return None
    try:
        day = datetime.strptime(text[:8], '%Y%m%d').replace(tzinfo=TZ)
    except ValueError:
        return None
    index = int(text[8:])
    return (text, day, index) if 1 <= index <= 288 else None


def period_slot(value):
    """Earliest opening minute. Slot 001=00:05; 288=next midnight."""
    parsed = parse_period(value)
    return parsed[1] + timedelta(minutes=parsed[2] * 5) if parsed else None


def validate_conflicting_periods(values):
    """Carry explicit rejected identities; absent history alone is not a conflict."""
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError('Conflicting periods must be a sequence of canonical period strings')
    result = set()
    for value in values:
        parsed = parse_period(value)
        if not isinstance(value, str) or not parsed or value != parsed[0]:
            raise ValueError('Invalid conflicting period identity')
        result.add(value)
    return result


def valid_draw_record(period, numbers, opened, now=None):
    parsed = parse_period(period)
    digits = valid_numbers(numbers)
    time = parse_time(opened)
    slot = period_slot(period)
    current = now or beijing_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    if (not parsed or digits is None or time is None or time > current
            or not slot <= time < slot + timedelta(minutes=1)):
        return None
    return {'period': parsed[0], 'numbers': digits, 'opentime': time.isoformat()}


def beijing_now():
    return datetime.now(TZ)


def business_start(now=None):
    now = now or beijing_now()
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    start = now.replace(hour=5, minute=0, second=0, microsecond=0)
    return start if now >= start else start - timedelta(days=1)


def parse_time(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, TZ)
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip().replace('/', '-').replace('Z', '+00:00')
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)
    except (ValueError, OverflowError, OSError):
        return None


def valid_numbers(value):
    if not isinstance(value, (list, tuple)) or len(value) not in (4, 5):
        return None
    digits = []
    for number in value:
        if isinstance(number, bool):
            return None
        if isinstance(number, int) and 0 <= number <= 9:
            digits.append(number)
        elif isinstance(number, str) and len(number.strip()) == 1 and number.strip() in '0123456789':
            digits.append(int(number))
        else:
            return None
    return digits[:4]


def collect_records(state, cached=(), now=None, *, conflicts_out=None, conflicting_periods=()):
    now = now or beijing_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    records = {}
    conflicts = validate_conflicting_periods(conflicting_periods)
    rejected = 0

    if not isinstance(state, dict):
        raise ValueError('Draw state must be an object')
    if not isinstance(cached, (list, tuple)):
        cached = []
        rejected += 1

    def add(period, numbers, opened):
        nonlocal rejected
        record = valid_draw_record(period, numbers, opened, now)
        if record is None:
            rejected += 1
            return
        period = record['period']
        if period in conflicts:
            rejected += 1
            return
        previous = records.get(period)
        if previous and previous['numbers'] != record['numbers']:
            conflicts.add(period)
            records.pop(period, None)
            rejected += 1
            return
        records[period] = record

    for record in cached:
        if isinstance(record, dict):
            add(record.get('period'), record.get('numbers'), record.get('opentime'))
    predictions = state.get('predictions', [])
    history = state.get('history', [])
    if not isinstance(predictions, list) or not isinstance(history, list):
        raise ValueError('Draw predictions/history must be lists')
    for prediction in predictions:
        ev = prediction.get('evaluation') if isinstance(prediction, dict) else None
        if not isinstance(ev, dict):
            continue
        add(ev.get('targetPeriod') or prediction.get('targetPeriod') or prediction.get('period'),
            ev.get('actualFirstFourDigits') or ev.get('actualNumbers'),
            ev.get('targetOpenTime') or ev.get('opentime') or ev.get('drawTime'))
    for record in history:
        if isinstance(record, dict):
            add(record.get('period') or record.get('issue'), record.get('numbers'),
                record.get('opentime') or record.get('openTime') or record.get('drawTime'))
    latest = state.get('latestRecord', {})
    if isinstance(latest, dict) and latest:
        add(latest.get('period') or latest.get('issue'), latest.get('numbers'),
            latest.get('opentime') or latest.get('openTime') or latest.get('drawTime'))
    if conflicts_out is not None:
        if not isinstance(conflicts_out, set):
            raise ValueError('Conflicting period output must be a set')
        conflicts_out.update(conflicts)
    return sorted(records.values(), key=lambda r: (parse_time(r['opentime']), r['period']))[-800:], rejected


def load_snapshot(state_path=STATE_PATH, cache_path=CACHE_PATH, now=None, persist=True):
    now = now or beijing_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    # A malformed/truncated source must fail visibly rather than become an empty day.
    state = json.loads(Path(state_path).read_text(encoding='utf-8'))
    cached, cached_conflicts = [], set()
    if cache_path and Path(cache_path).exists():
        cached_document = json.loads(Path(cache_path).read_text(encoding='utf-8'))
        if (not isinstance(cached_document, dict)
                or not isinstance(cached_document.get('records'), list)
                or ('version' in cached_document
                    and (type(cached_document['version']) is not int or cached_document['version'] != 1))):
            raise ValueError('Invalid draw-history cache')
        cached = cached_document['records']
        if any(not isinstance(row, dict)
               or valid_draw_record(row.get('period'), row.get('numbers'), row.get('opentime'), now) is None
               for row in cached):
            raise ValueError('Invalid cached draw record')
        cached_conflicts = validate_conflicting_periods(cached_document.get('conflicting_periods', ()))
    conflicts = set()
    records, rejected = collect_records(state, cached, now, conflicts_out=conflicts,
                                        conflicting_periods=cached_conflicts)
    # Expire a conflict only after 800 newer real clean draws displace it. A
    # wall-clock outage must not erase ambiguity; never restore a rejected copy
    # when pruning the flags after the clean record window has been selected.
    if len(records) >= 800:
        oldest = min(period_slot(row['period']) for row in records)
        conflicts = {period for period in conflicts if period_slot(period) >= oldest}
    if persist and cache_path:
        payload = {'version': 1, 'updatedAt': now.isoformat(), 'records': records,
                   'conflicting_periods': sorted(conflicts)}
        target = Path(cache_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as f:
                temporary = f.name
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, target)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    start = business_start(now)
    daily = [r for r in records if start <= parse_time(r['opentime']) <= now]
    recent = records[-50:]
    return {
        'state': state, 'now': now, 'start': start,
        'date_label': f"今日 {start:%Y-%m-%d} (05:00起)",
        'daily': [(r['period'], r['numbers']) for r in daily],
        'recent': [(r['period'], r['numbers']) for r in recent],
        'records': records, 'rejected': rejected, 'conflicting_periods': sorted(conflicts),
        'latest': records[-1] if records else None,
    }
