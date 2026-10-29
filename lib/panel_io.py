"""Cooperating writers update only their fragment under a shared lock."""
from pathlib import Path
from datetime import datetime, timezone
import fcntl
import os
import tempfile
import json
import math
import threading
import time
import queue

PANEL_PATH = Path('/root/penta5-runner/panel.html')
PANEL_SLOW_SECONDS = 0.5
_PHASES = ('lock_wait', 'read', 'transform', 'commit', 'total')
_COUNTER_LOCK = threading.Lock()
_TIMING_TOTALS = {'calls': 0, 'changed': 0, 'unchanged': 0, 'failed': 0, 'timing_invalid': 0,
                  **{name + '_seconds': 0.0 for name in _PHASES}}
_DELIVERY_QUEUE = queue.Queue(maxsize=2)
_DELIVERY_THREAD = None
_DELIVERY_THREAD_LOCK = threading.Lock()
_SOURCES = frozenset(('__main__', 'ai_job_status', 'ai_prediction_guard',
    'framework_hit_refresh', 'he3_refine_view', 'killer_hit_view',
    'selected_framework_model',
    'selected_framework_view', 'selected_six_model', 'selected_six_view',
    'stat50_compare_view', 'stat50_position_view', 'strategy_comparison'))
_ERROR_TYPES = frozenset(('OSError', 'IOError', 'PermissionError', 'FileNotFoundError',
    'UnicodeDecodeError', 'TypeError', 'ValueError', 'RuntimeError', 'TimeoutError',
    'KeyboardInterrupt', 'SystemExit'))


def _tick():
    try:
        value = time.monotonic()
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except Exception:
        return None


def _elapsed(started, ended):
    return round(ended-started, 6) if started is not None and ended is not None and ended >= started else None


def panel_timing_snapshot():
    """Return this process's counters without opening any file or ledger."""
    try:
        with _COUNTER_LOCK:
            return dict(_TIMING_TOTALS)
    except Exception:
        return {}


def _record_timing(timings, *, changed, success, error_type, transform):
    """Best-effort diagnostics run after releasing the publication file lock.

    A measurement, counter, or stdout failure must not replay a paid call,
    change the transform return value, or replace a durability exception.
    """
    try:
        with _COUNTER_LOCK:
            _TIMING_TOTALS['calls'] += 1
            _TIMING_TOTALS['changed' if success and changed else 'unchanged' if success else 'failed'] += 1
            _TIMING_TOTALS['timing_invalid'] += int(any(timings.get(name) is None for name in _PHASES))
            for name in _PHASES:
                value = timings.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                    _TIMING_TOTALS[name + '_seconds'] += value
        total = timings.get('total')
        if success and (total is None or total < PANEL_SLOW_SECONDS):
            return
        source = getattr(transform, '__module__', None)
        source = source if source in _SOURCES else 'other'
        record = {'event': 'panel_timing_v1', 'pid': os.getpid(), 'source': source,
                  'status': 'success' if success else 'failed', 'changed': bool(changed),
                  'slow': bool(total is not None and total >= PANEL_SLOW_SECONDS),
                  'error_type': error_type if error_type in _ERROR_TYPES else 'Exception' if error_type else None,
                  **{name + '_seconds': timings.get(name) for name in _PHASES}}
        print('PENTA5_PANEL_TIMING ' + json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')), flush=True)
    except Exception:
        pass


def _sync_parent(path):
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _delivery_clock():
    try:
        return datetime.now(timezone.utc)
    except Exception:
        return None


def _delivery_worker():
    while True:
        item=_DELIVERY_QUEUE.get()
        try:
            from panel_delivery_timing import record_delivery
            record_delivery(*item)
        except Exception:
            pass
        finally:
            _DELIVERY_QUEUE.task_done()


def _submit_delivery(path, before, committed, committed_at):
    """Bounded optional diagnostics: never await a lock, fsync or queue space.

    A short-lived publisher may exit before this daemon records an observation.
    Missing evidence stays missing; normal panel/stat refreshes can observe the
    currently committed representation later, without backdating its readiness.
    """
    global _DELIVERY_THREAD
    try:
        if not _DELIVERY_THREAD_LOCK.acquire(blocking=False):
            return
        try:
            if _DELIVERY_THREAD is None or not _DELIVERY_THREAD.is_alive():
                worker=threading.Thread(target=_delivery_worker,name='panel-delivery-observer',daemon=True)
                worker.start();_DELIVERY_THREAD=worker
        finally:
            _DELIVERY_THREAD_LOCK.release()
        _DELIVERY_QUEUE.put_nowait((path,before,committed,committed_at))
    except Exception:
        pass


def update_panel(transform, path=PANEL_PATH):
    started = _tick()
    timings = {name: 0.0 for name in _PHASES}
    changed = success = False
    delivery_at = None
    content = updated = None
    error_type = None
    phase, phase_started = 'lock_wait', started
    try:
        path = Path(path)
        with open(str(path) + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            locked = _tick()
            timings['lock_wait'] = _elapsed(started, locked)
            phase, phase_started = 'read', locked
            content = path.read_text(encoding='utf-8')
            read = _tick()
            timings['read'] = _elapsed(locked, read)
            phase, phase_started = 'transform', read
            updated = transform(content)
            transformed = _tick()
            timings['transform'] = _elapsed(read, transformed)
            if not isinstance(updated, str):
                raise TypeError('Panel transform must return text')
            phase, phase_started = 'commit', transformed
            if updated == content:
                # A prior replace may have completed before its directory fsync
                # failed. A same-card retry must finish that durable commit too.
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
                _sync_parent(path)
                delivery_at = _delivery_clock()
                timings['commit'] = _elapsed(transformed, _tick())
                success = True
                return False
            changed = True
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
                    temporary = f.name
                    f.write(updated)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(temporary, path.stat().st_mode & 0o777)
                os.replace(temporary, path)
                # Commit the visible card's replacement before publication can be
                # recorded in its independently persisted evidence ledger.
                _sync_parent(path)
                delivery_at = _delivery_clock()
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            timings['commit'] = _elapsed(transformed, _tick())
            success = True
            return True
    except BaseException as error:
        error_type = type(error).__name__
        timings[phase] = _elapsed(phase_started, _tick())
        raise
    finally:
        # The committed strings and clock are captured under panel.lock. The
        # optional derived sidecar is written only after releasing that lock.
        # Failed or unavailable diagnostics cannot replay/alter publication.
        if success and delivery_at is not None and path==PANEL_PATH:
            try:
                _submit_delivery(path, content, updated, delivery_at)
            except Exception:
                pass
        timings['total'] = _elapsed(started, _tick())
        try:
            _record_timing(timings, changed=changed, success=success, error_type=error_type, transform=transform)
        except Exception:
            pass
