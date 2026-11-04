#!/usr/bin/env python3
"""通用口径冻结守卫（多框架）：冻结窗口内受控源码/RULE_VERSION 变化即 WARN。

与 kill3 单框架老方案的关系
- 老文件 ops/kill3-freeze.json 保留为历史；首次 init 时把它迁移成 guards.kill3_tail。
- 守卫只做「变更检测 + 进度计时」，不阻止任何人改代码：改动必须显式留下
  hash_updates 记录（scripts/freeze_guard.py update --file ... --reason ...），
  这使「偷偷换口径」在 ops_health 里可见。
- 期数进度不整读封存账本（selected_six_seals.json 430M / he3 97M 会拖死
  ops_health），统一用 data/stats_draw_history.json（约 800 期、73K）计数。

CLI
  status                     打印每个框架的冻结状态
  init [--framework K] [--renew]
                             新建/刷新冻结基线（默认保留已有 frozen_at；--renew 重开窗口）
  update --framework K --file REL --reason TEXT
                             记录一次受控变更：刷新该文件 sha 并写 hash_updates
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path('/root/penta5-runner')
CONFIG = ROOT / 'ops' / 'freeze-guards.json'
LEGACY = ROOT / 'ops' / 'kill3-freeze.json'
DRAW_HISTORY = ROOT / 'data' / 'stats_draw_history.json'
DEFAULT_HOURS = 72
DEFAULT_PERIODS = 100

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Asia/Shanghai')
except Exception:  # pragma: no cover
    TZ = None

SHARED = ['lib/panel_context.py']

FRAMEWORKS = {
    'killer': {
        'label': '智能排除(killer)',
        'files': ['lib/auto_r1_killer.py', 'lib/auto_r1_match5.py',
                  'lib/killer_ai_client.py', 'lib/killer_tail_hold.py'],
        'shared': SHARED,
        'seal_path': 'data/r1_stats.json', 'seal_selector': 'prediction_seals',
        'rule_from_seal': True,
    },
    'framework': {
        'label': 'R1 框架',
        'files': ['lib/auto_r1_framework.py', 'lib/auto_r1_match5.py'],
        'shared': SHARED,
        'seal_path': 'data/r1_framework_seals.json', 'seal_selector': 'prediction_seals',
        'rule_from_seal': False,
    },
    'selected_six': {
        'label': '独立六项',
        'files': ['lib/selected_six_contract.py', 'lib/selected_six_model.py',
                  'lib/selected_six_request_options.py'],
        'shared': SHARED,
        'seal_path': 'data/selected_six_seal_index.json', 'seal_selector': 'prediction_seals',
        'rule_source': ('lib/selected_six_contract.py', 'REVISION'),
    },
    'he3_refine': {
        'label': 'he3 精修',
        'files': ['lib/he3_refine.py', 'lib/he3_refine_stats.py'],
        'shared': SHARED,
        'seal_path': 'data/he3_refine_seals.json', 'seal_selector': 'prediction_seals',
        'rule_source': ('lib/he3_refine.py', 'REVISION'),
    },
    'kill3_tail': {
        'label': '三位置杀码(kill3)',
        'files': ['lib/kill3_tail.py', 'lib/kill_three_view.py',
                  'scripts/run_kill3_tail.py', 'lib/panel_context.py'],
        'shared': [],
        'seal_path': 'data/kill3_tail/seals.json', 'seal_selector': 'records',
        'rule_source': ('lib/kill3_tail.py', 'RULE_VERSION'),
    },
    'kill1_tail': {
        'label': '单位置杀码(kill1)',
        'files': ['lib/kill1_tail.py', 'lib/kill_one_view.py'],
        'shared': SHARED,
        'seal_path': 'data/kill1_tail/seals.json', 'seal_selector': 'records',
        'rule_source': ('lib/kill1_tail.py', 'RULE_VERSION'),
    },
    'kill9_tail': {
        'label': '两位置杀码(kill9)',
        'files': ['lib/kill9_tail.py', 'lib/kill_nine_view.py'],
        'shared': SHARED,
        'seal_path': 'data/kill9_tail/seals.json', 'seal_selector': 'records',
        'rule_source': ('lib/kill9_tail.py', 'RULE_VERSION'),
    },
}


def _now():
    if TZ is not None:
        return datetime.now(TZ)
    return datetime.now().astimezone()


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(65536), b''):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _git_head(root):
    try:
        return subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--short', 'HEAD'],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ''


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False, encoding='utf-8')
    temporary = handle.name
    try:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=1)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _draw_periods(root):
    document = _read_json(root / DRAW_HISTORY, {}) or {}
    records = document.get('records') if isinstance(document, dict) else None
    if not isinstance(records, list):
        state = _read_json(root / 'data' / 'state.json', {}) or {}
        latest = state.get('latestRecord') if isinstance(state, dict) else None
        if isinstance(latest, dict) and latest.get('period'):
            return [str(latest['period'])]
        return []
    return [str(record.get('period')) for record in records
            if isinstance(record, dict) and record.get('period')]


def _periods_since(root, ref):
    """统计 ref 期之后已经开出的期数（用 800 期小索引，不碰几百 MB 封存账本）。"""
    if not ref:
        return 0
    periods = _draw_periods(root)
    if not periods:
        return 0
    count = sum(1 for period in periods if period > str(ref))
    if str(ref) < periods[0]:
        return len(periods)  # 早于索引覆盖范围，按下限报
    return count


def _latest_period(root):
    periods = _draw_periods(root)
    return max(periods) if periods else ''


def _rule_version(root, key):
    cfg = FRAMEWORKS[key]
    if cfg.get('rule_source'):
        path, name = cfg['rule_source']
        try:
            text = (root / path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None
        match = re.search(r"^[ \t]*%s\s*=\s*'([^']+)'" % re.escape(name), text, re.M)
        if match:
            return match.group(1)
        # 支持间接常量赋值：REVISION = V7_REVISION -> V7_REVISION = '...'
        alias = re.search(r"^[ \t]*%s\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\b" % re.escape(name), text, re.M)
        for _ in range(3):
            if not alias:
                return None
            target = alias.group(1)
            match = re.search(r"^[ \t]*%s\s*=\s*'([^']+)'" % re.escape(target), text, re.M)
            if match:
                return match.group(1)
            alias = re.search(r"^[ \t]*%s\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\b" % re.escape(target), text, re.M)
        return None
    if cfg.get('rule_from_seal'):
        document = _read_json(root / cfg['seal_path'], {}) or {}
        seals = document.get(cfg.get('seal_selector')) if isinstance(document, dict) else None
        if not isinstance(seals, dict) or not seals:
            # 大账本读不动时退回当前口径常量（killer/framework 共用 v2 基频）
            return 'marginal-frequency-v2'
        try:
            latest = seals[max(seals)]
        except (TypeError, ValueError):
            return 'marginal-frequency-v2'
        audit = latest.get('model_context_audit') if isinstance(latest, dict) else None
        body = audit.get('body') if isinstance(audit, dict) else None
        strategy = body.get('strategy') if isinstance(body, dict) else None
        if isinstance(strategy, dict) and strategy.get('strategy_id'):
            return str(strategy['strategy_id'])
        return 'marginal-frequency-v2'
    return 'marginal-frequency-v2'


def _load_config(root):
    document = _read_json(root / CONFIG, None)
    if not isinstance(document, dict) or not isinstance(document.get('guards'), dict):
        document = {'version': 1, 'guards': {}}
    return document


def _legacy_kill3(root):
    document = _read_json(root / LEGACY, None)
    return document if isinstance(document, dict) else None


def init(root, only=None, renew=False, hours=0, periods=0):
    root = Path(root)
    document = _load_config(root)
    guards = document['guards']
    legacy = _legacy_kill3(root)
    now = _now()
    ref_now = _latest_period(root)
    done = []
    for key, cfg in FRAMEWORKS.items():
        if only and key != only:
            continue
        previous = guards.get(key) if isinstance(guards.get(key), dict) else None
        source = previous or {}
        if not previous and key == 'kill3_tail' and legacy:
            source = {'files': legacy.get('files') or {},
                      'frozen_at': legacy.get('frozen_at'),
                      'freeze_ref_period': legacy.get('freeze_ref_period'),
                      'hash_updates': legacy.get('hash_updates') or [],
                      'freeze_hours': legacy.get('freeze_hours') or DEFAULT_HOURS,
                      'min_periods': legacy.get('min_periods') or DEFAULT_PERIODS,
                      'rule_version': legacy.get('rule_version'),
                      'frozen_commit': legacy.get('frozen_commit')}
        stamp = {
            'label': cfg['label'],
            'files': {rel: _sha256(root / rel) for rel in cfg['files']},
            'shared': {rel: _sha256(root / rel) for rel in (cfg.get('shared') or [])},
            'rule_version': _rule_version(root, key) or source.get('rule_version'),
            'freeze_hours': float(hours or source.get('freeze_hours') or DEFAULT_HOURS),
            'min_periods': int(periods or source.get('min_periods') or DEFAULT_PERIODS),
            'freeze_ref_period': ('' if renew else str(source.get('freeze_ref_period') or '')) or ref_now,
            'frozen_at': now.isoformat() if (renew or not source.get('frozen_at')) else source['frozen_at'],
            'frozen_commit': _git_head(root),
            'hash_updates': list(source.get('hash_updates') or []),
        }
        guards[key] = stamp
        done.append(key)
    document['updated_at'] = now.isoformat()
    document['version'] = 1
    _atomic_json(root / CONFIG, document)
    print('freeze_guard init: %s (%d guards) latest_draw=%s' % (','.join(done), len(guards), ref_now))
    for key in done:
        print('  %-14s files=%d ref=%s' % (key, len(guards[key]['files']),
                                           guards[key]['freeze_ref_period']))


def evaluate(root):
    """供 ops_health 使用：返回每个框架的冻结评估结果（不写盘）。"""
    root = Path(root)
    document = _load_config(root)
    guards = document.get('guards') or {}
    now = _now()
    rows = []
    for key, cfg in FRAMEWORKS.items():
        stamp = guards.get(key) if isinstance(guards.get(key), dict) else None
        if not stamp:
            rows.append({'key': key, 'label': cfg['label'], 'missing': True, 'files_changed': [],
                         'shared_changed': [], 'rule_version': None, 'rule_changed': False,
                         'periods': 0, 'min_periods': DEFAULT_PERIODS, 'hours': None,
                         'freeze_hours': DEFAULT_HOURS, 'expired': False})
            continue
        files_changed = []
        for rel, sha in sorted((stamp.get('files') or {}).items()):
            current = _sha256(root / rel)
            if sha and current and current != sha:
                files_changed.append(rel)
        shared_changed = [rel for rel, sha in sorted((stamp.get('shared') or {}).items())
                          if sha and _sha256(root / rel) not in (None, sha)]
        version = _rule_version(root, key)
        frozen_version = stamp.get('rule_version')
        rule_changed = bool(version and frozen_version and version != frozen_version)
        hours = None
        try:
            frozen_at = datetime.fromisoformat(str(stamp.get('frozen_at')))
            hours = (now - frozen_at).total_seconds() / 3600.0
        except (TypeError, ValueError):
            hours = None
        periods = _periods_since(root, stamp.get('freeze_ref_period'))
        min_periods = int(stamp.get('min_periods') or DEFAULT_PERIODS)
        freeze_hours = float(stamp.get('freeze_hours') or DEFAULT_HOURS)
        expired = (hours is not None and hours >= freeze_hours) or periods >= min_periods
        rows.append({'key': key, 'label': cfg['label'], 'missing': False,
                     'files_changed': files_changed, 'shared_changed': shared_changed,
                     'rule_version': version, 'frozen_version': frozen_version,
                     'rule_changed': rule_changed, 'periods': periods,
                     'min_periods': min_periods, 'hours': hours,
                     'freeze_hours': freeze_hours, 'expired': expired,
                     'frozen_at': stamp.get('frozen_at'),
                     'freeze_ref_period': stamp.get('freeze_ref_period')})
    return rows


def status(root):
    now = _now()
    for row in evaluate(root):
        if row.get('missing'):
            print('%-14s (no stamp)' % row['key'])
            continue
        print('%-14s ref=%-13s periods=%d/%d hours=%s/%.0f changed=%s rule=%s%s' % (
            row['key'], row.get('freeze_ref_period') or '-', row['periods'], row['min_periods'],
            ('%.1f' % row['hours']) if row['hours'] is not None else '-', row['freeze_hours'],
            row['files_changed'] or '-', row['rule_version'],
            ' (CHANGED)' if row['rule_changed'] else ''))


def update(root, key, rel, reason):
    root = Path(root)
    document = _load_config(root)
    stamp = document.get('guards', {}).get(key)
    if not isinstance(stamp, dict):
        raise SystemExit('unknown framework: %s' % key)
    current = _sha256(root / rel)
    if not current:
        raise SystemExit('cannot read file: %s' % rel)
    old = (stamp.get('files') or {}).get(rel)
    stamp.setdefault('files', {})[rel] = current
    stamp.setdefault('hash_updates', []).append({
        'at': _now().isoformat(), 'commit': _git_head(root), 'file': rel,
        'old_sha256': old, 'new_sha256': current, 'reason': reason})
    document['updated_at'] = _now().isoformat()
    _atomic_json(root / CONFIG, document)
    print('freeze_guard update: %s %s %s -> %s' % (key, rel, (old or '-')[:12], current[:12]))


def main(argv=None):
    parser = argparse.ArgumentParser(description='generic rule-freeze guard for penta5 frameworks')
    parser.add_argument('--root', default=str(ROOT))
    sub = parser.add_subparsers(dest='command', required=True)
    p_init = sub.add_parser('init')
    p_init.add_argument('--framework')
    p_init.add_argument('--renew', action='store_true')
    p_init.add_argument('--hours', type=float, default=0)
    p_init.add_argument('--periods', type=int, default=0)
    sub.add_parser('status')
    p_update = sub.add_parser('update')
    p_update.add_argument('--framework', required=True)
    p_update.add_argument('--file', required=True)
    p_update.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.command == 'init':
        init(root, only=args.framework, renew=args.renew, hours=args.hours, periods=args.periods)
    elif args.command == 'status':
        status(root)
    else:
        update(root, args.framework, args.file, args.reason)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
