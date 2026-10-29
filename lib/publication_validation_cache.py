"""Optional, signed acceleration for self-contained published-proof validation.

Only an explicit statistics session can reuse successes. Mutable draws,
settlements, conflicts, current selection and money are never cached here.
Losing any cache material restores native verification rather than evidence.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from hashlib import sha256
from importlib import import_module
from pathlib import Path
import fcntl
import hmac
import json
import os
import secrets
import stat
import tempfile

from ai_verified_stats import next_period
from stats_history import TZ, parse_period

DOMAIN='penta5-published-validation-v1'
MAX_TICKETS=4096
MAX_BYTES=8*1024*1024
_MODELS={'selected7':'selected_framework_model','selected6':'selected_six_model'}
_DEPENDENCIES=(
    'publication_validation_cache.py',
    'selected_framework_model.py','selected_framework_contract.py','selected_framework_view.py',
    'selected_six_model.py','selected_six_contract.py','selected_six_view.py',
    'selected_request_options.py','selected_six_request_options.py','selected_strategy_validation.py',
    'ai_strategy_validation.py','stat50_compare.py','ai_verified_stats.py','stats_history.py',
    'position_input_features.py','strategy_features.py','selected_framework_attack.py',
    'selected_single_view.py','ai_prediction_guard.py','ai_model_context.py',
    'killer_ai_client.py','framework_ai_client.py','ai_operation_window.py',
    'ai_request_deadline.py','scripts/run_ai_ready_job.py',
)
_DEFAULT_CODE_ROOT=Path(__file__).resolve().parent
_CURRENT=ContextVar('penta5_publication_validation_cache',default=None)
_ROOT_BASELINES={}
_FIELDS=frozenset(('domain','model','reference','published','proof_sha256','fingerprint','validated_at'))


def _strict_json(value,depth=0):
    if depth>100:raise ValueError('deep-cache-json')
    kind=type(value)
    if value is None or kind in (bool,int,float,str):return
    if kind is list:
        for item in value:_strict_json(item,depth+1)
    elif kind is dict:
        for key,item in value.items():
            if type(key) is not str:raise ValueError('nonstring-cache-key')
            _strict_json(item,depth+1)
    else:raise ValueError('non-json-cache-value')


def _packed(value):
    _strict_json(value)
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def _pairs(items):
    value={}
    for key,item in items:
        if key in value:raise ValueError('duplicate-cache-key')
        value[key]=item
    return value


def _at(value):
    if isinstance(value,str) and len(value)<=64:
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('invalid-cache-clock')
    return value.astimezone(TZ)


def _dependency_path(code_root,name):
    """Resolve a dependency from the code root, then from the project root.

    After the lib/ migration most modules live in lib/, but a few entries
    (for example scripts/run_ai_ready_job.py) still live at the project root.
    Resolving only against the code root raised FileNotFoundError, which
    silently disabled the whole validation cache.
    """
    for base in (code_root,code_root.parent):
        path=base/name
        try:
            path.stat()
            return path
        except OSError:
            continue
    return None


def _dependency_paths(code_root):
    result=[]
    for name in _DEPENDENCIES:
        path=_dependency_path(code_root,name)
        if path is not None:
            result.append(path)
    return result


def _signature(code_root):
    paths=_dependency_paths(code_root)
    paths.append(Path(__file__).resolve())
    result=[]
    for path in paths:
        info=path.stat()
        result.append((str(path),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns))
    return tuple(result)


try:_IMPORT_SIGNATURE=_signature(_DEFAULT_CODE_ROOT)
except OSError:_IMPORT_SIGNATURE=None


def _native(model):
    return import_module(_MODELS[model]).validate_publication


def _private(path,mode,directory=False):
    info=path.lstat()
    if (stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (os.name!='nt' and (info.st_mode & 0o077 or info.st_uid!=os.geteuid()))):
        raise ValueError('unsafe-cache-file')
    return info


def _read_key(directory,create):
    path=directory/'validation.key'
    nofollow=getattr(os,'O_NOFOLLOW',0)
    if create and not path.exists():
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|nofollow,0o600)
        except FileExistsError:pass
        else:
            with os.fdopen(fd,'wb') as stream:
                stream.write(secrets.token_bytes(32));stream.flush();os.fsync(stream.fileno())
    _private(path,0o600)
    with os.fdopen(os.open(path,os.O_RDONLY|nofollow),'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or (os.name!='nt' and
                (info.st_mode & 0o077 or info.st_uid!=os.geteuid()))):raise ValueError('unsafe-cache-key')
        key=stream.read(33)
    if len(key)!=32:raise ValueError('invalid-cache-key')
    return key


def _identifier(row):
    return sha256(_packed({key:row[key] for key in _FIELDS if key!='validated_at'})).hexdigest()


class ProofValidationCache:
    def __init__(self,root,*,persistent=True,enabled=True,code_root=None):
        self.root=Path(root).resolve()
        self.code_root=Path(code_root).resolve() if code_root is not None else _DEFAULT_CODE_ROOT
        self.metrics={'native_calls':0,'memory_hits':0,'persistent_hits':0,'read_errors':0,
                      'write_errors':0,'disabled':not enabled}
        self.tickets={};self.memory={};self.dirty={};self.key=None;self.fingerprint=None
        self.directory=self.root/'data/publication-validation-cache'
        self._files=None
        if not enabled:return
        try:
            before=_signature(self.code_root)
            baseline=_ROOT_BASELINES.setdefault(str(self.code_root),before)
            if baseline!=before or (self.code_root==_DEFAULT_CODE_ROOT and before!=_IMPORT_SIGNATURE):
                raise ValueError('cache-source-changed')
            files={str(path.relative_to(self.code_root.parent)):sha256(path.read_bytes()).hexdigest()
                   for path in _dependency_paths(self.code_root)}
            files['loaded-cache-implementation']=sha256(Path(__file__).read_bytes()).hexdigest()
            self.fingerprint=sha256(_packed({'domain':DOMAIN,'files':files})).hexdigest()
            self._files=before
            if not self._stable():raise ValueError('cache-source-changed')
        except Exception:
            self.metrics['disabled']=True;return
        if persistent:
            try:
                if not self.root.is_dir():raise ValueError('missing-cache-root')
                (self.root/'data').mkdir(exist_ok=True)
                self.directory.mkdir(mode=0o700,exist_ok=True)
                _private(self.directory,0o700,directory=True)
                self.key=_read_key(self.directory,True)
                self.tickets=self._read_tickets()
            except Exception:
                self.metrics['read_errors']+=1
                # Memory reuse still requires a native success in this process.
                self.key=None;self.tickets={}

    def _stable(self):
        try:stable=self._files is not None and _signature(self.code_root)==self._files
        except OSError:stable=False
        if not stable:self.metrics['disabled']=True
        return stable

    def _read_tickets(self):
        path=self.directory/'tickets.cache'
        if not path.exists():return {}
        info=_private(path,0o600)
        if info.st_size>MAX_BYTES:raise ValueError('oversized-cache')
        with os.fdopen(os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)),'rb') as stream:
            opened=os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size>MAX_BYTES or (os.name!='nt' and
                    (opened.st_mode & 0o077 or opened.st_uid!=os.geteuid()))):raise ValueError('unsafe-cache-file')
            raw=stream.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValueError('oversized-cache')
        value=json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite-cache')))
        if (not isinstance(value,dict) or set(value)!={'version','domain','entries'}
                or type(value['version']) is not int or value['version']!=1 or value['domain']!=DOMAIN
                or not isinstance(value['entries'],list) or len(value['entries'])>MAX_TICKETS):
            raise ValueError('invalid-cache-schema')
        result={}
        for signed in value['entries']:
            if not isinstance(signed,dict) or set(signed)!=_FIELDS|{'signature'}:
                raise ValueError('invalid-cache-ticket')
            row={key:signed[key] for key in _FIELDS}
            if (row['domain']!=DOMAIN or row['model'] not in _MODELS or row['published'] is not True
                    or not isinstance(row['reference'],str) or not parse_period(row['reference'])
                    or parse_period(row['reference'])[0]!=row['reference']
                    or any(not isinstance(row[key],str) or len(row[key])!=64
                           or any(char not in '0123456789abcdef' for char in row[key])
                           for key in ('proof_sha256','fingerprint'))
                    or not isinstance(signed['signature'],str)):
                raise ValueError('invalid-cache-ticket')
            _at(row['validated_at'])
            expected=hmac.new(self.key,_packed(row),'sha256').hexdigest()
            if not hmac.compare_digest(signed['signature'],expected):raise ValueError('invalid-cache-signature')
            identity=_identifier(row)
            if identity in result:raise ValueError('duplicate-cache-ticket')
            if row['fingerprint']==self.fingerprint:result[identity]=row
        return result

    def check(self,model,proof,reference,now,published,validator):
        self.metrics['native_calls']+=1
        if not published or self.metrics['disabled'] or not self._stable():
            return validator(proof,reference,now,published=published)
        try:
            if not isinstance(now,datetime):raise ValueError('non-datetime-cache-clock')
            current=_at(now)
            if (not isinstance(reference,str) or not parse_period(reference) or parse_period(reference)[0]!=reference
                    or not isinstance(proof,dict) or proof.get('ref_period')!=reference
                    or proof.get('target_period')!=next_period(reference) or proof.get('publication_state')!='published'
                    or not _at(proof.get('generated_at'))<=_at(proof.get('published_at'))<=current):
                raise ValueError('cache-ineligible-publication')
            original=_packed(proof)
            row={'domain':DOMAIN,'model':model,'reference':reference,'published':True,
                 'proof_sha256':sha256(original).hexdigest(),'fingerprint':self.fingerprint,
                 'validated_at':current.isoformat()}
            identity=_identifier(row)
            previous=self.memory.get(identity) or self.tickets.get(identity)
            if previous is not None and _at(previous['validated_at'])<=current:
                self.metrics['native_calls']-=1
                self.metrics['memory_hits' if identity in self.memory else 'persistent_hits']+=1
                return proof
        except Exception:
            return validator(proof,reference,now,published=published)
        checked=validator(proof,reference,now,published=published)
        # Native normalization or source changes must not issue a reusable ticket.
        try:
            if self._stable() and _packed(checked)==original and _packed(proof)==original:
                self.memory[identity]=row
                if self.key is not None:self.dirty[identity]=row
        except Exception:pass
        return checked

    def flush(self):
        if not self.dirty or self.key is None or self.metrics['disabled'] or not self._stable():return
        temporary=None
        try:
            _private(self.directory,0o700,directory=True)
            lock_path=self.directory/'tickets.lock'
            fd=os.open(lock_path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'a+b') as lock:
                _private(lock_path,0o600)
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                if _read_key(self.directory,False)!=self.key:raise ValueError('cache-key-rotated')
                try:existing=self._read_tickets()
                except (OSError,ValueError,TypeError,KeyError,OverflowError):existing={}
                existing.update(self.dirty)
                rows=sorted(existing.values(),key=lambda row:(_at(row['validated_at']),_identifier(row)))[-MAX_TICKETS:]
                entries=[{**row,'signature':hmac.new(self.key,_packed(row),'sha256').hexdigest()} for row in rows]
                data=_packed({'version':1,'domain':DOMAIN,'entries':entries})+b'\n'
                if len(data)>MAX_BYTES:raise ValueError('oversized-cache')
                with tempfile.NamedTemporaryFile(dir=self.directory,delete=False) as stream:
                    temporary=Path(stream.name);os.chmod(temporary,0o600)
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,self.directory/'tickets.cache');temporary=None
                if os.name!='nt':
                    directory=os.open(self.directory,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                    try:os.fsync(directory)
                    finally:os.close(directory)
                self.tickets=existing;self.dirty={}
        except Exception:self.metrics['write_errors']+=1
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass


@contextmanager
def session(root,*,persistent=True,enabled=True,code_root=None):
    cache=ProofValidationCache(root,persistent=persistent,enabled=enabled,code_root=code_root)
    token=_CURRENT.set(cache)
    try:yield cache
    finally:
        _CURRENT.reset(token)
        try:cache.flush()
        except Exception:cache.metrics['write_errors']+=1


def validate_publication(model,proof,reference,now,*,published=True,root=None):
    if model not in _MODELS:raise ValueError('unknown-publication-cache-model')
    validator=_native(model)
    cache=_CURRENT.get()
    if cache is None or (root is not None and Path(root).resolve()!=cache.root):
        return validator(proof,reference,now,published=published)
    return cache.check(model,proof,reference,now,published,validator)
