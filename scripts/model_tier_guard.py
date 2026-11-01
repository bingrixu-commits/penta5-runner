"""Balance-aware model tier guard.

按 DeepSeek 余额自动切换 kill 框架的模型档位（充值后自动归位 Pro，欠费时回退 flash）：
- balance >= pro_on_balance      -> 目标全量 deepseek-v4-pro（缺失键自动补齐）
- balance <  flash_below_balance -> 目标全量 deepseek-flash
- 两者之间                        -> 保持现状（记录 hold，不回摆）
配置文件 data/model_tier_guard.json（缺失时用默认值并写回）；写前备份 .env 到
/root/ops/model-tier-guard/<ts>/.env.bak，原子替换；日志 data/model_tier_guard.log。
--dry-run 只打印决策不写盘；--balance X 覆盖余额（测试用）。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/root/penta5-runner')
ENV = ROOT / '.env'
DATA = ROOT / 'data'
STATE = DATA / 'model_tier_guard.json'
LOG = DATA / 'model_tier_guard.log'
GUARD_STATE = DATA / 'ai_balance_guard.json'
BACKUP_DIR = Path('/root/ops/model-tier-guard')
TZ = ZoneInfo('Asia/Shanghai')

DEFAULTS = {
    'version': 1,
    'enabled': True,
    'targets': ['KILL1', 'KILL3'],
    'pro_model': 'deepseek-v4-pro',
    'flash_model': 'deepseek-flash',
    'pro_on_balance': 50.0,
    'flash_below_balance': 15.0,
    'last_balance': None,
    'last_decision': None,
    'last_action': None,
    'last_checked_at': None,
    'last_action_at': None,
}


def _now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def _log(text):
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as handle:
        handle.write(_now() + ' ' + text + chr(10))


def _load_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(default)
    if not isinstance(value, dict):
        return dict(default)
    merged = dict(default)
    merged.update(value)
    return merged


def _save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=1)
            stream.write(chr(10))
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _provider_balance():
    try:
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / 'lib'))
        from app_secrets import get_secret
        import requests
        key = get_secret('PENTA5_FRAMEWORK_API_KEY')
        response = requests.get('https://api.deepseek.com/user/balance',
                                headers={'Authorization': 'Bearer ' + key}, timeout=10)
        if response.status_code != 200:
            return None
        infos = (response.json() or {}).get('balance_infos') or []
        for item in infos:
            if item.get('currency') == 'CNY':
                return float(item.get('total_balance'))
        return None
    except Exception:
        return None


def _fallback_balance():
    state = _load_json(GUARD_STATE, {})
    value = state.get('last_balance')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _read_env():
    try:
        return ENV.read_text(encoding='utf-8')
    except OSError:
        return ''


def _parse_models(text, targets):
    result = {}
    lines = text.splitlines()
    for target in targets:
        key = 'PENTA5_MODEL_' + target
        value = None
        for line in lines:
            if line.startswith(key + '='):
                value = line.split('=', 1)[1].strip()
        result[target] = value
    return result


def _render_env(text, targets, model):
    lines = text.splitlines()
    trailing = text.endswith(chr(10))
    changed = {}
    missing = []
    for target in targets:
        key = 'PENTA5_MODEL_' + target
        found = False
        for index, line in enumerate(lines):
            if line.startswith(key + '='):
                old = line.split('=', 1)[1].strip()
                if old != model:
                    lines[index] = key + '=' + model
                    changed[target] = [old, model]
                found = True
                break
        if not found:
            missing.append(key)
    if missing:
        insert_at = len(lines)
        for index, line in enumerate(lines):
            if line.startswith('PENTA5_MODEL_'):
                insert_at = index + 1
        for offset, key in enumerate(missing):
            lines.insert(insert_at + offset, key + '=' + model)
            changed[key.replace('PENTA5_MODEL_', '')] = [None, model]
    rendered = chr(10).join(lines)
    if trailing:
        rendered += chr(10)
    return rendered, changed


def _backup_env():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
    folder = BACKUP_DIR / stamp
    folder.mkdir(parents=True, exist_ok=True)
    if ENV.exists():
        shutil.copy2(ENV, folder / '.env.bak')
    return str(folder)


def _write_env(rendered):
    handle, temp = tempfile.mkstemp(dir=str(ENV.parent), prefix='.env.')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(rendered)
        os.chmod(temp, 0o600)
        os.replace(temp, ENV)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--balance', type=float)
    args = parser.parse_args()

    state = _load_json(STATE, DEFAULTS)
    config = _load_json(STATE, DEFAULTS)
    if not config.get('enabled', True):
        print(json.dumps({'decision': 'disabled'}, ensure_ascii=False))
        return 0

    balance = args.balance
    source = 'argument'
    if balance is None:
        balance = _provider_balance()
        source = 'provider'
    if balance is None:
        balance = _fallback_balance()
        source = 'guard-state'
    if balance is None:
        _log('balance unknown; skip')
        print(json.dumps({'decision': 'unknown', 'balance': None}, ensure_ascii=False))
        return 0

    targets = [str(item).upper() for item in (config.get('targets') or DEFAULTS['targets'])]
    pro_model = config.get('pro_model') or DEFAULTS['pro_model']
    flash_model = config.get('flash_model') or DEFAULTS['flash_model']
    pro_on = float(config.get('pro_on_balance', DEFAULTS['pro_on_balance']))
    flash_below = float(config.get('flash_below_balance', DEFAULTS['flash_below_balance']))

    text = _read_env()
    current = _parse_models(text, targets)
    if balance >= pro_on:
        desired = pro_model
    elif balance < flash_below:
        desired = flash_model
    else:
        desired = None

    decision = 'hold'
    changed = {}
    if desired is not None and any(current[target] != desired for target in targets):
        rendered, changed = _render_env(text, targets, desired)
        decision = 'switch-pro' if desired == pro_model else 'switch-flash'
        if not args.dry_run:
            folder = _backup_env()
            _write_env(rendered)
            state['last_action'] = decision
            state['last_action_at'] = _now()
            _log('decision=%s balance=%.4f source=%s backup=%s changed=%s' % (
                decision, balance, source, folder, json.dumps(changed, sort_keys=True)))
    else:
        decision = 'hold-pro' if (desired == pro_model) else ('hold-flash' if desired == flash_model else 'hold-mid')
        if not args.dry_run:
            _log('decision=%s balance=%.4f source=%s targets=%s current=%s' % (
                decision, balance, source, ','.join(targets), json.dumps(current, sort_keys=True)))

    if not args.dry_run:
        state['last_balance'] = round(balance, 4)
        state['last_checked_at'] = _now()
        state['last_decision'] = decision
        _save_json(STATE, state)

    print(json.dumps({'decision': decision, 'balance': round(balance, 4), 'source': source,
                      'desired': desired, 'current': current, 'changed': changed,
                      'dry_run': bool(args.dry_run)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
