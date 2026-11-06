#!/usr/bin/env python3
"""kill3 v17 预登记门禁的自动执行器（把「门禁触发 -> 回滚」接到 cron）。

背景与判据（已预登记，见 ops/kill3-stability-plan.md §9）：
- lib/kill3_v17_guard.py 计算门禁：现行口径为 v17 且已结算 n>=150 时，
  z < -2.5 或整组命中率 < 50% 即 triggered；
- scripts/kill3_v17_rollback.py 执行回滚（默认 dry-run，--execute 前会再校验门禁）；
- 本脚本只做调度：每 5 分钟读一次 data/kill3_v17_guard.json，仅在
  status=triggered 且现行口径仍是 v17 时执行一次 --execute；回滚后幂等退出。

安全边界：
- 门禁未触发 -> 绝不动作（回滚脚本自身也会拒绝执行，双保险）；
- 最多尝试 MAX_ATTEMPTS 次，失败按 MIN_INTERVAL_SECONDS 退避；用尽后 gave_up，
  不再自动重试，等人工处理（避免在坏工作树上反复尝试）；
- 单实例：cron 侧 flock + 脚本内数据目录 flock；状态文件原子写入；
- 不改预测口径、不 push、不触碰 data 账本；全部输出由 cron 重定向到
  data/kill3_v17_auto_rollback.log。

用法：
  python3 scripts/kill3_v17_auto_rollback.py            # cron 调用（静默，仅在动作时输出）
  python3 scripts/kill3_v17_auto_rollback.py --status    # 人工查看门禁/配置/状态
  python3 scripts/kill3_v17_auto_rollback.py --root /tmp/x  # 测试用根目录
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get('PENTA5_ROOT') or '/root/penta5-runner')
V17_RULE = 'kill3-tail-v17-flexible-two-four-position-one-tail-two-form-three-avoid'
V16_RULE = 'kill3-tail-v16-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid-cold-drop'
GUARD_REL = Path('data') / 'kill3_v17_guard.json'
CONFIG_REL = Path('data') / 'kill3_v17_auto_rollback.json'
STATE_REL = Path('data') / 'kill3_v17_auto_rollback_state.json'
LOCK_REL = Path('data') / 'kill3_v17_auto_rollback.lock'
ROLLBACK_REL = Path('scripts') / 'kill3_v17_rollback.py'
RULE_RE = re.compile(r"^RULE_VERSION\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
DEFAULT_CONFIG = {
    'version': 1,
    'enabled': True,
    'min_interval_seconds': 600,
    'max_attempts': 5,
    'timeout_seconds': 300,
    'note': 'kill3 v17 预登记门禁的自动执行器配置；enabled=false 可随时解除自动回滚',
}
DEFAULT_STATE = {
    'version': 1,
    'status': 'idle',
    'attempts': 0,
    'last_checked_at': None,
    'last_attempt_at': None,
    'last_result': None,
    'finished_at': None,
}


def _iso(moment=None):
    return (moment or datetime.now().astimezone()).isoformat(timespec='seconds')


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=str(path.parent),
                                         delete=False, suffix='.tmp')
    try:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, str(path))


def load_config(root):
    config = dict(DEFAULT_CONFIG)
    document = _read_json(Path(root) / CONFIG_REL)
    if isinstance(document, dict):
        for key in ('enabled', 'min_interval_seconds', 'max_attempts', 'timeout_seconds'):
            if key in document:
                config[key] = document[key]
    return config


def load_state(root):
    state = dict(DEFAULT_STATE)
    document = _read_json(Path(root) / STATE_REL)
    if isinstance(document, dict):
        state.update(document)
    return state


def save_state(root, state):
    _write_json_atomic(Path(root) / STATE_REL, state)


def current_rule_version(root):
    try:
        text = (Path(root) / 'lib' / 'kill3_tail.py').read_text(encoding='utf-8')
    except OSError:
        return None
    match = RULE_RE.search(text)
    return match.group(1) if match else None


def _parse_time(text):
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _log(message):
    print('%s %s' % (_iso(), message), flush=True)


def _describe_guard(guard):
    return 'status=%s n=%s hits=%s rate=%s z=%s' % (
        guard.get('status'), guard.get('n'), guard.get('hits'),
        guard.get('rate'), guard.get('z'))


def execute_rollback(root, config):
    rollback = Path(root) / ROLLBACK_REL
    if not rollback.exists():
        raise RuntimeError('回滚脚本不存在: %s' % rollback)
    environment = dict(os.environ)
    environment['PENTA5_ROOT'] = str(root)
    process = subprocess.run(
        [sys.executable, str(rollback), '--execute'],
        cwd=str(root), env=environment, text=True, capture_output=True,
        timeout=int(config['timeout_seconds']))
    return process


def run(root, now=None):
    root = Path(root)
    now = now or datetime.now().astimezone()
    config = load_config(root)
    state = load_state(root)
    state['last_checked_at'] = _iso(now)
    guard = _read_json(root / GUARD_REL)
    rule = current_rule_version(root)

    if not config.get('enabled'):
        save_state(root, state)
        return 0
    if not isinstance(guard, dict):
        save_state(root, state)
        return 0

    if guard.get('status') != 'triggered':
        save_state(root, state)
        return 0

    if rule != V17_RULE:
        if state.get('status') != 'done':
            state['status'] = 'done'
            state['last_result'] = 'already-not-v17'
            state['finished_at'] = _iso(now)
            _log('SKIP 现行口径 %s 已不是 v17（已回滚或再升版），自动回滚器幂等退出' % rule)
        save_state(root, state)
        return 0

    attempts = int(state.get('attempts') or 0)
    if state.get('status') == 'done':
        save_state(root, state)
        return 0
    if state.get('status') == 'gave_up' and attempts >= int(config['max_attempts']):
        save_state(root, state)
        return 0
    last_attempt = _parse_time(state.get('last_attempt_at'))
    interval = timedelta(seconds=int(config['min_interval_seconds']))
    if last_attempt is not None and now - last_attempt < interval:
        save_state(root, state)
        return 0

    lock_path = root / LOCK_REL
    lock_handle = open(lock_path, 'a')
    try:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            save_state(root, state)
            return 0
        attempts += 1
        state['status'] = 'retrying'
        state['attempts'] = attempts
        state['last_attempt_at'] = _iso(now)
        state['last_result'] = 'running'
        save_state(root, state)
        _log('TRIGGERED 门禁已触发（%s n=%s z=%s rate=%s），执行回滚 attempt %d/%d'
             % (guard.get('rule_version'), guard.get('n'), guard.get('z'),
                guard.get('rate'), attempts, int(config['max_attempts'])))
        try:
            process = execute_rollback(root, config)
        except subprocess.TimeoutExpired:
            state['status'] = 'retrying'
            state['last_result'] = 'timeout'
            save_state(root, state)
            _log('ERROR 回滚脚本超时（%ss），下个周期重试' % config['timeout_seconds'])
            return 1
        except Exception as error:
            state['status'] = 'retrying'
            state['last_result'] = '%s: %s' % (type(error).__name__, error)
            save_state(root, state)
            _log('ERROR 回滚脚本无法启动: %s: %s' % (type(error).__name__, error))
            return 1
        new_rule = current_rule_version(root)
        if process.returncode == 0 and new_rule == V16_RULE:
            state['status'] = 'done'
            state['last_result'] = 'success'
            state['finished_at'] = _iso()
            save_state(root, state)
            _log('SUCCESS v17 预登记回滚已自动执行：RULE_VERSION=%s；'
                 '门禁状态将由回滚脚本写回 inactive' % new_rule)
            for line in (process.stdout or '').strip().splitlines()[-8:]:
                _log('  rollback: %s' % line)
            return 0
        state['last_result'] = 'failed rc=%s rule=%s' % (process.returncode, new_rule)
        if attempts >= int(config['max_attempts']):
            state['status'] = 'gave_up'
            _log('CRITICAL 自动回滚连续失败 %d 次，已停止自动重试，需要人工处理'
                 % attempts)
        else:
            state['status'] = 'retrying'
            _log('ERROR 自动回滚失败（rc=%s rule=%s），%d/%d 次，下个周期重试'
                 % (process.returncode, new_rule, attempts, int(config['max_attempts'])))
        tail = ((process.stderr or '') + (process.stdout or '')).strip()
        for line in tail.splitlines()[-8:]:
            _log('  rollback: %s' % line)
        save_state(root, state)
        return 1
    finally:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def show_status(root):
    config = load_config(root)
    state = load_state(root)
    guard = _read_json(Path(root) / GUARD_REL) or {}
    print('root: %s' % root)
    print('rule_version: %s' % (current_rule_version(root) or '-'))
    print('config: enabled=%s min_interval=%ss max_attempts=%s'
          % (config.get('enabled'), config.get('min_interval_seconds'),
             config.get('max_attempts')))
    print('guard: %s' % _describe_guard(guard))
    print('state: %s' % json.dumps(state, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='kill3 v17 预登记门禁自动执行器')
    parser.add_argument('--root', default=None, help='项目根目录（默认 PENTA5_ROOT 或 /root/penta5-runner）')
    parser.add_argument('--status', action='store_true', help='打印门禁/配置/状态后退出')
    parser.add_argument('--now', default=None, help='测试用：覆盖当前时间（ISO8601）')
    args = parser.parse_args(argv)
    root = Path(args.root or DEFAULT_ROOT)
    if args.status:
        return show_status(root)
    now = _parse_time(args.now) if args.now else None
    return run(root, now=now)


if __name__ == '__main__':
    raise SystemExit(main())
