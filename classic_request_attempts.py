"""Durable same-reference payment guards for the two classic Flash jobs.

The immutable input archive proves what was asked, not whether a request was
already paid. A separate daily ledger reserves each job/reference before the
client runs. Internal retries use one reservation; restarts cannot buy another
request after a sent, uncertain, failed, or otherwise unfinished invocation.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from ai_call_control import AICallsDisabled, require_ai_enabled
from ai_prediction_guard import PublicationRejected
from ai_verified_stats import atomic_json, next_period
from stats_history import TZ, beijing_now, business_start, parse_period, period_slot


ROOT = Path('/root/penta5-runner')
VERSION = 1
KINDS = ('killer', 'framework')
_MAX_BYTES = 2 * 1024 * 1024
_HASH = re.compile(r'[0-9a-f]{64}')
_OWNER = re.compile(r'[0-9a-f]{32}')
_FIELDS = frozenset(('owner', 'reference_period', 'target_period', 'started_at', 'input_sha256'))


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _aware(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise PublicationRejected('classic-attempt-invalid-clock') from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PublicationRejected('classic-attempt-invalid-clock')
    return value.astimezone(TZ)


def _period(value):
    parsed = parse_period(value) if isinstance(value, str) else None
    if not parsed or parsed[0] != value:
        raise PublicationRejected('classic-attempt-invalid-reference')
    return value


def _day(reference):
    return business_start(period_slot(next_period(_period(reference)))).strftime('%Y-%m-%d')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate-key')
        result[key] = value
    return result


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        try:
            import fcntl
        except ImportError:
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b'':
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _read(path, kind, day, now):
    if not path.exists():
        return {'version': VERSION, 'kind': kind, 'biz_date': day, 'attempts': {}}
    try:
        with path.open('rb') as handle:
            raw = handle.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError('oversized-ledger')
        document = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(document, dict):
            raise ValueError('invalid-ledger')
        checksum = document.pop('checksum', None)
        if (set(document) != {'version', 'kind', 'biz_date', 'attempts'}
                or type(document['version']) is not int or document['version'] != VERSION
                or document['kind'] != kind or document['biz_date'] != day
                or checksum != _digest(document)
                or not isinstance(document['attempts'], dict) or len(document['attempts']) > 288):
            raise ValueError('invalid-ledger')
        for reference, row in document['attempts'].items():
            if (not isinstance(row, dict) or set(row) != _FIELDS
                    or row['reference_period'] != _period(reference)
                    or row['target_period'] != next_period(reference) or _day(reference) != day
                    or not isinstance(row['owner'], str) or _OWNER.fullmatch(row['owner']) is None
                    or not isinstance(row['input_sha256'], str) or _HASH.fullmatch(row['input_sha256']) is None):
                raise ValueError('invalid-attempt')
            started = _aware(row['started_at'])
            if not period_slot(reference) <= started <= now or started >= period_slot(row['target_period']) - timedelta(seconds=70):
                raise ValueError('invalid-attempt-time')
        return document
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, OSError, PublicationRejected):
        raise PublicationRejected('classic-attempt-ledger-invalid') from None


def _write(path, document):
    body = deepcopy(document)
    body.pop('checksum', None)
    atomic_json(path, dict(body, checksum=_digest(body)))


def _release(path, lock, kind, day, reference, owner, clock):
    with _lock(lock):
        document = _read(path, kind, day, _aware(clock()))
        current = document['attempts'].get(reference)
        if current is not None and current['owner'] == owner:
            document['attempts'].pop(reference)
            _write(path, document)


@contextmanager
def reserve_request(kind, reference, *, post, input_sha256, root=ROOT, clock=beijing_now):
    """Reserve once, yielding the only POST callable passed to the AI client.

    Only a manual stop with two independent zero-send observations can release
    this invocation's owned reservation. Every other exit retains the guard.
    A saved prepared or published seal must be resumed before entering here.
    """
    if kind not in KINDS or not callable(post):
        raise PublicationRejected('classic-attempt-invalid-kind')
    reference, now = _period(reference), _aware(clock())
    if not isinstance(input_sha256, str) or _HASH.fullmatch(input_sha256) is None:
        raise PublicationRejected('classic-attempt-invalid-input-hash')
    if not period_slot(reference) <= now < period_slot(next_period(reference)) - timedelta(seconds=70):
        raise PublicationRejected('classic-attempt-outside-operation-window')
    require_ai_enabled(root=root, clock=clock)
    day = _day(reference)
    path = Path(root) / 'data' / ('ai_classic_attempts_' + kind + '_' + day + '.json')
    lock = Path(str(path) + '.lock')
    owner, observed_posts = uuid4().hex, 0
    with _lock(lock):
        document = _read(path, kind, day, now)
        if reference in document['attempts']:
            raise PublicationRejected('classic-' + kind + '-already-attempted')
        document['attempts'][reference] = {'owner': owner, 'reference_period': reference,
            'target_period': next_period(reference), 'started_at': now.isoformat(),
            'input_sha256': input_sha256}
        _write(path, document)  # Must commit before yielding any callable that can POST.

    def counted_post(*args, **kwargs):
        nonlocal observed_posts
        require_ai_enabled(root=root, clock=clock)
        observed_posts += 1  # Includes transport outcomes that cannot be proved unsent.
        return post(*args, **kwargs)

    try:
        yield counted_post
    except AICallsDisabled as error:
        reported = getattr(error, 'requests_sent', 1)
        reported = reported if type(reported) is int and reported >= 0 else 1
        if observed_posts == 0 and reported == 0:
            try:
                _release(path, lock, kind, day, reference, owner, clock)
            except Exception:
                pass  # Failed proof or cleanup retains the conservative tombstone.
        raise
