#!/usr/bin/env python3
"""Refresh the evidence-backup source baseline manifest when live sources drift.

Why this exists
---------------
scripts/run_evidence_backup.py runs daily at 05:09 and only starts when
lib.evidence_backup.newest_source_manifest() can find a
/root/backups/*/verification-manifest.json whose recorded candidate_sha256
still matches every live file.  Deploys that change source bytes silently break
that invariant: on 2026-10-30 the daily receipt was missing for a whole day
because the newest manifest no longer matched the tree.

This tool rebuilds a *current* baseline manifest.  Rows are the union of
  (a) every path mentioned by any existing manifest (so files monitored today
      never silently drop out),
  (b) every file in the project tree whose suffix the evidence layering treats
      as source, and
  (c) /root/run_stats.py, the single allow-listed path outside the repo.
Missing, symlinked and non-regular paths are skipped.  Each surviving row is
{"source", "path", "candidate_sha256", "original_sha256"} with both hashes taken
from the live bytes, matching the schema _source_entries() verifies.

--if-drift is the cron-safe mode: it writes nothing while the newest manifest
still matches (prints {"status": "no-drift", ...} and exits 0) and only writes
when a deploy actually changed monitored sources.

Deliberate exclusions (same policy as evidence_backup._complete_sources):
  * directories data, node_modules, .git, backups, logs, __pycache__ and any
    dot-directory;
  * .env*, *.pem, *.key and set_new_key.py (credential material);
  * panel.html and hitrate.html, generated display snapshots rewritten by the
    panel refresh loop (every few minutes) and the /hitrate page loop (every
    15 minutes).  evidence_backup archives panel.html as snapshots/panel.html,
    not as a source.  Tracking either file here would report drift constantly.

Writes are atomic (temp file + os.replace, mode 0600) and are verified again by
calling newest_source_manifest()/_source_entries() after the rename.  Every
written baseline appends one JSON line to data/source_manifest_refresh.log.
Standard library only; never touches system schedulers, git or running services.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
SOURCE_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.json', '.sql', '.sh', '.md',
                   '.css', '.html', '.txt'}
EXCLUDE_DIRS = {'data', 'node_modules', '.git', '__pycache__', 'logs', 'backups'}
EXCLUDE_NAMES = {'set_new_key.py'}
EXCLUDE_SUFFIXES = {'.pem', '.key'}
# Generated display snapshot, rewritten on every panel refresh; see docstring.
VOLATILE_GENERATED = {'panel.html', 'hitrate.html'}
# The only path outside the repo that evidence_backup's allow-list accepts.
RUN_STATS = Path('/root/run_stats.py')
# evidence_backup._source_entries refuses manifests with fewer rows.
MIN_ROWS = 91
LOG_NAME = 'source_manifest_refresh.log'


def _sha256(path):
    digest = sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_project(root):
    found = set()
    for parent, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames
                       if name not in EXCLUDE_DIRS and not name.startswith('.')]
        for filename in filenames:
            suffix = Path(filename).suffix
            if suffix not in SOURCE_SUFFIXES:
                continue
            if (filename in EXCLUDE_NAMES or filename in VOLATILE_GENERATED
                    or filename.startswith('.env') or suffix in EXCLUDE_SUFFIXES):
                continue
            found.add(Path(parent) / filename)
    return found


def _manifest_paths(root, backup_base):
    found = set()
    manifests = sorted(Path(backup_base).glob('*/verification-manifest.json'))
    unreadable = []
    for manifest in manifests:
        try:
            payload = json.loads(manifest.read_bytes())
        except (OSError, ValueError):
            unreadable.append(str(manifest))
            continue
        rows = payload.get('files') if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            unreadable.append(str(manifest))
            continue
        for row in rows:
            name = row.get('path') if isinstance(row, dict) else None
            if not isinstance(name, str) or not name:
                continue
            found.add(Path(name) if name.startswith('/') else root / name)
    return found, manifests, unreadable


def _canonical(paths, root):
    kept, missing, outside = set(), 0, 0
    for path in paths:
        if path.name in VOLATILE_GENERATED and path.parent == Path(root):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                missing += 1
                continue
            resolved = path.resolve()
        except OSError:
            missing += 1
            continue
        if resolved != RUN_STATS and root not in resolved.parents:
            outside += 1
            continue
        kept.add(resolved)
    return kept, missing, outside


def _rows_for(paths, root):
    rows = []
    for path in sorted(paths, key=lambda item: (str(item) != str(RUN_STATS), str(item))):
        digest = _sha256(path)
        if str(path) == str(RUN_STATS):
            source = 'root/run_stats.py'
            name = str(RUN_STATS)
        else:
            name = str(path.relative_to(root)).replace('\\', '/')
            source = 'project/' + name
        rows.append({'source': source, 'path': name,
                     'candidate_sha256': digest, 'original_sha256': digest})
    return rows


def _build_rows(root, backup_base):
    project, manifests, unreadable = _manifest_paths(root, backup_base)
    project |= _walk_project(root)
    if RUN_STATS.is_file() and not RUN_STATS.is_symlink():
        project.add(RUN_STATS)
    kept, missing, outside = _canonical(project, root)
    rows = _rows_for(kept, root)
    if len(rows) < MIN_ROWS:
        raise ValueError('baseline-too-small: %d rows (<%d)' % (len(rows), MIN_ROWS))
    stats = {'manifests_scanned': len(manifests),
             'manifest_unreadable': unreadable,
             'paths_missing_or_nonregular': missing,
             'paths_outside_allowlist': outside,
             'rows': len(rows),
             'run_stats_included': any(str(item) == str(RUN_STATS) for item in kept)}
    return rows, stats


def _write_temp_manifest(target_dir, rows):
    handle, temporary = tempfile.mkstemp(prefix='.manifest-', dir=str(target_dir))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump({'files': rows}, stream, ensure_ascii=True, indent=1)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        return temporary
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _fsync_dir(path):
    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def _write_baseline(root, backup_base, evidence_backup, stamp, max_attempts=3):
    base_name = 'auto-baseline-' + stamp
    target_dir = Path(backup_base) / base_name
    suffix = 1
    while target_dir.exists():
        suffix += 1
        target_dir = Path(backup_base) / ('%s-%02d' % (base_name, suffix))
    manifest_path = target_dir / 'verification-manifest.json'
    last_error = None
    created = False
    for attempt in range(1, max_attempts + 1):
        rows, stats = _build_rows(root, backup_base)
        if not created:
            target_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            created = True
        temporary = _write_temp_manifest(target_dir, rows)
        try:
            evidence_backup._source_entries(root, temporary, [])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            last_error = exc
            try:
                os.unlink(temporary)
            except OSError:
                pass
            time.sleep(0.5 * attempt)
            continue
        os.replace(temporary, manifest_path)
        _fsync_dir(target_dir)
        try:
            selected = evidence_backup.newest_source_manifest(root, backup_base)
            evidence_backup._source_entries(root, manifest_path, [])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            last_error = exc
            try:
                manifest_path.unlink()
            except OSError:
                pass
            time.sleep(0.5 * attempt)
            continue
        return manifest_path, rows, stats, selected
    try:
        target_dir.rmdir()
    except OSError:
        pass
    raise RuntimeError('baseline-verification-failed: %s' % last_error)


def _log(root, record):
    path = root / 'data' / LOG_NAME
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    except OSError:
        return
    try:
        os.write(handle, line.encode('utf-8'))
    finally:
        os.close(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Refresh the evidence-backup source baseline manifest when live sources drift.')
    parser.add_argument('--root', required=True, help='project root, e.g. /root/penta5-runner')
    parser.add_argument('--backup-base', default='/root/backups',
                        help='directory holding */verification-manifest.json (default /root/backups)')
    parser.add_argument('--if-drift', action='store_true',
                        help='write only when the newest manifest no longer matches live sources')
    parser.add_argument('--dry-run', action='store_true',
                        help='compute and print what would happen; write nothing')
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    backup_base = Path(args.backup_base).resolve()
    sys.path.insert(0, str(root / 'lib'))
    sys.path.insert(0, str(root))
    try:
        import evidence_backup
    except Exception as exc:
        print(json.dumps({'status': 'error',
                          'error': 'cannot import evidence_backup: %s' % exc}), flush=True)
        return 1
    try:
        previous = None
        if args.if_drift:
            try:
                previous = evidence_backup.newest_source_manifest(root, backup_base)
            except (OSError, ValueError, TypeError, KeyError):
                previous = None
            if previous is not None:
                payload = {'status': 'no-drift', 'manifest': str(previous)}
                if args.dry_run:
                    payload['dry_run'] = True
                print(json.dumps(payload, ensure_ascii=False), flush=True)
                return 0
        stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
        trigger = 'if-drift' if args.if_drift else 'always'
        if args.dry_run:
            rows, stats = _build_rows(root, backup_base)
            payload = {'status': 'would-write', 'dry_run': True, 'trigger': trigger,
                       'would_write': str(Path(backup_base) / ('auto-baseline-' + stamp)
                                          / 'verification-manifest.json'),
                       'previous_manifest': str(previous) if previous else None,
                       'files': len(rows), 'stats': stats}
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            return 0
        manifest_path, rows, stats, selected = _write_baseline(
            root, backup_base, evidence_backup, stamp)
        record = {'at': datetime.now(TZ).isoformat(), 'status': 'baseline-written',
                  'manifest': str(manifest_path), 'files': len(rows), 'trigger': trigger,
                  'previous_manifest': str(previous) if previous else None,
                  'selected_manifest': str(selected), 'stats': stats}
        _log(root, record)
        print(json.dumps({'status': 'baseline-written', 'manifest': str(manifest_path),
                          'files': len(rows), 'selected_manifest': str(selected),
                          'trigger': trigger}, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'error',
                          'error': '%s: %s' % (type(exc).__name__, exc)}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
