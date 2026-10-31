#!/usr/bin/env python3
"""非版本化配置的每日备份：.env / run_stats.py / panel.html / crontab（live + 版本化快照）。

只读收集到 /root/backups/config-<ts>/，附 sha256 清单与 latest receipt，保留最近 14 份。
不触碰任何运行状态；失败只影响备份产出（exit 1 用于 cron 可见）。
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

ROOT = Path('/root/penta5-runner')
BACKUP_BASE = Path('/root/backups')
KEEP = 14
TZ = timezone(timedelta(hours=8))

# 只备份「不在 git 里 / 经常被手改」的配置产物；git 跟踪的代码由 git 负责。
FILES = {
    '.env': ROOT / '.env',
    'run_stats.py': Path('/root/run_stats.py'),
    'panel.html': ROOT / 'panel.html',
    'crontab.txt': ROOT / 'ops' / 'crontab.txt',
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    now = datetime.now(TZ)
    stamp = now.strftime('%Y%m%d-%H%M%S')
    target = BACKUP_BASE / ('config-' + stamp)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    records, errors = {}, []
    for name, source in FILES.items():
        try:
            if not source.is_file():
                errors.append('%s:missing' % name)
                continue
            shutil.copy2(str(source), str(target / name))
            records[name] = {'sha256': sha256(target / name),
                             'bytes': source.stat().st_size,
                             'source': str(source)}
        except (OSError, shutil.Error) as error:
            errors.append('%s:%s' % (name, type(error).__name__))
    # live crontab（可能未同步进 ops/crontab.txt）
    try:
        live = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=20)
        if live.returncode == 0:
            (target / 'crontab-live.txt').write_text(live.stdout, encoding='utf-8')
            os.chmod(target / 'crontab-live.txt', 0o600)
            records['crontab-live.txt'] = {'sha256': sha256(target / 'crontab-live.txt'),
                                           'bytes': len(live.stdout.encode('utf-8')),
                                           'source': 'crontab -l'}
        else:
            errors.append('crontab-live:rc=%s' % live.returncode)
    except (OSError, subprocess.SubprocessError) as error:
        errors.append('crontab-live:%s' % type(error).__name__)
    # git HEAD 快照（溯源用）
    try:
        head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, timeout=20)
        if head.returncode == 0:
            records['.git-head'] = head.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    manifest = {'created_at': now.isoformat(timespec='seconds'),
                'keep': KEEP, 'files': records, 'errors': errors}
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(target),
                                     delete=False) as stream:
        tmp = stream.name
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, str(target / 'manifest.json'))
    # prune 旧备份
    pruned = []
    try:
        existing = sorted([p for p in BACKUP_BASE.glob('config-*') if p.is_dir()])
        for stale in existing[:-KEEP]:
            shutil.rmtree(stale)
            pruned.append(stale.name)
    except (OSError, shutil.Error) as error:
        errors.append('prune:%s' % type(error).__name__)
    receipt = {'backup': str(target), 'created_at': manifest['created_at'],
               'files': len(records), 'errors': errors, 'pruned': pruned}
    try:
        (BACKUP_BASE / 'latest-config-backup.json').write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8')
        os.chmod(BACKUP_BASE / 'latest-config-backup.json', 0o600)
    except OSError as error:
        errors.append('receipt:%s' % type(error).__name__)
    print(json.dumps({'status': 'ok', 'backup': str(target),
                      'files': len(records), 'errors': errors,
                      'pruned': len(pruned)}, ensure_ascii=False))
    return 1 if errors and not records else 0


if __name__ == '__main__':
    raise SystemExit(main())
