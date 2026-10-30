#!/usr/bin/env python3
"""把 350MB+ 的 selected_six_seals.json 压成可读的紧凑封存索引。

背景: data/selected_six_seals.json 已超 128MB, lib/strategy_comparison._read()
会以 ValueError('oversized-strategy-source') 拒绝读取, 面板的 six 卡片因此显示
「来源核验暂不可用」。本脚本不改动任何封存证据, 只做等价搬运: 在字节流里定位
顶层 "prediction_seals" 对象, 做括号配平后截取该段文本再 json.loads, 最后以
紧凑 JSON 写出索引。索引保留每个 seal 的全部字段, 后续校验(validate_publication
的 seal_sha256/_body/card_html/publication_state 等)强度不变。

用法:
  python3 scripts/build_six_seal_index.py              # 强制重建
  python3 scripts/build_six_seal_index.py --if-stale   # 源 size+mtime 未变则跳过
  python3 scripts/build_six_seal_index.py --root DIR   # 指定项目根(默认脚本上级)
"""
from pathlib import Path
import argparse
import fcntl
import json
import os
import re
import tempfile
import time

SOURCE_REL = Path('data') / 'selected_six_seals.json'
INDEX_REL = Path('data') / 'selected_six_seal_index.json'
LOCK_REL = Path('data') / '.selected_six_seal_index.lock'
MAX_SOURCE_BYTES = 128 * 1024 * 1024
HEADER_BYTES = 4096
_STRUCT = re.compile(rb'["{}\[\]]')
_SIZE_RE = re.compile(rb'"source_size":(\d+)')
_MTIME_RE = re.compile(rb'"source_mtime_ns":(\d+)')


def _string_end(raw, quote):
    """返回 raw[quote] 这个双引号字符串的结束位置(不含)。"""
    position = quote + 1
    while True:
        found = raw.find(b'"', position)
        if found < 0:
            raise ValueError('unterminated-string')
        backslashes = 0
        cursor = found - 1
        while cursor >= 0 and raw[cursor] == 0x5c:
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return found + 1
        position = found + 1


def _object_span(raw, marker=b'"prediction_seals"'):
    """按字节定位顶层 prediction_seals 对象, 返回 (起始 '{', 结束 ') 偏移)。"""
    found = raw.find(marker)
    if found < 0:
        raise ValueError('missing-prediction-seals')
    cursor = found + len(marker)
    while cursor < len(raw) and raw[cursor] in b' \t\r\n':
        cursor += 1
    if raw[cursor:cursor + 1] != b':':
        raise ValueError('invalid-prediction-seals')
    cursor += 1
    while cursor < len(raw) and raw[cursor] in b' \t\r\n':
        cursor += 1
    if raw[cursor:cursor + 1] != b'{':
        raise ValueError('invalid-prediction-seals')
    start = cursor
    depth = 0
    while True:
        match = _STRUCT.search(raw, cursor)
        if match is None:
            raise ValueError('unterminated-prediction-seals')
        cursor = match.start()
        byte = raw[cursor]
        if byte == 0x22:
            cursor = _string_end(raw, cursor)
            continue
        if byte in (0x7b, 0x5b):
            depth += 1
            cursor += 1
            continue
        depth -= 1
        cursor += 1
        if depth == 0:
            return start, cursor


def _header(path):
    """只读索引头部, 判断是否为同一源快照; 无效/缺失返回 None。"""
    try:
        with path.open('rb') as stream:
            head = stream.read(HEADER_BYTES)
    except OSError:
        return None
    if not head.startswith(b'{"version":1,'):
        return None
    size, mtime = _SIZE_RE.search(head), _MTIME_RE.search(head)
    if size is None or mtime is None:
        return None
    return int(size.group(1)), int(mtime.group(1))


def _source_stat(path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def is_fresh(source, index):
    current, recorded = _source_stat(source), _header(index)
    return current is not None and recorded is not None and current == recorded


def _atomic_write(index, payload):
    # json.dump 的逐块写入在这台机器上要 5.5s; 一次 dumps+encode 是 1.2s。
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(dir=str(index.parent), prefix='.selected_six_seal_index.', suffix='.tmp')
        temporary = Path(name)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        del data
        os.chmod(temporary, 0o600)
        os.replace(temporary, index)
        temporary = None
        directory = os.open(index.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def build(source, index, *, force=False, log=print):
    started = time.monotonic()
    with source.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        raw = stream.read()
    if stat.st_size <= 0 or len(raw) != stat.st_size:
        raise ValueError('invalid-prediction-seals-source')
    start, end = _object_span(raw)
    span = raw[start:end]
    del raw
    text = span.decode('utf-8')
    del span
    seals = json.loads(text)
    del text
    if not isinstance(seals, dict) or not seals:
        raise ValueError('invalid-prediction-seals')
    payload = {'version': 1, 'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
               'prediction_seals': seals}
    _atomic_write(index, payload)
    size = index.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise ValueError('oversized-six-seal-index')
    if log is not None:
        log('six-seal-index built: seals=%d source_bytes=%d index_bytes=%d elapsed=%.2fs'
            % (len(seals), stat.st_size, size, time.monotonic() - started))
    return {'seals': len(seals), 'source_size': stat.st_size, 'index_size': size,
            'elapsed': round(time.monotonic() - started, 2), 'rebuilt': True}


def _locked_build(source, index, *, if_stale, log):
    index.parent.mkdir(parents=True, exist_ok=True)
    lock_path = index.parent / LOCK_REL.name
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if if_stale and is_fresh(source, index):
                if log is not None:
                    log('six-seal-index fresh: skip rebuild')
                return {'rebuilt': False}
            return build(source, index, log=log)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description='build compact index for selected_six_seals.json')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--if-stale', action='store_true')
    arguments = parser.parse_args(argv)
    root = Path(arguments.root).resolve()
    source, index = root / SOURCE_REL, root / INDEX_REL
    if not source.is_file():
        print('six-seal-index error: missing source %s' % source, flush=True)
        return 2
    try:
        outcome = _locked_build(source, index, if_stale=arguments.if_stale, log=print)
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as error:
        print('six-seal-index error: %s: %s' % (type(error).__name__, error), flush=True)
        return 2
    print(json.dumps(outcome, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
