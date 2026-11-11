"""Manual, durable AI egress switch. Missing or invalid control means stopped.

This is independent of hour/day: only an explicit successful enabled-state
transition allows the existing scheduled jobs to call providers. It never
starts jobs, reads credentials, or makes network requests itself.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import fcntl
import json
import os

from ai_prediction_guard import PublicationRejected
from ai_verified_stats import atomic_json, sync_parent_directory
from stats_history import TZ, beijing_now


ROOT = Path('/root/penta5-runner')
_PUBLIC_FIELDS = ('version', 'mode', 'enabled', 'generation', 'updated_at', 'introduced_at')
_FIELDS = frozenset(_PUBLIC_FIELDS + ('transitions', 'checksum'))
_MAX_BYTES = 16 * 1024 * 1024


class AICallsDisabled(PublicationRejected):
    """Fixed, non-sensitive stop reason, never a transient inference failure."""


def _path(root):
    return Path(root) / 'data/ai_api_control.json'


def _default():
    return {'version': 1, 'mode': 'manual', 'enabled': False, 'generation': 0,
            'updated_at': None, 'introduced_at': None, 'transitions': [], 'checksum': None}


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError('invalid-clock')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('invalid-clock')
    return moment


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate-control-key')
        result[key] = value
    return result


def _checksum(value):
    return sha256(_canonical({key: item for key, item in value.items() if key != 'checksum'})).hexdigest()


def _read_internal(root):
    try:
        with _path(root).open('rb') as stream:
            raw = stream.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return _default()
    except OSError:
        raise AICallsDisabled('ai-api-control-unavailable') from None
    try:
        if len(raw) > _MAX_BYTES:
            raise ValueError('oversized-control')
        value = json.loads(raw, object_pairs_hook=_pairs)
        if (not isinstance(value, dict) or set(value) != _FIELDS
                or type(value['version']) is not int or value['version'] != 1
                or value['mode'] != 'manual' or type(value['enabled']) is not bool
                or type(value['generation']) is not int or value['generation'] < 1
                or not isinstance(value['transitions'], list)
                or len(value['transitions']) != value['generation']
                or value['checksum'] != _checksum(value)):
            raise ValueError('invalid-control-schema')
        introduced, updated = _stamp(value['introduced_at']), _stamp(value['updated_at'])
        previous = introduced
        for generation, row in enumerate(value['transitions'], 1):
            if (not isinstance(row, dict) or set(row) != {'generation', 'enabled', 'at'}
                    or type(row['generation']) is not int or row['generation'] != generation
                    or type(row['enabled']) is not bool):
                raise ValueError('invalid-transition')
            moment = _stamp(row['at'])
            if moment < previous:
                raise ValueError('unordered-transitions')
            previous = moment
        first, last = value['transitions'][0], value['transitions'][-1]
        if (first['at'] != value['introduced_at'] or last['at'] != value['updated_at']
                or last['enabled'] != value['enabled'] or introduced > updated):
            raise ValueError('inconsistent-transitions')
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise AICallsDisabled('ai-api-control-invalid') from None
    return deepcopy(value)


def read_control_history(root=ROOT):
    """Return an independently validated deep copy of committed state/history.

    Intent audit lines are deliberately excluded: a financial caller must only
    use history committed in the same atomic state transaction as the switch.
    """
    path = _path(root)
    if not path.exists():
        return _default()
    try:
        with Path(str(path) + '.lock').open('a') as lock:
            fcntl.flock(lock, getattr(fcntl, 'LOCK_SH', fcntl.LOCK_EX))
            return _read_internal(root)
    except AICallsDisabled:
        raise
    except OSError:
        raise AICallsDisabled('ai-api-control-unavailable') from None


def read_control(root=ROOT):
    """Return only six public fields, never the internal committed intervals."""
    document = read_control_history(root)
    return {key: document[key] for key in _PUBLIC_FIELDS}


def require_ai_enabled(*, root=ROOT, clock=beijing_now, scope=None):
    """Re-read immediately before each egress attempt; do not cache this result.

    ``clock`` is accepted for the shared caller-injection API. The manual switch
    deliberately never consults the hour, so 08:00 cannot turn it back on.

    ``scope``（或环境变量 PENTA5_AI_SCOPE）用于按框架隔离：全局开关之外，
    再叠加该作用域自己的开关。未设置作用域时行为与旧版完全一致。
    """
    result = read_control(root)
    if not result['enabled']:
        raise AICallsDisabled('ai-api-stopped')
    scope = (scope or os.getenv('PENTA5_AI_SCOPE') or '').strip().lower()
    if scope:
        try:
            from ai_scope import require_scope_enabled
        except Exception:          # 作用域层缺失时不影响全局开关语义
            return result
        require_scope_enabled(scope, clock=clock)
    return result


def _audit(root, previous, current, *, event='control-transition-intent'):
    """Durable metadata-only intent; state digest distinguishes commit/failure."""
    record = {'version': 1, 'event': event, 'mode': 'manual',
              'previous_enabled': previous['enabled'], 'previous_generation': previous['generation'],
              'enabled': current['enabled'], 'generation': current['generation'],
              'updated_at': current['updated_at'],
              'control_sha256': sha256(_canonical(current)).hexdigest()}
    path = Path(root) / 'data/ai_api_control_audit.jsonl'
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(descriptor, 0o600)
        raw = _canonical(record) + b'\n'
        while raw:
            written = os.write(descriptor, raw)
            if written <= 0:
                raise OSError('incomplete-audit-write')
            raw = raw[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sync_parent_directory(path)


def set_enabled(enabled, expected_generation, *, root=ROOT, clock=beijing_now):
    """CAS one explicit control action under a cross-process lock.

    A valid action increments generation even if the flag already has that
    value. Audit must be durable before enabling. A stop still commits the
    disabled flag if its audit fails, then reports that audit failure.
    """
    if type(enabled) is not bool:
        raise ValueError('invalid-ai-api-enabled')
    if type(expected_generation) is not int or expected_generation < 0:
        raise ValueError('invalid-ai-api-expected-generation')
    try:
        moment = clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError('invalid-clock')
        updated = moment.astimezone(TZ).isoformat()
    except Exception:
        raise ValueError('invalid-ai-api-control-clock') from None
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = _read_internal(root)
        if previous['generation'] != expected_generation:
            raise ValueError('ai-api-control-generation-conflict')
        if previous['updated_at'] is not None and moment < _stamp(previous['updated_at']):
            raise ValueError('ai-api-control-clock-moved-backward')
        current = {'version': 1, 'mode': 'manual', 'enabled': enabled,
                   'generation': previous['generation'] + 1, 'updated_at': updated,
                   'introduced_at': previous['introduced_at'] or updated,
                   'transitions': previous['transitions'] + [
                       {'generation': previous['generation'] + 1, 'enabled': enabled, 'at': updated}]}
        current['checksum'] = _checksum(current)
        audit_failed = False
        try:
            _audit(root, previous, current)
        except OSError:
            if enabled:
                raise AICallsDisabled('ai-api-control-audit-unavailable') from None
            audit_failed = True
        try:
            atomic_json(path, current)
        except (OSError, ValueError, TypeError):
            # A directory fsync can fail after replacement. Never leave a
            # failed "start" response with a newly enabled state behind it.
            if enabled:
                fallback = deepcopy(current)
                fallback['enabled'] = False
                fallback['transitions'][-1]['enabled'] = False
                fallback['checksum'] = _checksum(fallback)
                try:
                    atomic_json(path, fallback)
                except (OSError, ValueError, TypeError):
                    pass
            raise AICallsDisabled('ai-api-control-write-unavailable') from None
        if audit_failed:
            raise AICallsDisabled('ai-api-control-audit-unavailable')
        return {key: deepcopy(current[key]) for key in _PUBLIC_FIELDS}
