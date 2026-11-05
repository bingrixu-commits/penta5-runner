"""Credential-free, stable evidence backup and isolated verification only.

No restore-to-production command exists. Creating a bundle uses a read-only RDS
transaction and rejects changing evidence/source files; verification never
imports archived code or starts a service. The archive is an evidence recovery
point, not a full MariaDB/system image or a substitute for external credentials.
"""
import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

VERSION = 'penta5-evidence-backup-v1'
TZ = ZoneInfo('Asia/Shanghai')
MAX_MEMBER = 1024 * 1024 * 1024
# 2026-11-01: business evidence (seal/stat JSON) grew past 2GiB; data/ ~1.34GiB + RDS business-tables (system_snapshots) pushed the raw bundle over the old 2GiB bound -> raise to 3GiB (disk check needs 2*MAX_TOTAL+1GiB=7GiB, disk free ~23GiB, OK).
MAX_TOTAL = 3 * 1024 * 1024 * 1024
# 2026-11-05: 封存账本最大单体已达 432MiB，整棵 json.loads 时对象图可达数 GiB；
# 超过该阈值的 JSON 只做结构哨兵+原文凭据扫描+敏感键正则，不物化对象图。
FULL_JSON_VALIDATE_MAX = 64 * 1024 * 1024
_WS_BYTES = b' \t\r\n'
SAFE_CONFIG = {'LOCAL_STATE_MODE', 'ALGO_DURABLE_STORE', 'DATA_READ_MODE',
               'PENTA5_DAY_RESET_HOUR', 'PENTA5_ALGO_PROCESS_MODE',
               'PENTA5_ORCHESTRATE_FORCE_INPROCESS'}
JSON_DIRS = ('ai_model_inputs', 'stat50_position_verified', 'kill3_tail', 'kill1_tail', 'kill10_tail', 'kill11_tail')
REQUIRED_DATA = frozenset('data/' + name for name in (
    'ai_api_control.json', 'ai_api_control_audit.jsonl', 'state.json', 'stats_draw_history.json',
    'r1_stats.json', 'r1_framework_seals.json',
    'he3_refine_seals.json',
    'r1_framework_hit_stats.json',
    'he3_refine_hit_stats.json'))
LEGACY_DATA = frozenset()
RDS_TABLES = ('official_draws', 'model_predictions', 'model_evaluations',
              'model_counters', 'model_states', 'system_snapshots')
SOURCE_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.json', '.sql', '.sh', '.md', '.css', '.html',
                   '.svg', '.png', '.jpg', '.jpeg', '.ico', '.woff', '.woff2', '.ttf', '.wasm'}
SOURCE_EXCLUDE_DIRS = {'data', 'node_modules', '.git', '__pycache__', 'logs', 'backups'}
SOURCE_EXCLUDE_NAMES = {'set_new_key.py'}
SENSITIVE_KEYS = {'password', 'api_key', 'apikey', 'authorization', 'private_key',
                  'access_token', 'refresh_token', 'client_secret'}
CREDENTIAL_FIELD_PATTERN = re.compile(
    rb'"(?:' + b'|'.join(key.encode() for key in sorted(SENSITIVE_KEYS)) + rb')"\s*:\s*"(?!\[redacted\])[^"]+"',
    re.IGNORECASE)
NODE_EXPORT = r'''
const path=require('path');
const {once}=require('events');
const root=process.argv[1];
const {createPool}=require(path.join(root,'api/db.js'));
async function emit(value){if(!process.stdout.write(JSON.stringify(value)+'\n'))await once(process.stdout,'drain');}
(async()=>{let pool,conn;try{
 pool=createPool();conn=await pool.getConnection();
 await conn.query('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ');
 await conn.query('SET TRANSACTION READ ONLY');
 await conn.query('START TRANSACTION WITH CONSISTENT SNAPSHOT');
 const names=['official_draws','model_predictions','model_evaluations','model_counters','model_states','system_snapshots'];
 const [actual]=await conn.query('SHOW TABLES');
 const actualNames=actual.map(row=>Object.values(row)[0]).sort();
 if(JSON.stringify(actualNames)!==JSON.stringify([...names].sort()))throw new Error('unexpected-business-schema');
 await emit({type:'header',version:1,read_only:true,isolation:'REPEATABLE READ',exported_at:new Date().toISOString()});
 for(const name of names){
  const [rows]=await conn.query('SELECT * FROM `'+name+'` ORDER BY id');
  const [ddl]=await conn.query('SHOW CREATE TABLE `'+name+'`');
  await emit({type:'table',name,create_table:ddl[0]['Create Table']});
  for(const row of rows)await emit({type:'row',name,row});
 }
 await conn.rollback();
 await emit({type:'complete'});
}catch(e){process.stderr.write('rds-readonly-export-failed');process.exitCode=1;}
finally{if(conn)conn.release();if(pool)await pool.end();}})();
'''


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


class _LazyBlob:
    """已落盘的大归档成员：tar 时按需流式读取，避免整块常驻内存。"""
    __slots__ = ('path', 'size', 'sha256')

    def __init__(self, path, size, digest):
        self.path = path
        self.size = size
        self.sha256 = digest


_CANONICAL_ENCODER = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False)


class _CanonicalRows:
    """已按 canonical 逗号拼接落盘的行集合；写出时按字节流拼进 [...]，不物化列表。"""
    __slots__ = ('path',)

    def __init__(self, path):
        self.path = Path(path)


class _CanonicalWriter:
    """与 _canonical() 逐字节等价、但按值递归流式写出。

    json.iterencode 在分块模式下会落到纯 Python 编码器（821MiB 文档实测约
    1.2MiB/s，单次写完要十几分钟）；这里容器递归、标量/单行交给 C 编码器，
    既保持字节等价，又把峰值内存压到一个行对象的量级。
    """

    _CHUNK = 1 << 20

    def __init__(self, stream):
        self.stream = stream
        self.digest = sha256()
        self.size = 0
        self.parts = []
        self.pending = 0

    def _flush(self):
        if self.parts:
            data = ''.join(self.parts).encode('utf-8')
            self.parts.clear()
            self.pending = 0
            self.digest.update(data)
            self.size += len(data)
            self.stream.write(data)

    def text(self, value):
        self.parts.append(value)
        self.pending += len(value)
        if self.pending >= self._CHUNK:
            self._flush()

    def raw(self, data):
        self._flush()
        self.digest.update(data)
        self.size += len(data)
        self.stream.write(data)

    def value(self, item):
        if isinstance(item, _CanonicalRows):
            self.raw(b'[')
            with open(item.path, 'rb') as source:
                for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
                    self.raw(chunk)
            self.raw(b']')
        elif isinstance(item, dict):
            self.text('{')
            for position, key in enumerate(sorted(item)):
                if position:
                    self.text(',')
                self.text(_CANONICAL_ENCODER.encode(str(key)))
                self.text(':')
                self.value(item[key])
            self.text('}')
        elif isinstance(item, list):
            self.text('[')
            for position, child in enumerate(item):
                if position:
                    self.text(',')
                self.value(child)
            self.text(']')
        else:
            self.text(_CANONICAL_ENCODER.encode(item))

    def finish(self):
        self._flush()
        self.stream.flush()
        os.fsync(self.stream.fileno())
        return self.size, self.digest.hexdigest()


def _write_canonical(value, path):
    """把 _canonical(value) 的等价字节流式写入 path，返回 (size, sha256)。"""
    with open(path, 'wb') as stream:
        writer = _CanonicalWriter(stream)
        writer.value(value)
        return writer.finish()


class _RdsExport:
    """流式导出的结果：canonical 已落盘，只回传证明字段与尺寸。"""
    __slots__ = ('proof', 'exported_at', 'size', 'sha256', 'max_opened')

    def __init__(self, proof, exported_at, size, digest, max_opened):
        self.proof = proof
        self.exported_at = exported_at
        self.size = size
        self.sha256 = digest
        self.max_opened = max_opened


def _check_credential_fields(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS and item not in (None, '', '[redacted]'):
                raise ValueError('credential-field-refused')
            _check_credential_fields(item)
    elif isinstance(value, list):
        for item in value:
            _check_credential_fields(item)


def _validate_official_draw_row(row, periods, max_opened):
    period = row.get('period')
    if not isinstance(period, str) or not re.fullmatch(r'[0-9]{8}(?:[0-2][0-9]{2})', period):
        raise ValueError('rds-period-invalid')
    index = int(period[-3:])
    try:
        slot = datetime.strptime(period[:8], '%Y%m%d').replace(tzinfo=TZ) + timedelta(minutes=5 * index)
    except ValueError:
        raise ValueError('rds-period-invalid') from None
    if not 1 <= index <= 288 or period in periods:
        raise ValueError('rds-period-duplicate-or-invalid')
    periods.add(period)
    if any(type(row.get('ball' + str(i))) is not int or not 0 <= row['ball' + str(i)] <= 9 for i in range(1, 5)):
        raise ValueError('rds-first-four-invalid')
    try:
        opened = datetime.fromisoformat(str(row.get('open_time')).replace('Z', '+00:00'))
        # api/draw_store.js::toMysqlDateTime serializes ISO UTC then removes T/Z.
        opened = (opened.replace(tzinfo=timezone.utc).astimezone(TZ)
                  if opened.tzinfo is None else opened.astimezone(TZ))
    except ValueError:
        raise ValueError('rds-open-time-invalid') from None
    if not slot <= opened < slot + timedelta(minutes=1):
        raise ValueError('rds-open-time-invalid')
    return opened if max_opened is None or opened > max_opened else max_opened


def _export_rds_streaming(root, destination):
    """RDS 导出边读边校验边落盘：行数据只保留当前一条，canonical 直接写 destination。

    2026-11-05 之前 export_draws() 会把 821MiB business-tables 解析成完整对象图
    （实测 RSS 3.3GiB），再叠加 1.3GiB 原始证据字节，与 05:00 批次并发时触发全局
    OOM。这里改成单遍流式：每行校验后写成 canonical 片段进 spool，最后按排序好的
    表顺序拼成 canonical 文件，峰值只与单行大小相关。
    """
    destination = Path(destination)
    spool_dir = Path(tempfile.mkdtemp(prefix='.rds-rows-', dir=destination.parent))
    process = subprocess.Popen(['node', '-e', NODE_EXPORT, str(root)], cwd=root,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    began = time.monotonic()
    header, tables, periods, max_opened = {}, {}, set(), None
    streams = {}
    complete = False
    try:
        try:
            for raw in process.stdout:
                if time.monotonic() - began > 180:
                    raise TimeoutError('rds-readonly-export-timeout')
                try:
                    item = json.loads(raw)
                except ValueError:
                    raise ValueError('rds-export-invalid-json') from None
                kind = item.get('type')
                if kind == 'header':
                    header.update({key: value for key, value in item.items() if key != 'type'})
                elif kind == 'table' and item.get('name') in RDS_TABLES:
                    name = item['name']
                    if name in tables:
                        raise ValueError('rds-export-invalid-json')
                    path = spool_dir / (name + '.rows')
                    streams[name] = path.open('wb')
                    tables[name] = {'create_table': item['create_table'], 'path': path, 'count': 0}
                elif kind == 'row' and item.get('name') in tables:
                    name = item['name']
                    row = item['row']
                    _check_credential_fields(row)
                    if name == 'official_draws':
                        max_opened = _validate_official_draw_row(row, periods, max_opened)
                    stream = streams[name]
                    if tables[name]['count']:
                        stream.write(b',')
                    stream.write(_CANONICAL_ENCODER.encode(row).encode('utf-8'))
                    tables[name]['count'] += 1
                elif kind == 'complete':
                    complete = True
                else:
                    raise ValueError('rds-export-invalid-json')
            if not complete:
                raise ValueError('rds-export-invalid-json')
        finally:
            for stream in streams.values():
                stream.close()
        if process.wait():
            raise ValueError('rds-readonly-export-failed')
        merged = {'version': 1, 'read_only': True}
        merged.update(header)
        if (merged.get('read_only') is not True or set(tables) != set(RDS_TABLES)
                or not tables['official_draws']['count']):
            raise ValueError('rds-export-empty-or-invalid')
        proof = {'row_count': len(periods), 'first_period': min(periods),
                 'last_period': max(periods), 'model_balls': [1, 2, 3, 4],
                 'ball5_display_only': True,
                 'table_counts': {name: tables[name]['count'] for name in RDS_TABLES}}
        merged['tables'] = {name: {'create_table': tables[name]['create_table'],
                                   'rows': _CanonicalRows(tables[name]['path'])}
                            for name in RDS_TABLES}
        with open(destination, 'wb') as stream:
            writer = _CanonicalWriter(stream)
            writer.value(merged)
            size, digest = writer.finish()
        return _RdsExport(proof, merged.get('exported_at'), size, digest, max_opened)
    finally:
        for stream in streams.values():
            try:
                stream.close()
            except Exception:
                pass
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except Exception:
                pass
        if process.poll() is None:
            process.kill()
            process.wait()
        for path in spool_dir.glob('*'):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            spool_dir.rmdir()
        except OSError:
            pass


def _entry_len(value):
    return value.size if isinstance(value, _LazyBlob) else len(value)


def _entry_sha(value):
    return value.sha256 if isinstance(value, _LazyBlob) else _hash(value)


def _hash(data):
    return sha256(data).hexdigest()


def _file_hash(path):
    digest = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(name):
    if (not isinstance(name, str) or '\\' in name or '\x00' in name
            or re.match(r'^[A-Za-z]:', name)):
        raise ValueError('unsafe-archive-path')
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in ('..', '.') for p in path.parts):
        raise ValueError('unsafe-archive-path')
    return path


def _env_metadata(root):
    """Read values only to exclude secrets; archive only safe mode switches."""
    values = dict(os.environ)
    for path in (root / '.env', root / 'api/.env'):
        if not path.exists():
            continue
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, value = line.split('=', 1)
            values.setdefault(name.strip(), value.strip().strip('\"\''))
    secrets = []
    for key, value in values.items():
        sensitive = bool(re.search(r'PASSWORD|SECRET|TOKEN|(?:API|PRIVATE)_KEY', key))
        if key == 'SOURCE_API_URL':
            try:
                url = urlsplit(value)
                # A public draw endpoint already present in runtime source is
                # not a credential. Auth/userinfo, query and fragment values
                # remain external, and actual API secrets are scanned above.
                sensitive = bool(url.username or url.password or url.query or url.fragment)
            except ValueError:
                sensitive = True
        if sensitive and len(value) >= 8:
            secrets.append(value.encode('utf-8'))
    config = {k: v for k, v in values.items() if k in SAFE_CONFIG}
    return secrets, {'safe_modes': config, 'credentials_external_required': True,
                     'business_timezone': 'Asia/Shanghai', 'business_reset_hour': 5}


def _check_secrets(data, secrets, *, parsed=None):
    if any(value in data for value in secrets):
        raise ValueError('secret-content-refused')
    # 2026-11-05: 432MiB 账本上直接跑带 \b 的交替正则会退化成 ~8s 全量扫描；
    # 先做廉价字面量预筛（bytes.find 的双路算法），命中才跑正则，语义不变。
    if (b'-----BEGIN ' in data or b'sk-' in data) and re.search(
            rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}', data):
        raise ValueError('secret-content-refused')
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in SENSITIVE_KEYS and item not in (None, '', '[redacted]'):
                    raise ValueError('credential-field-refused')
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    if parsed is not None:
        walk(parsed)


def _check_secrets_file(path, secrets):
    """对已落盘的大 JSON 做分块凭据扫描，内存上限约一个 chunk。"""
    overlap = max(64, max((len(value) for value in secrets), default=0))
    pattern = re.compile(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}')
    tail = b''
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(8 * 1024 * 1024)
            if not chunk:
                break
            data = tail + chunk
            if any(value in data for value in secrets):
                raise ValueError('secret-content-refused')
            if (b'-----BEGIN ' in data or b'sk-' in data) and pattern.search(data):
                raise ValueError('secret-content-refused')
            tail = data[-overlap:]


def discover(root):
    """Only business JSON/JSONL evidence, never logs/tmp/locks/env/backups."""
    root = Path(root).resolve()
    data = root / 'data'
    paths = []
    for path in data.iterdir():
        if path.suffix in ('.json', '.jsonl') and path.is_symlink():
            raise ValueError('symlink-evidence-refused')
        if path.is_file() and not path.is_symlink() and path.suffix in ('.json', '.jsonl'):
            paths.append(path)
    for name in JSON_DIRS:
        base = data / name
        if base.exists():
            for path in base.rglob('*'):
                if path.is_symlink():
                    raise ValueError('symlink-evidence-refused')
                if path.is_file() and path.suffix in ('.json', '.jsonl'):
                    paths.append(path)
    if not paths or not (data / 'ai_api_control.json').is_file():
        raise ValueError('required-control-evidence-missing')
    return sorted(paths)


def _required_members(names, *, legacy=()):
    names = set(names)
    required = REQUIRED_DATA | set(legacy)
    if (not required <= names or not any(name.startswith('data/ai_model_inputs/') and name.endswith('.json') for name in names)):
        raise ValueError('required-evidence-members-missing')
    return sorted(required)


def _validate_json_bytes(raw, suffix, secrets):
    if len(raw) > MAX_MEMBER:
        raise ValueError('evidence-file-too-large')
    _check_secrets(raw, secrets)
    if suffix == '.jsonl':
        for line in io.BytesIO(raw):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except (ValueError, UnicodeError):
                raise ValueError('invalid-evidence-json') from None
            _check_secrets(line, secrets, parsed=parsed)
        return raw
    if len(raw) <= FULL_JSON_VALIDATE_MAX:
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError('invalid-evidence-json') from None
        _check_secrets(raw, secrets, parsed=parsed)
        return raw
    # 大文件：结构哨兵 + 原文凭据扫描 + 敏感键正则（语义近似 walk，不物化对象图）。
    # 注意：lstrip/rstrip 在带尾随换行的 432MiB 账本上会整块复制，这里用手写扫描。
    lo, hi = 0, len(raw) - 1
    while lo <= hi and raw[lo] in _WS_BYTES:
        lo += 1
    while hi >= lo and raw[hi] in _WS_BYTES:
        hi -= 1
    head, tail = raw[lo:lo + 1] if lo <= hi else b'', raw[hi:hi + 1] if lo <= hi else b''
    if not ((head == b'{' and tail == b'}') or (head == b'[' and tail == b']')):
        raise ValueError('invalid-evidence-json')
    if CREDENTIAL_FIELD_PATTERN.search(raw):
        raise ValueError('credential-field-refused')
    return raw


def _read_json_file(path, secrets):
    return _validate_json_bytes(path.read_bytes(), path.suffix, secrets)


def _file_signature(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('evidence-changed-during-backup')
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def capture_files(root, paths):
    """Fast immutable raw-byte cut, with no JSON parsing/secret traversal.

    All paths and signatures must remain identical across the short capture.
    Validation and RDS export happen later and cannot extend this bookend.
    """
    began = time.monotonic()
    try:
        before = {path: _file_signature(path) for path in paths}
        if any(signature[2] > MAX_MEMBER for signature in before.values()):
            raise ValueError('evidence-file-too-large')
        captured = {path: path.read_bytes() for path in paths}
        if (discover(root) != paths or any(_file_signature(path) != signature
                or len(captured[path]) != signature[2] for path, signature in before.items())):
            raise ValueError('evidence-changed-during-backup')
    except OSError:
        raise ValueError('evidence-changed-during-backup') from None
    return captured, round(time.monotonic() - began, 6)


def _source_entries(root, source_manifest, secrets):
    manifest_raw = Path(source_manifest).read_bytes()
    manifest = json.loads(manifest_raw)
    rows = manifest.get('files')
    if not isinstance(rows, list) or len(rows) < 91:
        raise ValueError('source-manifest-incomplete')
    result = {'metadata/source-manifest.json': manifest_raw}
    origins = {}
    for row in rows:
        name = row.get('path') if isinstance(row, dict) else None
        if not isinstance(name, str):
            raise ValueError('source-manifest-path-invalid')
        path = Path(name) if name.startswith('/') else root / name
        if path.is_symlink():
            raise ValueError('source-symlink-refused')
        resolved = path.resolve()
        if root not in resolved.parents and resolved not in (Path('/root/run_stats.py'),):
            raise ValueError('source-manifest-outside-allowlist')
        if any(p.startswith('.env') for p in resolved.parts) or resolved.suffix in ('.pem', '.key'):
            raise ValueError('source-credential-path-refused')
        raw = resolved.read_bytes()
        if _hash(raw) != row.get('candidate_sha256'):
            raise ValueError('source-manifest-hash-mismatch')
        _check_secrets(raw, secrets)
        relative = 'sources/' + (str(resolved.relative_to(root)).replace('\\', '/')
                                  if root in resolved.parents else '_root/run_stats.py')
        _safe_relative(relative)
        result[relative] = raw
        origins[resolved] = _hash(raw)
    return result, origins


def _complete_sources(root, secrets):
    """Current source tree, including algorithm modules beyond the 91 manifest.

    Generated backups and credential-writing legacy maintenance helpers are not
    runtime source. Package manifests are included; dependency binaries are not.
    """
    entries, origins, omitted, aliases = {}, {}, [], []
    def record_alias(path):
        target = path.resolve()
        if not target.exists() or root not in target.parents:
            raise ValueError('source-alias-outside-project-or-missing')
        aliases.append({'path': str(path.relative_to(root)).replace('\\', '/'),
                        'target': str(target.relative_to(root)).replace('\\', '/'),
                        'kind': 'directory' if target.is_dir() else 'file',
                        'restore_policy': 'recreate-relative-link-after-target-verified'})
    for parent, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories
                          if name not in SOURCE_EXCLUDE_DIRS and not name.startswith('.')]
        for name in directories[:]:
            path = Path(parent) / name
            if path.is_symlink():
                record_alias(path)
                directories.remove(name)
        for filename in filenames:
            path = Path(parent) / filename
            if path.suffix not in SOURCE_SUFFIXES and filename != 'requirements.txt':
                continue
            name = str(path.relative_to(root)).replace('\\', '/')
            if filename in SOURCE_EXCLUDE_NAMES:
                omitted.append({'path': name, 'reason': 'legacy-credential-maintenance-helper-external'})
                continue
            if path.is_symlink():
                record_alias(path)
                continue
            raw = path.read_bytes()
            _check_secrets(raw, secrets)
            if name == 'panel.html':
                # A display snapshot is useful recovery evidence but updates
                # independently of program code and financial input state.
                entries['snapshots/panel.html'] = raw
            else:
                entries['sources/' + name] = raw
                origins[path] = _hash(raw)
    if not entries:
        raise ValueError('runtime-sources-missing')
    for alias in aliases:
        target = 'sources/' + alias['target']
        covered = (target in entries if alias['kind'] == 'file'
                   else any(name.startswith(target + '/') for name in entries))
        if not covered:
            raise ValueError('source-alias-target-not-archived')
    entries['metadata/source-exclusions.json'] = _canonical(omitted)
    entries['metadata/source-aliases.json'] = _canonical(sorted(aliases, key=lambda row: row['path']))
    return entries, origins


def preflight(root, source_manifest):
    """Read-only current-tree policy check; no archive, DB or provider access."""
    root = Path(root).resolve()
    secrets, _ = _env_metadata(root)
    paths = discover(root)
    names = {str(path.relative_to(root)).replace('\\', '/') for path in paths}
    _required_members(names, legacy=LEGACY_DATA & names)
    entries, origins = _complete_sources(root, secrets)
    _, monitored = _source_entries(root, source_manifest, secrets)
    for path in paths:
        _read_json_file(path, secrets)
    return {'status': 'preflight-passed', 'data_files': len(paths), 'source_files': len(origins),
            'monitored_sources': len(monitored),
            'internal_aliases': len(json.loads(entries['metadata/source-aliases.json'])),
            'provider_calls': 0, 'rds_reads': 0, 'production_writes': 0}


def newest_source_manifest(root, backup_base=Path('/root/backups')):
    """Choose the newest deployment manifest that matches current live bytes."""
    root = Path(root).resolve()
    candidates = sorted(Path(backup_base).glob('*/verification-manifest.json'),
                        key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        try:
            _source_entries(root, path, [])
            return path
        except (OSError, ValueError, TypeError, KeyError):
            continue
    raise ValueError('matching-source-manifest-unavailable')


def export_draws(root):
    # 2026-11-05: 821MiB 的 JSONL 导出若用 subprocess.run 捕获 stdout 再 splitlines()，
    # 会额外占用 ~1.6GiB；改为逐行流式读取（node 端本来就是一行一条记录）。
    process = subprocess.Popen(['node', '-e', NODE_EXPORT, str(root)], cwd=root,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    began = time.monotonic()
    try:
        value = {'version': 1, 'read_only': True, 'tables': {}}
        complete = False
        try:
            for raw in process.stdout:
                if time.monotonic() - began > 60:
                    raise TimeoutError('rds-readonly-export-timeout')
                item = json.loads(raw)
                if item.get('type') == 'header':
                    value.update({k: v for k, v in item.items() if k != 'type'})
                elif item.get('type') == 'table' and item.get('name') in RDS_TABLES:
                    if item['name'] in value['tables']:
                        raise ValueError('duplicate-rds-table')
                    value['tables'][item['name']] = {'create_table': item['create_table'], 'rows': []}
                elif item.get('type') == 'row' and item.get('name') in value['tables']:
                    value['tables'][item['name']]['rows'].append(item['row'])
                elif item.get('type') == 'complete':
                    complete = True
                else:
                    raise ValueError('unexpected-rds-export-record')
            if not complete:
                raise ValueError('incomplete-rds-export')
        except ValueError:
            raise ValueError('rds-export-invalid-json') from None
        if process.wait():
            raise ValueError('rds-readonly-export-failed')
        return value
    finally:
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except Exception:
                pass
        if process.poll() is None:
            process.kill()
            process.wait()


def _draw_proof(value, now):
    tables = value.get('tables') if isinstance(value, dict) else None
    if (not isinstance(tables, dict) or set(tables) != set(RDS_TABLES)
            or value.get('read_only') is not True
            or any(not isinstance(table, dict) or not isinstance(table.get('rows'), list)
                   or not isinstance(table.get('create_table'), str) for table in tables.values())
            or not tables['official_draws']['rows']):
        raise ValueError('rds-export-empty-or-invalid')
    periods = set()
    for row in tables['official_draws']['rows']:
        period = row.get('period')
        if not isinstance(period, str) or not re.fullmatch(r'[0-9]{8}(?:[0-2][0-9]{2})', period):
            raise ValueError('rds-period-invalid')
        index = int(period[-3:])
        try:
            slot = datetime.strptime(period[:8], '%Y%m%d').replace(tzinfo=TZ) + timedelta(minutes=5 * index)
        except ValueError:
            raise ValueError('rds-period-invalid') from None
        if not 1 <= index <= 288 or period in periods:
            raise ValueError('rds-period-duplicate-or-invalid')
        periods.add(period)
        if any(type(row.get('ball' + str(i))) is not int or not 0 <= row['ball' + str(i)] <= 9 for i in range(1, 5)):
            raise ValueError('rds-first-four-invalid')
        try:
            opened = datetime.fromisoformat(str(row.get('open_time')).replace('Z', '+00:00'))
            # api/draw_store.js::toMysqlDateTime serializes ISO UTC then
            # removes T/Z. The DB VARCHAR is UTC, not a Beijing wall time.
            opened = (opened.replace(tzinfo=timezone.utc).astimezone(TZ)
                      if opened.tzinfo is None else opened.astimezone(TZ))
        except ValueError:
            raise ValueError('rds-open-time-invalid') from None
        if not slot <= opened < slot + timedelta(minutes=1) or opened > now:
            raise ValueError('rds-open-time-invalid')
    return {'row_count': len(periods), 'first_period': min(periods), 'last_period': max(periods),
            'model_balls': [1, 2, 3, 4], 'ball5_display_only': True,
            'table_counts': {name: len(table['rows']) for name, table in tables.items()}}


def _snapshot_metadata_proof(manifest):
    def aware(value):
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError('naive-snapshot-time')
        return moment
    try:
        fs, db = manifest['filesystem_snapshot'], manifest['rds_snapshot']
        moments = [aware(manifest['started_at']), aware(fs['capture_started_at']),
                   aware(fs['capture_completed_at']), aware(fs['validation_completed_at']),
                   aware(db['transaction_started_at']), aware(db['export_completed_at'])]
        if (any(a > b for a, b in zip(moments, moments[1:]))
                or db['export_completed_at'] != manifest['completed_at']
                or type(fs['capture_elapsed_seconds']) not in (int, float) or fs['capture_elapsed_seconds'] < 0
                or fs['guarantee'] != 'identical-path-set-and-stat-bookends-during-raw-capture'
                or db['may_be_newer_than_filesystem'] is not True
                or manifest['consistency'] != 'independent-stable-files-and-readonly-rds-snapshots'
                or manifest['derived_summaries_authoritative'] is not False
                or manifest['recovery_policy'] != 'preserve-raw-seals-control-and-ledgers-recompute-pending-with-newer-draws-no-retro-predictions'):
            raise ValueError('snapshot-contract-invalid')
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('backup-snapshot-metadata-invalid') from None


def create_bundle(root, destination, source_manifest, *, exporter=_export_rds_streaming, clock=None):
    """One stable attempt. Caller schedules/retries; unstable snapshots fail closed."""
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if root in destination.parents or destination == root or destination.exists():
        raise ValueError('backup-destination-invalid')
    now = clock() if clock else datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('backup-clock-naive')
    secrets, config = _env_metadata(root)
    paths = discover(root)
    data_names = {str(path.relative_to(root)).replace('\\', '/') for path in paths}
    required = _required_members(data_names, legacy=LEGACY_DATA & data_names)
    entries, source_origins = _complete_sources(root, secrets)
    complete_source_paths = set(source_origins)
    manifest_entries, monitored_origins = _source_entries(root, source_manifest, secrets)
    entries.update(manifest_entries)
    source_origins.update(monitored_origins)
    capture_started = clock() if clock else datetime.now(TZ)
    captured, capture_seconds = capture_files(root, paths)
    capture_completed = clock() if clock else datetime.now(TZ)
    for path, raw in captured.items():
        _validate_json_bytes(raw, path.suffix, secrets)
        name = str(path.relative_to(root)).replace('\\', '/')
        entries[name] = raw
    validation_completed = clock() if clock else datetime.now(TZ)
    # RDS has a separate later read-only transaction. Its draws may be newer
    # than the immutable filesystem cut; no common transaction is claimed.
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, rds_temporary = tempfile.mkstemp(prefix='.rds-', suffix='.json', dir=destination.parent)
    os.close(handle)
    os.chmod(rds_temporary, 0o600)
    try:
        exported = exporter(root, rds_temporary)
        finished = clock() if clock else datetime.now(TZ)
        if isinstance(exported, dict):
            # 兼容以物化 draws 返回的旧 exporter（测试/外部调用），内存占用较高。
            exported_at = exported.get('exported_at')
            proof = _draw_proof(exported, finished)
            rds_size, rds_sha = _write_canonical(exported, rds_temporary)
            _check_secrets(b'', secrets, parsed=exported)
            del exported
        else:
            # 默认流式路径：导出时已做行级校验/凭据扫描，canonical 字节数即归档大小。
            exported_at = exported.exported_at
            proof = exported.proof
            rds_size, rds_sha = exported.size, exported.sha256
            if exported.max_opened is not None and exported.max_opened > finished:
                raise ValueError('rds-open-time-invalid')
        _check_secrets_file(rds_temporary, secrets)
        entries['rds/business-tables.json'] = _LazyBlob(rds_temporary, rds_size, rds_sha)
        entries['metadata/safe-config.json'] = _canonical(config)
        after_sources, after_origins = _complete_sources(root, secrets)
        if (set(after_origins) != complete_source_paths
                or entries['metadata/source-aliases.json'] != after_sources['metadata/source-aliases.json']
                or any(_file_hash(path) != digest for path, digest in source_origins.items())):
            raise ValueError('sources-changed-during-backup')
        if sum(_entry_len(raw) for raw in entries.values()) > MAX_TOTAL:
            raise ValueError('backup-too-large')
        manifest = {'version': VERSION, 'started_at': now.isoformat(), 'completed_at': finished.isoformat(),
                    'business_timezone': 'Asia/Shanghai', 'business_reset_hour': 5,
                    'consistency': 'independent-stable-files-and-readonly-rds-snapshots',
                    'filesystem_snapshot': {'capture_started_at': capture_started.isoformat(),
                        'capture_completed_at': capture_completed.isoformat(),
                        'validation_completed_at': validation_completed.isoformat(),
                        'capture_elapsed_seconds': capture_seconds,
                        'guarantee': 'identical-path-set-and-stat-bookends-during-raw-capture'},
                    'rds_snapshot': {'transaction_started_at': exported_at,
                        'export_completed_at': finished.isoformat(), 'may_be_newer_than_filesystem': True},
                    'recovery_policy': 'preserve-raw-seals-control-and-ledgers-recompute-pending-with-newer-draws-no-retro-predictions',
                    'derived_summaries_authoritative': False,
                    'display_snapshot_consistency': 'best-effort-immutable-copy-not-state-transaction',
                    'rds': proof, 'source_count': len(source_origins), 'credentials_included': False,
                    'monitored_source_count': len(monitored_origins),
                    'required_evidence': required,
                    'production_restore_supported': False,
                    'files': {name: {'sha256': _entry_sha(raw), 'size': _entry_len(raw)}
                              for name, raw in sorted(entries.items())}}
        _snapshot_metadata_proof(manifest)
        handle, temporary = tempfile.mkstemp(prefix='.evidence-', suffix='.tar.gz', dir=destination.parent)
        os.close(handle)
        os.chmod(temporary, 0o600)
        try:
            with tarfile.open(temporary, 'w:gz', compresslevel=1) as archive:
                merged = {**entries, 'backup-manifest.json': _canonical(manifest)}
                for name, raw in sorted(merged.items()):
                    item = tarfile.TarInfo(name); item.size = _entry_len(raw); item.mode = 0o600
                    if isinstance(raw, _LazyBlob):
                        with open(raw.path, 'rb') as source:
                            archive.addfile(item, source)
                    else:
                        archive.addfile(item, io.BytesIO(raw))
            with open(temporary, 'r+b') as file:
                os.fsync(file.fileno())
            os.replace(temporary, destination)
            if os.name != 'nt':
                directory = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()
        return {'status': 'created-unverified', 'archive': str(destination), 'archive_sha256': _file_hash(destination),
                'file_count': len(entries), 'source_count': len(source_origins), 'rds': proof,
                'capture_elapsed_seconds': capture_seconds,
                'provider_calls': 0, 'production_restore': False}
    finally:
        if Path(rds_temporary).exists():
            Path(rds_temporary).unlink()


def verify_bundle(path, *, temporary_parent=None):
    """Extract regular bytes into a fresh temporary tree, validate, then remove.

    No archived Python/JS/SQL is executed. DB contents remain JSON, and no
    production directory is accepted as an extraction target.
    """
    with tempfile.TemporaryDirectory(prefix='penta5-restore-check-', dir=temporary_parent) as folder:
        root = Path(folder)
        members = {}
        total = 0
        with tarfile.open(path, 'r:gz') as archive:
            for item in archive:
                name = str(_safe_relative(item.name))
                total += item.size
                if not item.isfile() or not 0 <= item.size <= MAX_MEMBER or total > MAX_TOTAL or name in members:
                    raise ValueError('unsafe-archive-member')
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, size = sha256(), 0
                with archive.extractfile(item) as source, target.open('wb') as output:
                    while True:
                        chunk = source.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk); digest.update(chunk); output.write(chunk)
                if size != item.size:
                    raise ValueError('truncated-archive-member')
                members[name] = {'sha256': digest.hexdigest(), 'size': size}
        try:
            manifest = json.loads((root / 'backup-manifest.json').read_bytes())
            members.pop('backup-manifest.json')
        except (ValueError, KeyError):
            raise ValueError('backup-manifest-invalid') from None
        if manifest.get('version') != VERSION or set(manifest.get('files', {})) != set(members):
            raise ValueError('backup-manifest-member-mismatch')
        _snapshot_metadata_proof(manifest)
        for name, actual in members.items():
            row = manifest['files'][name]
            if row != actual:
                raise ValueError('backup-member-hash-mismatch')
            if name.endswith(('.json', '.jsonl')) and name != 'rds/business-tables.json':
                p = root / name
                _read_json_file(p, [])
        now = datetime.fromisoformat(manifest['completed_at'])
        proof = _draw_proof(json.loads((root / 'rds/business-tables.json').read_bytes()), now)
        if proof != manifest.get('rds') or manifest.get('business_reset_hour') != 5:
            raise ValueError('backup-recovery-proof-mismatch')
        required = _required_members(members, legacy=LEGACY_DATA & set(members))
        if manifest.get('required_evidence') != required:
            raise ValueError('backup-required-evidence-proof-mismatch')
        control = json.loads((root / 'data/ai_api_control.json').read_bytes())
        checksum = control.get('checksum')
        if checksum != _hash(_canonical({k: v for k, v in control.items() if k != 'checksum'})):
            raise ValueError('backup-manual-control-checksum')
        transitions = control.get('transitions')
        if (type(control.get('enabled')) is not bool or type(control.get('generation')) is not int
                or not isinstance(transitions, list) or len(transitions) != control['generation']
                or not transitions or transitions[-1].get('enabled') != control['enabled']
                or any(row.get('generation') != i for i, row in enumerate(transitions, 1))):
            raise ValueError('backup-manual-control-history')
        source_manifest = json.loads((root / 'metadata/source-manifest.json').read_bytes())
        aliases = json.loads((root / 'metadata/source-aliases.json').read_bytes())
        if not isinstance(aliases, list):
            raise ValueError('backup-alias-metadata-invalid')
        for alias in aliases:
            _safe_relative(alias['path']); _safe_relative(alias['target'])
            target = 'sources/' + alias['target']
            covered = (target in members if alias['kind'] == 'file'
                       else alias['kind'] == 'directory' and any(name.startswith(target + '/') for name in members))
            if not covered or alias.get('restore_policy') != 'recreate-relative-link-after-target-verified':
                raise ValueError('backup-alias-target-unavailable')
        monitored = source_manifest.get('files', [])
        if not isinstance(monitored, list) or len(monitored) < 91:
            raise ValueError('backup-source-proof-incomplete')
        for row in monitored:
            name = row.get('path')
            source_name = ('sources/_root/run_stats.py' if name == '/root/run_stats.py'
                           else 'sources/' + name)
            if source_name not in members or members[source_name]['sha256'] != row.get('candidate_sha256'):
                raise ValueError('backup-source-proof-mismatch')
        # Tombstones, manual state/history, inputs and seals remain exact bytes.
        # Importing validators could alter semantics or launch side effects;
        # isolated verification checks their manifest membership/hashes only.
        return {'status': 'verified-isolated', 'file_count': len(members),
                'source_count': manifest.get('source_count'), 'rds': proof,
                'executed_archived_code': False, 'provider_calls': 0,
                'production_restore': False, 'temporary_tree_removed': True}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--root', default='/root/penta5-runner')
    create.add_argument('--destination', required=True)
    create.add_argument('--source-manifest', required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('archive')
    args = parser.parse_args()
    try:
        result = (create_bundle(args.root, args.destination, args.source_manifest)
                  if args.command == 'create' else verify_bundle(args.archive))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as error:
        # No path/env/provider/database error body enters logs.
        code = str(error) if type(error) is ValueError and re.fullmatch(r'[a-z0-9-]+', str(error)) else type(error).__name__
        print(json.dumps({'status': 'failed', 'code': code, 'provider_calls': 0}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
