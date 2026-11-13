#!/usr/bin/env python3
"""fanotify 写入审计：记录谁在写 panel.html（以及 stats/hitrate/accuracy）。

2026-11-13 10:10 panel.html 被写成 2 字节碎片，现有日志无法给出写入进程。
本进程用 fanotify(FAN_CLOSE_WRITE|FAN_EVENT_ON_CHILD) 监听仓库根目录的直接子文件，
命中目标文件时记录 pid / comm / cmdline / 大小；若 panel.html 写入后小于阈值，
立即拉起 scripts/panel_guard.py 还原。

需要 root（CAP_SYS_ADMIN）。pm2 托管：
  pm2 start scripts/panel_write_watch.py --name penta5-panel-watch --interpreter python3
"""
import ctypes
import json
import os
import select
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/penta5-runner')
LOG = ROOT / 'data' / 'panel_write_watch.log'
GUARD = ROOT / 'scripts' / 'panel_guard.py'
WATCH_NAMES = tuple(os.environ.get('PENTA5_PANEL_WATCH_NAMES',
                                   'panel.html,stats.html,hitrate.html,accuracy.html').split(','))
MIN_PANEL_BYTES = 120_000
LOG_LIMIT = 1024 * 1024

FAN_CLOEXEC = 0x00000001
FAN_NONBLOCK = 0x00000002
FAN_CLASS_NOTIF = 0x00000000
FAN_UNLIMITED_QUEUE = 0x00000010
FAN_MARK_ADD = 0x00000001
FAN_MODIFY = 0x00000002
FAN_CLOSE_WRITE = 0x00000008
FAN_EVENT_ON_CHILD = 0x08000000
AT_FDCWD = -100
EVENT = struct.Struct('=IBBHQii')  # event_len, vers, reserved, metadata_len, mask, fd, pid

libc = ctypes.CDLL('libc.so.6', use_errno=True)
libc.fanotify_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
libc.fanotify_init.restype = ctypes.c_int
libc.fanotify_mark.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_int, ctypes.c_char_p]
libc.fanotify_mark.restype = ctypes.c_int


def now():
    return datetime.now(timezone.utc).isoformat()


def rotate():
    try:
        if LOG.exists() and LOG.stat().st_size > LOG_LIMIT:
            os.replace(str(LOG), str(LOG) + '.1')
    except OSError:
        pass


def log(record):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        rotate()
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(now() + ' ' + json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        pass


def proc_info(pid):
    info = {'pid': pid}
    try:
        info['comm'] = Path('/proc/%d/comm' % pid).read_text().strip()
    except OSError:
        pass
    try:
        raw = Path('/proc/%d/cmdline' % pid).read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace').strip()
        info['cmdline'] = raw[:400]
    except OSError:
        pass
    try:
        status = Path('/proc/%d/status' % pid).read_text()
        for line in status.splitlines():
            if line.startswith('PPid:'):
                info['ppid'] = int(line.split()[1])
                break
    except OSError:
        pass
    return info


def start_guard():
    try:
        subprocess.Popen(['python3', str(GUARD)], cwd=str(ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        pass


def classify(name):
    """Return (target, kind) for direct page writes or atomic temp files."""
    for target in WATCH_NAMES:
        if not target:
            continue
        if name == target:
            return target, 'direct'
        if name.startswith('.' + target + '.'):
            return target, 'temp'
    return None, None


def main():
    handle = libc.fanotify_init(FAN_CLASS_NOTIF | FAN_CLOEXEC | FAN_NONBLOCK | FAN_UNLIMITED_QUEUE, os.O_RDONLY)
    if handle < 0:
        sys.stderr.write('fanotify_init failed: errno=%d\n' % ctypes.get_errno())
        return 1
    mask = FAN_CLOSE_WRITE | FAN_EVENT_ON_CHILD
    if libc.fanotify_mark(handle, FAN_MARK_ADD, mask, AT_FDCWD, str(ROOT).encode()) < 0:
        sys.stderr.write('fanotify_mark failed: errno=%d\n' % ctypes.get_errno())
        os.close(handle)
        return 1
    print('panel write watch active on %s -> %s' % (ROOT, ','.join(WATCH_NAMES)), flush=True)
    while True:
        try:
            ready, _, _ = select.select([handle], [], [], 30)
            if not ready:
                continue
            buffer = os.read(handle, 65536)
        except InterruptedError:
            continue
        offset = 0
        while offset + EVENT.size <= len(buffer):
            event_len, _vers, _res, _meta, mask, event_fd, pid = EVENT.unpack_from(buffer, offset)
            offset += event_len
            path = None
            try:
                path = os.readlink('/proc/self/fd/%d' % event_fd)
            except OSError:
                pass
            finally:
                try:
                    os.close(event_fd)
                except OSError:
                    pass
            if not path:
                continue
            name = os.path.basename(path.split(' (deleted)')[0])
            target, kind = classify(name)
            if target is None:
                continue
            try:
                size = os.stat(path.split(' (deleted)')[0]).st_size
            except OSError:
                size = None
            record = {'event': 'write', 'path': path, 'size': size, 'match': kind,
                      'mask': mask, 'target': target, **proc_info(pid)}
            log(record)
            if kind == 'direct' and target == 'panel.html' and size is not None and size < MIN_PANEL_BYTES:
                log({'event': 'trigger_guard', 'path': path, 'size': size, **proc_info(pid)})
                start_guard()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
