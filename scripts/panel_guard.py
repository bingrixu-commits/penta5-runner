#!/usr/bin/env python3
"""panel.html 完整性守卫。

背景：2026-11-13 10:10 panel.html 被未知写入方截断成约 10KB 的碎片（无 </html>），
run_stats 因 “Panel body missing; daily statistics guard not installed” 持续失败，
框架页 / 统计区块整片消失，只能手工从备份还原。

本脚本由 cron 每 2 分钟执行一次：
  1. 与面板写入方共用 panel.html.lock（flock），避免和 lib/panel_io.update_panel 抢；
  2. 结构校验：≥120KB、以 </html> 结尾、含 </body>、13 个框架标记至少 10 个、
     至少一个统计区块、文件内所有 START/END 标记成对；
  3. 正常：把当前面板快照到 data/panel_last_good.html（内容变化才写）；
  4. 异常：坏面板留档到 data/panel_corrupt/（保留最近 5 份），按
     “最近一次好快照 → 最近的有效备份” 顺序挑一份原子替换 panel.html；
  5. 全程写 data/panel_guard.log（256KB 轮转），便于巡检。

用法：
  python3 scripts/panel_guard.py                 # 守卫一次（cron 用）
  python3 scripts/panel_guard.py --check         # 只检查；坏页退出码 2
  python3 scripts/panel_guard.py --root DIR --backups-dir DIR   # 沙箱测试
"""
import argparse
import fcntl
import glob
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
FRAMEWORK_MARKERS = (
    'KILL_ONE', 'KILL_THREE', 'KILL_TEN', 'KILL_ELEVEN', 'KILL_TWELVE', 'KILL_THIRTEEN',
    'KILL_FOURTEEN', 'KILL_FIFTEEN', 'COLD_FIFTEEN', 'COLD_THIRTY', 'HE3_REFINE',
    'R1_KILLER', 'R1_FRAMEWORK',
)
STATS_MARKERS = ('PENTA5_STATS_PANEL', 'COLD_OBSERVATION')
MIN_BYTES = 120_000
MAX_CORRUPT = 5
LOG_LIMIT = 256 * 1024
LOCK_WAIT_SECONDS = 8.0
MARKER_RE = re.compile(r'<!-- ([A-Z0-9_]+)_(START|END) -->')


def now():
    return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')


def rotate_log(path):
    try:
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            os.replace(str(path), str(path) + '.1')
    except OSError:
        pass


def log(path, message):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_log(path)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(now() + ' ' + message + '\n')
    except OSError:
        pass


def validate(data):
    """Return None when the page is structurally sound, else a short reason."""
    if len(data) < MIN_BYTES:
        return 'too_small:%d' % len(data)
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return 'not_utf8'
    if not text.rstrip().endswith('</html>'):
        return 'missing_html_end'
    if '</body>' not in text:
        return 'missing_body_end'
    found = sum(1 for marker in FRAMEWORK_MARKERS if ('<!-- %s_START -->' % marker) in text)
    if found < 10:
        return 'framework_markers:%d/%d' % (found, len(FRAMEWORK_MARKERS))
    if not any(('<!-- %s_START -->' % marker) in text for marker in STATS_MARKERS):
        return 'no_stats_block'
    counts = {}
    for name, side in MARKER_RE.findall(text):
        counts.setdefault(name, {'START': 0, 'END': 0})[side] += 1
    for name in counts:
        if counts[name]['START'] != counts[name]['END']:
            return 'unbalanced:%s(%d/%d)' % (name, counts[name]['START'], counts[name]['END'])
    return None


def atomic_write(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, str(path))
        directory = os.open(str(path.parent), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def acquire_lock(path, timeout):
    stream = open(str(path), 'a')
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return stream
        except BlockingIOError:
            if time.monotonic() >= deadline:
                stream.close()
                return None
            time.sleep(0.1)


def candidates(root, backups_dir):
    paths = []
    good = root / 'data' / 'panel_last_good.html'
    if good.exists():
        paths.append(good)
    patterns = []
    if backups_dir:
        patterns.extend((
            str(backups_dir / 'config-*/panel.html'),
            str(backups_dir / '*/panel.html'),
        ))
    patterns.append(str(root / 'panel.html.bak-*'))
    for pattern in patterns:
        for item in glob.glob(pattern):
            try:
                if os.path.getsize(item) >= MIN_BYTES:
                    paths.append(Path(item))
            except OSError:
                continue
    seen, unique = set(), []
    for path in paths:
        try:
            resolved = str(path.resolve())
            stat = path.stat()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append((stat.st_mtime, path))
    unique.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in unique]


def prune_corrupt(directory):
    try:
        items = sorted(directory.glob('panel-*.html'), key=lambda path: path.stat().st_mtime)
    except OSError:
        return
    for path in items[:-MAX_CORRUPT]:
        try:
            path.unlink()
        except OSError:
            pass


def restore(root, panel, log_path, backups_dir):
    stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
    corrupt_dir = root / 'data' / 'panel_corrupt'
    corrupt_dir.mkdir(parents=True, exist_ok=True)
    corrupt_copy = corrupt_dir / ('panel-%s.html' % stamp)
    try:
        atomic_write(corrupt_copy, panel.read_bytes())
        prune_corrupt(corrupt_dir)
    except OSError:
        corrupt_copy = None
    for path in candidates(root, backups_dir):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        reason = validate(data)
        if reason is not None:
            continue
        atomic_write(panel, data)
        log(log_path, 'RESTORED from=%s size=%d corrupt_copy=%s' % (path, len(data), corrupt_copy))
        return path
    log(log_path, 'RESTORE_FAILED no valid candidate corrupt_copy=%s' % corrupt_copy)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description='panel.html integrity guard')
    parser.add_argument('--root', default='/root/penta5-runner')
    parser.add_argument('--backups-dir', default='/root/backups')
    parser.add_argument('--check', action='store_true', help='validate only; exit 2 when broken')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    panel = root / 'panel.html'
    log_path = root / 'data' / 'panel_guard.log'
    backups_dir = Path(args.backups_dir).resolve() if args.backups_dir else None

    if not panel.exists():
        if args.check:
            print(json.dumps({'ok': False, 'reason': 'panel_missing', 'checked_at': now()}, ensure_ascii=False))
        log(log_path, 'PANEL_MISSING')
        return 1

    if args.check:
        data = panel.read_bytes()
        reason = validate(data)
        print(json.dumps({'ok': reason is None, 'size': len(data), 'reason': reason,
                          'checked_at': now()}, ensure_ascii=False))
        return 0 if reason is None else 2

    lock = acquire_lock(root / 'panel.html.lock', LOCK_WAIT_SECONDS)
    if lock is None:
        log(log_path, 'SKIP lock_busy')
        if args.verbose:
            print('skip: lock busy')
        return 0
    try:
        data = panel.read_bytes()
        reason = validate(data)
        if reason is None:
            good = root / 'data' / 'panel_last_good.html'
            try:
                snapshot = good.read_bytes() if good.exists() else None
            except OSError:
                snapshot = None
            if snapshot != data:
                atomic_write(good, data)
                log(log_path, 'SNAPSHOT size=%d' % len(data))
                if args.verbose:
                    print('snapshot %d bytes' % len(data))
            return 0
        log(log_path, 'BROKEN reason=%s size=%d' % (reason, len(data)))
        restored = restore(root, panel, log_path, backups_dir)
        if args.verbose:
            print('broken: %s; restored from %s' % (reason, restored))
        return 0 if restored is not None else 1
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
