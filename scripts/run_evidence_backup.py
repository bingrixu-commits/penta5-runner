"""Daily bounded evidence backup; no deletion and no production restore."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import shutil
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence_backup import create_bundle, verify_bundle, newest_source_manifest, TZ, MAX_TOTAL


def _atomic(path, value):
    handle, temporary = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as file:
            json.dump(value, file, sort_keys=True, separators=(',', ':'), allow_nan=False)
            file.write('\n'); file.flush(); os.fsync(file.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


@contextmanager
def _budget(seconds):
    if not hasattr(signal, 'SIGALRM'):
        yield
        return
    def timeout(*_):
        raise TimeoutError('backup-wall-budget-exhausted')
    old = signal.signal(signal.SIGALRM, timeout)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    if old_timer != (0.0, 0.0):
        signal.signal(signal.SIGALRM, old)
        raise ValueError('backup-timer-already-owned')
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def run(root, destination, source_manifest=None, *, creator=create_bundle, verifier=verify_bundle,
        clock=lambda: datetime.now(TZ), monotonic=time.monotonic, sleep=time.sleep):
    import fcntl
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if (root == destination or root in destination.parents
            or destination == Path('/root/penta5-runner/data/backups')):
        raise ValueError('backup-directory-invalid')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    with (destination / 'evidence-backup.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'skipped-overlap', 'provider_calls': 0}
        # Keep room for a maximum-sized archive and isolated extraction, plus
        # one GiB for production. A failed backup never deletes older evidence.
        if shutil.disk_usage(destination).free < 2 * MAX_TOTAL + 1024 ** 3:
            raise ValueError('backup-disk-space-low')
        started, began = clock(), monotonic()
        # 2026-11-01: bundle grew past 2GiB (data/ ~1.34GiB + RDS system_snapshots);
        # MAX_TOTAL now 3GiB and the whole create+isolated-restore-verify cycle
        # exceeds the old 480s wall budget. Give the bounded work 870s so the
        # internal SIGALRM fires before the cron timeout (900s) and prints a
        # clean status instead of being SIGTERM-killed silently.
        with _budget(870):
            source_manifest = source_manifest or newest_source_manifest(root)
            for attempt in range(1, 4):
                if monotonic() - began >= 860:
                    raise TimeoutError('backup-wall-budget-exhausted')
                archive = destination / ('evidence-' + started.strftime('%Y%m%d-%H%M%S')
                                         + '-' + uuid4().hex[:8] + '.tar.gz')
                try:
                    result = creator(root, archive, source_manifest, clock=clock)
                    verification = verifier(archive)
                    if verification.get('status') != 'verified-isolated':
                        raise ValueError('backup-isolated-verification-failed')
                    completed = clock()
                    receipt = {'version': 1, 'verified': True, 'verified_at': completed.isoformat(),
                               'archive_name': archive.name, 'sha256': result['archive_sha256'],
                               'size_bytes': archive.stat().st_size, 'source_count': result['source_count'],
                               'rds': result['rds'], 'attempt': attempt,
                               'business_reset_hour': 5, 'provider_calls': 0,
                               'offserver_copy_verified': False}
                    _atomic(destination / 'latest-verified.json', receipt)
                    return {'status': 'verified', **receipt}
                except ValueError as error:
                    # Changing input is a normal concurrent-producer condition.
                    # Credentials/schema/integrity failures require inspection.
                    if str(error) not in ('evidence-changed-during-backup', 'sources-changed-during-backup') or attempt == 3:
                        raise
                    if monotonic() - began > 850:
                        raise TimeoutError('backup-wall-budget-exhausted')
                    sleep(3)
    raise ValueError('backup-attempts-exhausted')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--destination', default='/root/evidence-archives')
    parser.add_argument('--source-manifest', help='Optional pinned manifest; default newest matching verified deployment')
    args = parser.parse_args()
    try:
        result = run(args.root, args.destination, args.source_manifest)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as error:
        # Preserve any previously verified receipt; its age remains observable.
        code = str(error) if type(error) is ValueError and str(error).replace('-', '').isalnum() else type(error).__name__
        print(json.dumps({'status': 'failed', 'code': code, 'provider_calls': 0}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
