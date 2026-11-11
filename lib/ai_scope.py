"""按框架隔离 AI 调用：开关 / 密钥 / 余额，三个维度各自独立。

设计原则：不改动既有 `ai_call_control`（带校验和与审计的全局开关），
只在其之上叠加一层“作用域”，并保持向后兼容：

- 未设置 PENTA5_AI_SCOPE 的进程 → 行为与以前完全一致（只受全局开关约束）；
- 设置了 PENTA5_AI_SCOPE=kill15 → 额外受 kill15 作用域开关约束；
- 若该作用域配置了独立密钥（PENTA5_API_KEY_KILL15），`with_scope.sh`
  会把它注入 DEEPSEEK_API_KEY，从而实现额度隔离。

状态文件 data/ai_scope_control.json：
{
  "version": 1,
  "updated_at": "...",
  "scopes": {
    "kill15": {"enabled": true, "generation": 3, "auto_paused": false,
                "last_balance": 12.3, "paused_at": null, "updated_at": "..."}
  }
}
"""
from datetime import datetime
from pathlib import Path
import contextlib
import json
import os

ROOT = Path('/root/penta5-runner')
STATE_PATH = ROOT / 'data' / 'ai_scope_control.json'
LOCK_PATH = ROOT / 'data' / 'ai_scope_control.json.lock'

# 作用域注册表：scope -> 展示名 / 独立密钥环境变量
SCOPES = {
    'kill1':     {'label': 'kill1 双定位杀码',    'key_env': 'PENTA5_API_KEY_KILL1'},
    'kill3':     {'label': 'kill3 定位杀码',      'key_env': 'PENTA5_API_KEY_KILL3'},
    'kill10':    {'label': 'kill10 定位杀码',     'key_env': 'PENTA5_API_KEY_KILL10'},
    'kill11':    {'label': 'kill11 三定位杀码',   'key_env': 'PENTA5_API_KEY_KILL11'},
    'kill12':    {'label': 'kill12 定位杀码',     'key_env': 'PENTA5_API_KEY_KILL12'},
    'kill13':    {'label': 'kill13 定位杀码',     'key_env': 'PENTA5_API_KEY_KILL13'},
    'kill14':    {'label': 'kill14 四位置排除',   'key_env': 'PENTA5_API_KEY_KILL14'},
    'kill15':    {'label': 'kill15 分位杀号',     'key_env': 'PENTA5_API_KEY_KILL15'},
    'cold15':    {'label': 'cold15 冷号',         'key_env': 'PENTA5_API_KEY_COLD15'},
    'cold30':    {'label': 'cold30 冷号',         'key_env': 'PENTA5_API_KEY_COLD30'},
    'killer':    {'label': 'killer 主杀号任务',   'key_env': 'PENTA5_API_KEY_KILLER'},
    'framework': {'label': 'framework 框架任务',  'key_env': 'PENTA5_API_KEY_FRAMEWORK'},
    'he3':       {'label': 'he3 精修',            'key_env': 'PENTA5_API_KEY_HE3'},
    'replay':    {'label': '回放/实验',           'key_env': 'PENTA5_API_KEY_REPLAY'},
}
DEFAULT_SCOPE_ENV = 'PENTA5_AI_SCOPE'
ENV_PATH = ROOT / '.env'
_env_loaded = False


def load_env_once():
    """把 .env 读进 os.environ（setdefault，不覆盖已注入的值）。

    cron 进程不继承 .env，CLI 与余额守卫都需要这一步，否则看不到独立密钥。
    """
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        raw = ENV_PATH.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def current_scope():
    return (os.getenv(DEFAULT_SCOPE_ENV) or '').strip().lower()


def _now():
    return datetime.now().astimezone().isoformat()


@contextlib.contextmanager
def _locked():
    """跨进程互斥；锁文件不存在时自动创建。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, 'a+')
    try:
        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        try:
            handle.close()
        except Exception:
            pass


def _empty():
    return {'version': 1, 'updated_at': _now(), 'scopes': {}}


def read_state():
    try:
        document = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(document, dict) or not isinstance(document.get('scopes'), dict):
        return _empty()
    return document


def _write_state(document):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    document['updated_at'] = _now()
    tmp = STATE_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, STATE_PATH)


def scope_entry(document, scope):
    entry = (document.get('scopes') or {}).get(scope)
    return entry if isinstance(entry, dict) else {}


def is_scope_enabled(scope, document=None):
    """未登记的作用域默认开启（保持旧行为）。"""
    document = document or read_state()
    entry = scope_entry(document, scope)
    return entry.get('enabled') is not False


def set_scope_enabled(scope, enabled, *, note=None, auto=False):
    """CAS 语义用 generation 保护，返回新的 entry。"""
    if scope not in SCOPES:
        raise ValueError('unknown-ai-scope:%s' % scope)
    with _locked():
        document = read_state()
        entry = dict(scope_entry(document, scope))
        entry['enabled'] = bool(enabled)
        entry['generation'] = int(entry.get('generation') or 0) + 1
        entry['updated_at'] = _now()
        entry['auto_paused'] = bool(auto) if not enabled else False
        if auto:
            entry['auto_source'] = 'balance-guard'
        if not enabled and not auto:
            entry['paused_at'] = _now()
        if enabled:
            entry['resumed_at'] = _now()
        if note:
            entry['note'] = str(note)[:120]
        document.setdefault('scopes', {})[scope] = entry
        _write_state(document)
        return entry


def require_scope_enabled(scope, *, clock=None):
    """在全局开关之后、每次外发请求之前调用。"""
    if not scope:
        return True
    if not is_scope_enabled(scope):
        from ai_call_control import AICallsDisabled
        raise AICallsDisabled('ai-scope-stopped:%s' % scope)
    return True


def key_for(scope):
    """返回该作用域的独立密钥；未配置则返回空串（调用方回退共享密钥）。"""
    if not scope:
        return ''
    spec = SCOPES.get(scope)
    if not spec:
        return ''
    load_env_once()
    value = os.getenv(spec['key_env']) or ''
    return value.strip()


def scopes_with_keys(document=None):
    """[(scope, key)] —— 已配置独立密钥的作用域，供余额守卫逐个核对。"""
    document = document or read_state()
    load_env_once()
    result = []
    for scope in SCOPES:
        key = key_for(scope)
        if key:
            result.append((scope, key))
    return result


def describe(document=None):
    document = document or read_state()
    rows = []
    for scope, spec in SCOPES.items():
        entry = scope_entry(document, scope)
        rows.append({
            'scope': scope,
            'label': spec['label'],
            'enabled': entry.get('enabled') is not False,
            'own_key': bool(key_for(scope)),
            'generation': entry.get('generation'),
            'auto_paused': entry.get('auto_paused', False),
            'last_balance': entry.get('last_balance'),
            'updated_at': entry.get('updated_at'),
        })
    return rows
