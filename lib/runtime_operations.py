"""Read-only operational checks and bounded, derived monitoring snapshots.

This module never calls providers, controls jobs, repairs evidence or starts
services. Its only writes are its own derived summary/history and panel range.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import shutil
import tempfile
import time

TZ = timezone(timedelta(hours=8))
ROOT = Path('/root/penta5-runner')


def atomic_json(path, value):
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
        if hasattr(os, 'O_DIRECTORY'):
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resources(root=ROOT, *, memory_path=Path('/proc/meminfo')):
    usage = shutil.disk_usage(root)
    used = round(100 * usage.used / usage.total, 2) if usage.total else None
    memory = {}
    try:
        for row in Path(memory_path).read_text(encoding='ascii').splitlines():
            key, text = row.split(':', 1)
            if key in ('MemTotal', 'MemAvailable'):
                memory[key] = int(text.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        memory = {}
    total, available = memory.get('MemTotal'), memory.get('MemAvailable')
    low_memory = bool(total and available is not None and available < total * .05)
    level = ('critical' if (used is not None and used >= 90) or low_memory else
             'warning' if used is not None and used >= 75 else 'ok')
    return {'disk_total_bytes': usage.total, 'disk_free_bytes': usage.free,
            'disk_used_percent': used, 'memory_total_bytes': total,
            'memory_available_bytes': available, 'level': level,
            'warning_disk_percent': 75, 'critical_disk_percent': 90}


def backup_status(directory=Path('/root/evidence-archives')):
    """Only claim a local verified backup when the receipt explicitly proves it."""
    try:
        value = json.loads((Path(directory) / 'latest-verified.json').read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('verified') is not True
                or not isinstance(value.get('verified_at'), str)):
            return {'last_verified_at': None}
        stamp = datetime.fromisoformat(value['verified_at'])
        if stamp.tzinfo is None:
            return {'last_verified_at': None}
        name, size = value.get('archive_name'), value.get('size_bytes')
        if (not isinstance(name, str) or not re.fullmatch(r'evidence-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}\.tar\.gz', name)
                or type(size) is not int or size <= 0):
            return {'last_verified_at': None}
        archive = Path(directory) / name
        if not archive.is_file() or archive.is_symlink() or archive.stat().st_size != size:
            return {'last_verified_at': None}
        return {'last_verified_at': stamp.astimezone(TZ).isoformat()}
    except (OSError, ValueError, TypeError):
        return {'last_verified_at': None}


def _history_point(summary):
    # Copy a precise allowlist; input prompts, response text and credentials
    # never enter this derived history.
    models = {}
    for name, row in summary.get('models', {}).items():
        models[name] = {key: row.get(key) for key in
            ('status', 'phase', 'reference_period', 'target_period',
             'panel_matches', 'stages', 'durations_seconds', 'close_remaining_seconds','availability')}
    return {'updated_at': summary.get('updated_at'), 'source': summary.get('source'),
            'round': summary.get('round'), 'models': models,
            'stats_error': summary.get('stats_error'), 'resources': summary.get('resources')}


def run(*, root=ROOT, clock=None, collector=None, publisher=None):
    root = Path(root)
    now = clock if isinstance(clock, datetime) else datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('timezone-required')
    now = now.astimezone(TZ)
    if collector is None:
        from runtime_observability import collect
        collector = collect
    if publisher is None:
        from runtime_observability_view import publish
        publisher = publish
    started = time.monotonic()
    summary = collector(root=root, clock=lambda: now)
    if not isinstance(summary, dict) or summary.get('version') != 1:
        raise ValueError('invalid-runtime-summary')
    summary['resources'] = resources(root)
    summary['backup'] = backup_status()
    summary['collection_elapsed_seconds'] = round(time.monotonic() - started, 3)
    destination = root / 'data/runtime_observability'
    atomic_json(destination / 'summary.json', summary)
    day = (now - timedelta(hours=5)).strftime('%Y-%m-%d')
    history_path = destination / (day + '.json')
    history = []
    try:
        if history_path.stat().st_size <= 32 * 1024 * 1024:
            existing = json.loads(history_path.read_text(encoding='utf-8'))
            if isinstance(existing, dict) and existing.get('day') == day and isinstance(existing.get('snapshots'), list):
                history = existing['snapshots'][-1439:]
    except (OSError, ValueError, TypeError):
        pass
    point = _history_point(summary)
    minute = now.strftime('%Y-%m-%dT%H:%M')
    history = [row for row in history if isinstance(row, dict)
               and str(row.get('updated_at', ''))[:16] != minute]
    history.append(point)
    atomic_json(history_path, {'version': 1, 'day': day, 'snapshots': history[-1440:]})
    changed = publisher(summary, root=root)
    return {'status': 'updated', 'updated_at': summary['updated_at'],
            'stats_error': summary.get('stats_error'), 'panel_changed': bool(changed),
            'resource_level': summary['resources']['level'],
            'collection_elapsed_seconds': summary['collection_elapsed_seconds']}
