"""只读模型运行观测：从各 AI 任务日志收集逐次调用证据并派生汇总。

数据来源（进程日志，不额外发 HTTP 请求，不改任何业务账本）：
- AI_HTTP_PHASE   请求边界与 HTTP 状态码（start/complete + at + status）
- AI_RESPONSE_META 已接受响应的 usage（prompt/completion/cache hit/miss/reasoning）
- AI_RETRY_META   重试事件（http_status/reason/delay）
- "attempt N/3 rejected; error_type=..."  无响应时的失败分类

产物（全部是派生数据，位于 data/model_usage/，可随时删除重建）：
- ledger-YYYYMM.jsonl  逐事件追加账本（只读日志，不写业务账本）
- state.json           日志读取位点与去重集合
- balance.jsonl        DeepSeek 余额快照（读 ai_balance_guard 状态，不额外调接口）
- summary.json         今日/昨日/近7日汇总 + 健康评估

口径：只统计已经落盘到日志的调用；没有日志证据的请求不估算。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import os
import re
import tempfile

TZ = timezone(timedelta(hours=8))
ROOT = Path('/root/penta5-runner')
USAGE_DIR = 'data/model_usage'

# 只有这 7 个日志当前会出现 AI_* 观测行；新增 AI 任务需要在这里补一行。
SOURCE_LOGS = (
    ('framework', 'data/ai_job_framework.log'),
    ('killer', 'data/ai_job_killer.log'),
    ('six', 'data/selected_six_cron.log'),
    ('he3', 'data/he3_refine_cron.log'),
    ('kill3', 'data/kill3_tail_cron.log'),
    ('kill1', 'data/kill1_tail_cron.log'),
    ('kill2p', 'data/kill2p_tail_cron.log'),
)

MAX_READ_BYTES = 4 * 1024 * 1024
MAX_EVENT_AGE_DAYS = 8
MAX_SEEN_KEYS = 12000
MAX_BALANCE_SAMPLES = 20000
PENDING_ATTEMPT_SECONDS = 180

REF_RE = re.compile(r'^[0-9]{11}$')
MODEL_RE = re.compile(r'^[A-Za-z0-9._:-]{1,64}$')
REJECT_RE = re.compile(r'attempt ([1-3])/3 rejected; error_type=([A-Za-z]{1,40})')
TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens',
                'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')


def _now(clock=None):
    value = clock() if callable(clock) else datetime.now(TZ)
    if not isinstance(value, datetime):
        value = datetime.now(TZ)
    if value.tzinfo is None:
        value = value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def _parse_stamp(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(TZ)


def _biz_day(stamp):
    """统计日：北京时间 05:00 起（与全站业务日一致）。"""
    return (stamp - timedelta(hours=5)).strftime('%Y-%m-%d')


def _finite_int(value, maximum=9007199254740991):
    if type(value) is int and 0 <= value <= maximum:
        return value
    return None


def _finite_number(value, maximum=86400 * 10):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return round(float(value), 3) if 0 <= value <= maximum else None
    return None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            if hasattr(os, 'fchmod'):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_text(path, lines):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            if hasattr(os, 'fchmod'):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(''.join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _append_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def _read_jsonl(path, limit=0):
    rows = []
    try:
        with Path(path).open('r', encoding='utf-8') as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        return rows
    if limit and len(rows) > limit:
        return rows[-limit:]
    return rows


def _model(value):
    if isinstance(value, str) and MODEL_RE.match(value):
        return value
    return None


def _event_key(event):
    kind = event.get('kind')
    source = event.get('source')
    ref = event.get('ref')
    attempt = event.get('attempt')
    if kind == 'http':
        return 'http|%s|%s|%s|%s|%s' % (source, ref, attempt, event.get('phase'), event.get('at'))
    if kind == 'response':
        return 'response|%s|%s|%s' % (source, ref, attempt)
    if kind == 'retry':
        return 'retry|%s|%s|%s|%s|%s|%s' % (source, ref, attempt, event.get('http_status'),
                                            event.get('reason'), event.get('delay'))
    if kind == 'reject':
        return 'reject|%s|%s|%s|%s' % (source, ref, attempt, event.get('error_type'))
    return 'other|' + json.dumps(event, sort_keys=True, ensure_ascii=True)


def _parse_line(source, line, active_ref, collected_at):
    """Return (event, new_active_ref). Only fixed public fields are kept."""
    if not isinstance(line, str) or not line:
        return None, active_ref
    if line.startswith('AI_HTTP_PHASE '):
        try:
            value = json.loads(line.split(' ', 1)[1])
        except (ValueError, TypeError):
            return None, active_ref
        if not isinstance(value, dict):
            return None, active_ref
        ref, attempt = value.get('ref'), value.get('attempt')
        if not isinstance(ref, str) or not REF_RE.match(ref):
            return None, active_ref
        if type(attempt) is not int or not 1 <= attempt <= 3:
            return None, active_ref
        phase = value.get('phase')
        if phase not in ('start', 'complete'):
            return None, ref
        status = value.get('status')
        status = status if type(status) is int and 100 <= status <= 599 else None
        stamp = _parse_stamp(value.get('at'))
        return ({'kind': 'http', 'ref': ref, 'attempt': attempt, 'phase': phase,
                 'at': stamp.isoformat() if stamp else None, 'status': status}, ref)
    if line.startswith('AI_RESPONSE_META '):
        try:
            value = json.loads(line.split(' ', 1)[1])
        except (ValueError, TypeError):
            return None, active_ref
        if not isinstance(value, dict):
            return None, active_ref
        ref, attempt = value.get('ref'), value.get('attempt')
        if not isinstance(ref, str) or not REF_RE.match(ref):
            return None, active_ref
        if type(attempt) is not int or not 1 <= attempt <= 3:
            return None, ref
        raw_usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
        usage = {}
        for field in TOKEN_FIELDS:
            number = _finite_int(raw_usage.get(field))
            if number is not None:
                usage[field] = number
        reasoning = _finite_int(raw_usage.get('reasoning_tokens'))
        if reasoning is not None:
            usage['reasoning_tokens'] = reasoning
        if not usage:
            return None, ref
        finish = value.get('finish_reason')
        finish = finish if finish in ('stop', 'length', 'tool_calls', 'content_filter',
                                      'insufficient_system_resource') else None
        event = {'kind': 'response', 'ref': ref, 'attempt': attempt,
                 'requested_model': _model(value.get('requested_model')),
                 'returned_model': _model(value.get('returned_model')),
                 'finish_reason': finish, 'usage': usage,
                 'elapsed': _finite_number(value.get('elapsed_seconds'), 3600),
                 'max_tokens': _finite_int(value.get('max_tokens'), 1048576)}
        return event, ref
    if line.startswith('AI_RETRY_META '):
        try:
            value = json.loads(line.split(' ', 1)[1])
        except (ValueError, TypeError):
            return None, active_ref
        if not isinstance(value, dict):
            return None, active_ref
        ref, attempt = value.get('ref'), value.get('attempt')
        if not isinstance(ref, str) or not REF_RE.match(ref):
            return None, active_ref
        if type(attempt) is not int or not 1 <= attempt <= 3:
            return None, ref
        status = value.get('http_status')
        status = status if type(status) is int and 100 <= status <= 599 else None
        reason = value.get('reason')
        reason = reason[:40] if isinstance(reason, str) else None
        return ({'kind': 'retry', 'ref': ref, 'attempt': attempt, 'http_status': status,
                 'reason': reason, 'delay': _finite_number(value.get('delay_seconds'), 600),
                 'remaining': _finite_number(value.get('remaining_seconds'), 3600)}, ref)
    match = REJECT_RE.search(line)
    if match:
        return ({'kind': 'reject', 'ref': active_ref, 'attempt': int(match.group(1)),
                 'error_type': match.group(2)}, active_ref)
    return None, active_ref


def _read_log_lines(path, entry):
    """Read new complete lines since the recorded byte offset (bounded)."""
    try:
        stat = os.stat(path)
    except OSError:
        return [], entry
    entry = entry if isinstance(entry, dict) else {}
    offset = entry.get('offset') if entry.get('inode') == stat.st_ino else 0
    if type(offset) is not int or offset < 0 or stat.st_size < offset:
        offset = 0
    try:
        with open(path, 'rb') as stream:
            stream.seek(offset)
            chunk = stream.read(MAX_READ_BYTES)
    except OSError:
        return [], entry
    text = str(entry.get('pending') or '') + chunk.decode('utf-8', 'replace')
    lines = text.split('\n')
    pending = lines.pop() if lines else ''
    new_entry = {'inode': stat.st_ino, 'offset': offset + len(chunk), 'pending': pending}
    return [line for line in lines if line], new_entry


def _parse_seen(value):
    result = {}
    if isinstance(value, dict):
        for key in value:
            if isinstance(key, str):
                result[key] = 1
    return result


def collect(*, root=ROOT, clock=None):
    """Tail the AI logs into the derived ledger. No provider calls, no business writes."""
    root = Path(root)
    now = _now(clock)
    usage = root / USAGE_DIR
    usage.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = _read_json(usage / 'state.json')
    state = state if isinstance(state, dict) else {}
    files = state.get('files') if isinstance(state.get('files'), dict) else {}
    seen = _parse_seen(state.get('seen'))
    seeding = not bool(state.get('seeded'))
    collected_at = now.isoformat(timespec='seconds')
    candidates = []
    for source, rel in SOURCE_LOGS:
        if seeding:
            candidates.append((source, rel + '.1'))
        candidates.append((source, rel))
    added, logs_read, errors = [], [], []
    for source, rel in candidates:
        path = root / rel
        if not path.is_file():
            continue
        entry = files.get(rel) if isinstance(files.get(rel), dict) else {}
        try:
            lines, new_entry = _read_log_lines(path, entry)
        except (OSError, ValueError, TypeError) as error:
            errors.append({'file': rel, 'error': type(error).__name__})
            continue
        files[rel] = new_entry
        logs_read.append(rel)
        active_ref = None
        for line in lines:
            try:
                event, active_ref = _parse_line(source, line, active_ref, collected_at)
            except (ValueError, TypeError, OverflowError):
                continue
            if not event:
                continue
            event['source'] = source
            event['collected_at'] = collected_at
            key = _event_key(event)
            if key in seen:
                continue
            seen[key] = 1
            added.append(event)
        while len(seen) > MAX_SEEN_KEYS:
            seen.pop(next(iter(seen)))
    if added:
        _append_jsonl(usage / ('ledger-%s.jsonl' % now.strftime('%Y%m')), added)
    new_state = {'version': 1, 'seeded': True, 'updated_at': collected_at,
                 'files': files, 'seen': seen}
    _atomic_json(usage / 'state.json', new_state)
    return {'status': 'ok', 'events_added': len(added), 'logs_read': len(logs_read),
            'seeding': seeding, 'errors': errors[:5], 'collected_at': collected_at}


def _ref_times(root):
    mapping = {}
    document = _read_json(Path(root) / 'data' / 'stats_draw_history.json')
    records = document.get('records') if isinstance(document, dict) else None
    if isinstance(records, list):
        for row in records:
            if not isinstance(row, dict):
                continue
            period = row.get('period')
            opened = _parse_stamp(row.get('opentime'))
            if isinstance(period, str) and REF_RE.match(period) and opened is not None:
                mapping[period] = opened
    return mapping


def _read_events(root, now):
    usage = Path(root) / USAGE_DIR
    cutoff = now - timedelta(days=MAX_EVENT_AGE_DAYS + 1)
    paths = sorted(usage.glob('ledger-*.jsonl'))[-2:]
    events, errors = {}, []
    for path in paths:
        try:
            for row in _read_jsonl(path):
                if not isinstance(row, dict):
                    continue
                collected = _parse_stamp(row.get('collected_at'))
                if collected is not None and collected < cutoff:
                    continue
                key = _event_key(row)
                if key not in events:
                    events[key] = row
        except (OSError, ValueError, TypeError):
            errors.append(path.name)
    return list(events.values()), errors


def _row():
    return {'attempts': 0, 'responses': 0, 'pending': 0, 'retries': 0, 'mismatch': 0,
            'errors': {}, 'prompt': 0, 'completion': 0, 'reasoning': 0,
            'cache_hit': 0, 'cache_miss': 0, 'latency': [],
            'by_model': {}, 'by_task': {}}


def _group_row():
    return {'attempts': 0, 'responses': 0, 'errors': 0, 'prompt': 0,
            'completion': 0, 'cache_hit': 0, 'cache_miss': 0, 'latency': []}


def _group(row, name):
    value = row['by_model'].get(name)
    if value is None:
        value = _group_row()
        row['by_model'][name] = value
    return value


def _task_group(row, name):
    value = row['by_task'].get(name)
    if value is None:
        value = _group_row()
        row['by_task'][name] = value
    return value


def _outcome(record, now):
    if record.get('response') is not None:
        return 'accepted'
    status = record.get('status')
    if isinstance(status, int):
        if status == 402:
            return 'payment'
        if status == 429:
            return 'rate_limit'
        if 500 <= status <= 599:
            return 'provider'
        if 200 <= status <= 299:
            return 'invalid_response'
        return 'http_other'
    error = (record.get('reject') or '').lower()
    if 'timeout' in error or 'deadline' in error:
        return 'timeout'
    if 'strategy' in error:
        return 'strategy'
    started = record.get('start')
    if started is not None and (now - started).total_seconds() > PENDING_ATTEMPT_SECONDS:
        return 'timeout'
    return 'pending'


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return round(ordered[low], 3)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def _finalize_group(group):
    latency = group['latency']
    cache_base = group['cache_hit'] + group['cache_miss']
    return {'attempts': group['attempts'], 'responses': group['responses'],
            'errors': group['errors'], 'prompt': group['prompt'],
            'completion': group['completion'], 'cache_hit': group['cache_hit'],
            'cache_miss': group['cache_miss'],
            'cache_rate': round(group['cache_hit'] / cache_base, 4) if cache_base else None,
            'p50': _quantile(latency, 0.5), 'p95': _quantile(latency, 0.95)}


def _finalize_row(row):
    latency = row['latency']
    cache_base = row['cache_hit'] + row['cache_miss']
    return {'attempts': row['attempts'], 'responses': row['responses'],
            'pending': row['pending'], 'retries': row['retries'], 'mismatch': row['mismatch'],
            'errors': dict(sorted(row['errors'].items())),
            'error_total': sum(row['errors'].values()),
            'tokens': {'prompt': row['prompt'], 'completion': row['completion'],
                       'reasoning': row['reasoning'], 'cache_hit': row['cache_hit'],
                       'cache_miss': row['cache_miss'],
                       'cache_rate': round(row['cache_hit'] / cache_base, 4) if cache_base else None},
            'latency': {'n': len(latency), 'p50': _quantile(latency, 0.5),
                        'p95': _quantile(latency, 0.95),
                        'max': round(max(latency), 3) if latency else None},
            'by_model': {name: _finalize_group(group) for name, group in
                         sorted(row['by_model'].items())},
            'by_task': {name: _finalize_group(group) for name, group in
                        sorted(row['by_task'].items())}}


def _merge_row(target, source):
    for field in ('attempts', 'responses', 'pending', 'retries', 'mismatch',
                  'prompt', 'completion', 'reasoning', 'cache_hit', 'cache_miss'):
        target[field] += source[field]
    for name, count in source['errors'].items():
        target['errors'][name] = target['errors'].get(name, 0) + count
    target['latency'].extend(source['latency'])
    for bucket, target_bucket in (('by_model', target['by_model']), ('by_task', target['by_task'])):
        for name, group in source[bucket].items():
            merged = target_bucket.get(name)
            if merged is None:
                merged = _group_row()
                target_bucket[name] = merged
            for field in ('attempts', 'responses', 'errors', 'prompt', 'completion',
                          'cache_hit', 'cache_miss'):
                merged[field] += group[field]
            merged['latency'].extend(group['latency'])


def _aggregate(events, ref_times, now):
    known_sources = frozenset(source for source, _ in SOURCE_LOGS)
    events = [event for event in events if event.get('source') in known_sources]
    starts, records = {}, {}
    for event in events:
        kind = event.get('kind')
        ref, source, attempt = event.get('ref'), event.get('source'), event.get('attempt')
        if not isinstance(ref, str) or not REF_RE.match(ref) or not isinstance(source, str):
            continue
        if kind == 'http' and event.get('phase') == 'start':
            stamp = _parse_stamp(event.get('at'))
            key = (source, ref, attempt)
            if stamp is not None and (key not in starts or stamp < starts[key]):
                starts[key] = stamp
    for event in events:
        kind = event.get('kind')
        ref, source, attempt = event.get('ref'), event.get('source'), event.get('attempt')
        if not isinstance(ref, str) or not REF_RE.match(ref) or not isinstance(source, str):
            continue
        if type(attempt) is not int or not 1 <= attempt <= 3:
            continue
        key = (source, ref, attempt)
        record = records.get(key)
        if record is None:
            record = {'source': source, 'ref': ref, 'attempt': attempt,
                      'start': starts.get(key), 'status': None, 'response': None,
                      'retries': 0, 'reject': None}
            records[key] = record
        if kind == 'http' and event.get('phase') == 'complete':
            status = event.get('status')
            if isinstance(status, int):
                record['status'] = status
        elif kind == 'response':
            record['response'] = event
        elif kind == 'retry':
            record['retries'] += 1
        elif kind == 'reject':
            record['reject'] = event.get('error_type')
    days, unattributed = {}, 0
    for record in records.values():
        moment = record['start'] or ref_times.get(record['ref'])
        if moment is None:
            unattributed += 1
            continue
        day = _biz_day(moment)
        row = days.get(day)
        if row is None:
            row = _row()
            days[day] = row
        outcome = _outcome(record, now)
        if outcome == 'pending':
            row['pending'] += 1
            continue
        row['attempts'] += 1
        row['retries'] += record['retries']
        task = _task_group(row, record['source'])
        task['attempts'] += 1
        if outcome != 'accepted':
            row['errors'][outcome] = row['errors'].get(outcome, 0) + 1
            task['errors'] += 1
            continue
        response = record['response']
        usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
        prompt = _finite_int(usage.get('prompt_tokens')) or 0
        completion = _finite_int(usage.get('completion_tokens')) or 0
        reasoning = _finite_int(usage.get('reasoning_tokens')) or 0
        hit = _finite_int(usage.get('prompt_cache_hit_tokens')) or 0
        miss = _finite_int(usage.get('prompt_cache_miss_tokens')) or 0
        elapsed = response.get('elapsed')
        model = response.get('requested_model') or '(unknown)'
        if response.get('returned_model') and response.get('requested_model') \
                and response['returned_model'] != response['requested_model']:
            row['mismatch'] += 1
        row['responses'] += 1
        row['prompt'] += prompt
        row['completion'] += completion
        row['reasoning'] += reasoning
        row['cache_hit'] += hit
        row['cache_miss'] += miss
        if isinstance(elapsed, (int, float)):
            row['latency'].append(float(elapsed))
        group = _group(row, model)
        group['responses'] += 1
        group['prompt'] += prompt
        group['completion'] += completion
        group['cache_hit'] += hit
        group['cache_miss'] += miss
        if isinstance(elapsed, (int, float)):
            group['latency'].append(float(elapsed))
        task['responses'] += 1
        task['prompt'] += prompt
        task['completion'] += completion
        task['cache_hit'] += hit
        task['cache_miss'] += miss
        if isinstance(elapsed, (int, float)):
            task['latency'].append(float(elapsed))
    today = _biz_day(now)
    yesterday = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    last7 = _row()
    window_start = (now - timedelta(days=6)).date().isoformat()
    for day in sorted(days):
        if window_start <= day <= today:
            _merge_row(last7, days[day])
    return {'today': _finalize_row(days.get(today) or _row()),
            'yesterday': _finalize_row(days.get(yesterday) or _row()),
            'last7d': _finalize_row(last7),
            'days': {day: _finalize_row(days[day]) for day in sorted(days)[-10:]},
            'unattributed': unattributed}


def _balance(root, now):
    usage = Path(root) / USAGE_DIR
    guard = _read_json(Path(root) / 'data' / 'ai_balance_guard.json')
    guard = guard if isinstance(guard, dict) else {}
    available = guard.get('last_is_available')
    available = available if isinstance(available, bool) else None
    balance = guard.get('last_balance')
    balance = round(float(balance), 2) if isinstance(balance, (int, float)) and not isinstance(balance, bool) else None
    checked = _parse_stamp(guard.get('last_checked_at'))
    history_path = usage / 'balance.jsonl'
    samples = [row for row in _read_jsonl(history_path, MAX_BALANCE_SAMPLES)
               if isinstance(row, dict) and _parse_stamp(row.get('at')) is not None
               and isinstance(row.get('balance'), (int, float))]
    if balance is not None and checked is not None:
        last = samples[-1] if samples else None
        last_at = _parse_stamp(last.get('at')) if last else None
        if last_at is None or (checked > last_at
                               and (checked - last_at).total_seconds() >= 60):
            samples.append({'at': checked.isoformat(timespec='seconds'), 'balance': balance})
            _append_jsonl(history_path, [samples[-1]])
    try:
        if history_path.stat().st_size > 3 * 1024 * 1024:
            keep = samples[-MAX_BALANCE_SAMPLES:]
            _atomic_text(history_path, [json.dumps(row, ensure_ascii=False,
                                                   separators=(',', ':')) + '\n'
                                        for row in keep])
            samples = keep
    except (OSError, ValueError):
        pass
    samples.sort(key=lambda row: _parse_stamp(row['at']))

    def rate(hours):
        if len(samples) < 2:
            return None
        target = now - timedelta(hours=hours)
        base = min(samples, key=lambda row: abs((_parse_stamp(row['at']) - target).total_seconds()))
        base_at = _parse_stamp(base['at'])
        latest = samples[-1]
        latest_at = _parse_stamp(latest['at'])
        span = (latest_at - base_at).total_seconds() / 3600.0
        minimum_span = 0.1 if hours <= 1 else (1.0 if hours <= 6 else 4.0)
        if span < minimum_span:
            return None
        delta = float(latest['balance']) - float(base['balance'])
        if delta > 0.01:
            return {'recharged': True, 'cny_per_hour': None, 'span_hours': round(span, 3)}
        return {'recharged': False, 'cny_per_hour': round(-delta / span, 3),
                'span_hours': round(span, 3)}

    burn_1h, burn_6h, burn_24h = rate(1), rate(6), rate(24)
    runway = None
    if balance is not None and burn_1h and burn_1h.get('cny_per_hour'):
        if burn_1h['cny_per_hour'] > 0:
            runway = round(balance / burn_1h['cny_per_hour'], 2)
    return {'available': available, 'balance': balance,
            'at': checked.isoformat(timespec='seconds') if checked else None,
            'auto_paused': guard.get('auto_paused') if isinstance(guard.get('auto_paused'), bool) else None,
            'burn_1h_cny': (burn_1h or {}).get('cny_per_hour'),
            'burn_6h_cny': (burn_6h or {}).get('cny_per_hour'),
            'burn_24h_cny': (burn_24h or {}).get('cny_per_hour'),
            'burn_1h_span_hours': (burn_1h or {}).get('span_hours'),
            'burn_6h_span_hours': (burn_6h or {}).get('span_hours'),
            'burn_24h_span_hours': (burn_24h or {}).get('span_hours'),
            'runway_hours': runway,
            'samples': len(samples),
            'recharged_recently': bool((burn_1h or {}).get('recharged'))}


def _raise_status(current, target):
    rank = {'ok': 0, 'warn': 1, 'fail': 2}
    return target if rank.get(target, 0) > rank.get(current, 0) else current


def _health(summary):
    reasons = []
    status = 'ok'
    today = summary['today']
    attempts, responses = today['attempts'], today['responses']
    if attempts:
        ratio = today['error_total'] / attempts
        if ratio >= 0.4:
            status = _raise_status(status, 'fail')
            reasons.append('失败尝试占比 %.0f%%（%d/%d）' % (ratio * 100, today['error_total'], attempts))
        elif ratio >= 0.15:
            status = _raise_status(status, 'warn')
            reasons.append('失败尝试占比 %.0f%%（%d/%d）' % (ratio * 100, today['error_total'], attempts))
    if attempts and responses == 0:
        status = _raise_status(status, 'fail')
        reasons.append('今日 %d 次尝试没有一次成功响应' % attempts)
    payment = today['errors'].get('payment', 0)
    if payment:
        status = _raise_status(status, 'warn')
        reasons.append('HTTP 402（余额不足）%d 次' % payment)
    if today['mismatch']:
        status = _raise_status(status, 'warn')
        reasons.append('请求/返回模型不一致 %d 次' % today['mismatch'])
    p95 = today['latency'].get('p95')
    if isinstance(p95, (int, float)) and p95 > 120:
        status = _raise_status(status, 'warn')
        reasons.append('响应延迟 p95=%.1fs（>120s）' % p95)
    balance = summary.get('balance') or {}
    if balance.get('available') is False:
        status = _raise_status(status, 'warn')
        reasons.append('DeepSeek 余额接口不可用')
    elif isinstance(balance.get('balance'), (int, float)) and balance['balance'] < 3:
        status = _raise_status(status, 'warn')
        reasons.append('余额仅 %.2f CNY' % balance['balance'])
    runway = balance.get('runway_hours')
    if isinstance(runway, (int, float)) and runway < 2:
        status = _raise_status(status, 'warn')
        span = balance.get('burn_1h_span_hours')
        if isinstance(span, (int, float)) and span < 0.75:
            reasons.append('按近期扣费速率（约 %.0f 分钟窗口）余额仅够 %.1f 小时'
                           % (span * 60, runway))
        else:
            reasons.append('按近 1h 扣费速率余额仅够 %.1f 小时' % runway)
    return {'status': status, 'reasons': reasons[:8]}


def build_summary(*, root=ROOT, clock=None):
    root = Path(root)
    now = _now(clock)
    collection = collect(root=root, clock=lambda: now)
    events, read_errors = _read_events(root, now)
    ref_times = _ref_times(root)
    aggregation = _aggregate(events, ref_times, now)
    balance = _balance(root, now)
    summary = {
        'version': 1, 'revision': 'model-usage-v1',
        'updated_at': now.isoformat(timespec='seconds'),
        'biz_date': _biz_day(now),
        'business_start': _biz_day(now) + 'T05:00:00+08:00',
        'window_days': MAX_EVENT_AGE_DAYS,
        'sources': {'logs': collection['logs_read'], 'events_in_window': len(events),
                    'unattributed': aggregation['unattributed'],
                    'collection': collection, 'ledger_errors': read_errors},
        'today': aggregation['today'],
        'yesterday': aggregation['yesterday'],
        'last7d': aggregation['last7d'],
        'days': aggregation['days'],
        'balance': balance,
    }
    summary['health'] = _health(summary)
    _atomic_json(root / USAGE_DIR / 'summary.json', summary)
    return summary


def run(*, root=ROOT, clock=None, publish_panel=True):
    root = Path(root)
    summary = build_summary(root=root, clock=clock)
    result = {'status': 'ok', 'updated_at': summary['updated_at'],
              'biz_date': summary['biz_date'],
              'today': {'attempts': summary['today']['attempts'],
                        'responses': summary['today']['responses'],
                        'errors': summary['today']['error_total'],
                        'tokens': summary['today']['tokens']['prompt'] + summary['today']['tokens']['completion'],
                        'cache_rate': summary['today']['tokens']['cache_rate'],
                        'p95': summary['today']['latency']['p95']},
              'balance': summary['balance'],
              'health': summary['health'],
              'collection': summary['sources']['collection'],
              'unattributed': summary['sources']['unattributed']}
    if publish_panel:
        try:
            from model_usage_view import publish
            result['panel_changed'] = bool(publish(summary, root=root))
        except Exception as error:
            result['panel'] = {'status': 'error', 'error': type(error).__name__}
    return result
