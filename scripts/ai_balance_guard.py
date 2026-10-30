#!/usr/bin/env python3
"""余额守护：DeepSeek 欠费自动停 AI 出口，缴费后自动恢复（并让既有 cron 用最新数据继续跑）。

设计约束（刻意做窄，避免影响手动开关语义）：
- 唯一的 AI 总闸是 data/ai_api_control.json；本脚本只通过 ai_call_control.set_enabled
  写它，保留 CAS generation 链与审计，不直接改 JSON。
- 连续两次确认 is_available=false 才自动停（防余额接口抖动）。
- 只自动恢复「自己停掉的」那一代：停的时候记录 generation，恢复前校验 generation
  仍相等；若期间有人手动改过开关，则清掉标记、尊重手动状态。
- 恢复后不补历史期、不回放旧数据；既有 */5 的 AI cron 在下一轮自动读取
  「最近 50 期」最新开奖数据继续发布。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_call_control import AICallsDisabled, read_control, set_enabled  # noqa: E402
from stats_history import beijing_now  # noqa: E402

BALANCE_URL = 'https://api.deepseek.com/user/balance'
BALANCE_TIMEOUT = 10
BALANCE_ENV_KEYS = ('PENTA5_FRAMEWORK_API_KEY', 'PENTA5_KILLER_API_KEY')
STATE_NAME = 'ai_balance_guard.json'
PAUSE_CONFIRMATIONS = 2


def state_path(root):
    return Path(root) / 'data' / STATE_NAME


def default_state():
    return {'version': 1, 'auto_paused': False, 'paused_generation': None,
            'paused_at': None, 'resumed_at': None, 'consecutive_unavailable': 0,
            'last_checked_at': None, 'last_balance': None, 'last_is_available': None,
            'last_action': None, 'last_error': None}


def load_state(path):
    state = default_state()
    try:
        loaded = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return state
    if isinstance(loaded, dict):
        for key in state:
            if key in loaded:
                state[key] = loaded[key]
    return state


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')), encoding='utf-8')
    os.replace(tmp, path)


def read_env_secret(env_path, names):
    values = {}
    try:
        text = Path(env_path).read_text(encoding='utf-8')
    except OSError as error:
        return None, 'env-unreadable:%s' % type(error).__name__
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key in names:
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            if value:
                values[key] = value
    for name in names:
        if values.get(name):
            return name, values[name]
    return None, 'no-api-key'


def fetch_balance(url, secret, timeout=BALANCE_TIMEOUT):
    request = urllib.request.Request(
        url, headers={'Authorization': 'Bearer ' + secret, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as error:
        return {'ok': False, 'error': 'http-%s' % error.code}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': type(error).__name__}
    try:
        infos = payload.get('balance_infos') or []
        first = infos[0] if infos else {}
        raw_balance = first.get('total_balance')
        balance = float(raw_balance) if raw_balance is not None else None
        is_available = payload.get('is_available')
        if not isinstance(is_available, bool):
            return {'ok': False, 'error': 'invalid-is-available'}
        if balance is None:
            return {'ok': False, 'error': 'missing-total-balance'}
    except (AttributeError, TypeError, ValueError):
        return {'ok': False, 'error': 'balance-parse-failed'}
    return {'ok': True, 'is_available': is_available, 'total_balance': balance,
            'currency': str(first.get('currency') or 'CNY'),
            'unavailable': (is_available is False) or balance <= 0.0}


def decide(control, balance, state):
    """Pure decision step; mutates only state counters/marker fields (no writes)."""
    if not balance.get('ok'):
        state['last_error'] = balance.get('error')
        return 'skip-balance-error'
    state['last_error'] = None
    if balance['unavailable']:
        state['consecutive_unavailable'] = min(9, int(state.get('consecutive_unavailable') or 0) + 1)
        if not control['enabled']:
            return 'skip-already-stopped'
        if state['consecutive_unavailable'] < PAUSE_CONFIRMATIONS:
            return 'skip-wait-confirmation'
        return 'pause'
    state['consecutive_unavailable'] = 0
    if not state.get('auto_paused'):
        return 'none'
    if control['generation'] != state.get('paused_generation'):
        return 'clear-marker'
    if not control['enabled']:
        return 'resume'
    return 'clear-marker'


def apply_action(root, action, control, state, dry_run):
    """Return (current_control_or_None, error_or_None)."""
    if dry_run:
        return None, None
    try:
        if action == 'pause':
            current = set_enabled(False, control['generation'], root=root)
            state['auto_paused'] = True
            state['paused_generation'] = current['generation']
            state['paused_at'] = current['updated_at']
            return current, None
        if action == 'resume':
            current = set_enabled(True, control['generation'], root=root)
            state['auto_paused'] = False
            state['paused_generation'] = None
            state['resumed_at'] = current['updated_at']
            return current, None
        if action == 'clear-marker':
            state['auto_paused'] = False
            state['paused_generation'] = None
        return None, None
    except ValueError as error:
        if str(error) == 'ai-api-control-generation-conflict':
            return None, 'generation-conflict'
        return None, 'control-%s' % str(error)
    except AICallsDisabled as error:
        return None, 'control-%s' % str(error)
    except (OSError, TypeError) as error:
        return None, type(error).__name__


def main(argv=None):
    parser = argparse.ArgumentParser(description='DeepSeek balance guard for penta5 AI egress')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--balance-url', default=BALANCE_URL)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    root = Path(args.root)
    result = {'at': beijing_now().isoformat(), 'root': str(root),
              'dry_run': bool(args.dry_run), 'action': 'none',
              'balance': None, 'is_available': None, 'balance_error': None,
              'enabled_before': None, 'enabled_after': None,
              'generation_before': None, 'generation_after': None,
              'auto_paused': None, 'action_error': None}

    try:
        control = read_control(root=root)
    except AICallsDisabled as error:
        result['action'] = 'skip-control-error'
        result['action_error'] = str(error)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    result['enabled_before'] = control['enabled']
    result['generation_before'] = control['generation']

    key_name, secret = read_env_secret(root / '.env', BALANCE_ENV_KEYS)
    if secret is None:
        balance = {'ok': False, 'error': secret}
    else:
        balance = fetch_balance(args.balance_url, secret)
    result['balance'] = balance.get('total_balance')
    result['is_available'] = balance.get('is_available')
    result['balance_error'] = balance.get('error')

    state = load_state(state_path(root))
    action = decide(control, balance, state)
    result['action'] = action
    current, action_error = apply_action(root, action, control, state, args.dry_run)
    if action_error:
        result['action'] = 'skip-%s' % action_error
        result['action_error'] = action_error
        if action_error == 'generation-conflict':
            try:
                latest = read_control(root=root)
                retry = decide(latest, balance, state)
                if retry != action:
                    result['action'] = retry
                    current, action_error = apply_action(root, retry, latest, state, args.dry_run)
                    if action_error:
                        result['action'] = 'skip-%s' % action_error
                        result['action_error'] = action_error
            except AICallsDisabled as error:
                result['action'] = 'skip-control-error'
                result['action_error'] = str(error)
    if current:
        result['enabled_after'] = current['enabled']
        result['generation_after'] = current['generation']
    elif action in ('pause', 'resume', 'clear-marker'):
        result['enabled_after'] = control['enabled'] if args.dry_run else None
        result['generation_after'] = control['generation'] if args.dry_run else None

    state['last_checked_at'] = result['at']
    state['last_balance'] = balance.get('total_balance')
    state['last_is_available'] = balance.get('is_available')
    state['last_action'] = result['action'] if result['action'].startswith(('pause', 'resume', 'clear')) else 'none'
    if balance.get('ok'):
        state['last_error'] = None
    else:
        state['last_error'] = balance.get('error')
    result['auto_paused'] = bool(state.get('auto_paused'))
    if not args.dry_run:
        save_state(state_path(root), state)

    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
