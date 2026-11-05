#!/usr/bin/env python3
"""kill3 v16 预登记回滚脚本（默认 dry-run；--execute 才真正执行）。

背景：ops/kill3-stability-plan.md §9 的预登记门禁（lib/kill3_v16_guard.py 判定）
- 触发条件：v16 已结算样本 n >= 150，且 z < -2.5 或整组命中率 < 50%；
- 触发后运行本脚本 --execute，把 kill3 回滚到 v15 语义（保留 2026-11-06 01:41 之后的
  max_tokens 16384 / finish_reason 日志等非 v16 改动），并重开冻结窗口。

执行步骤（每步失败即从备份恢复并退出非 0）：
1. 读取门禁：status != triggered 时拒绝（--force 跳过，用于人工确认后的紧急回滚）；
2. 备份 lib/kill3_tail.py、lib/kill_three_view.py、scripts/run_kill3_tail.py、
   ops/freeze-guards.json、ops/kill3-freeze.json 到 /root/ops/kill3-v16-rollback-<ts>/；
3. git show 417c472 -- 三文件生成补丁，git apply -R --check 预检后反向应用；
4. py_compile + 断言 RULE_VERSION 回 v15、v16 冷热覆盖代码/文案已移除；
5. python3 scripts/freeze_guard.py init --framework kill3_tail --renew 重开冻结窗口，
   并把 ops/freeze-guards.json 的 kill3_tail 镜像回 legacy ops/kill3-freeze.json；
6. 写 data/kill3_v16_guard.json（inactive）；
7. git add 仅本次相关文件后 commit（不 push）。

用法：
  python3 scripts/kill3_v16_rollback.py            # dry-run：打印计划并做反向补丁预检
  python3 scripts/kill3_v16_rollback.py --execute  # 真正回滚并 commit
  python3 scripts/kill3_v16_rollback.py --execute --force   # 门禁未触发时强制回滚
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get('PENTA5_ROOT') or '/root/penta5-runner')
V16_COMMIT = '417c472'
V16_RULE = ('kill3-tail-v16-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid'
            '-cold-drop')
V15_RULE = ('kill3-tail-v15-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid')
FILES = ['lib/kill3_tail.py', 'lib/kill_three_view.py', 'scripts/run_kill3_tail.py']
FREEZE_FILES = ['ops/freeze-guards.json', 'ops/kill3-freeze.json']
COMMIT_MSG = ('fix(kill3): 回滚 v16 弃位取冷（预登记门禁触发）\n\n'
              '依据 ops/kill3-stability-plan.md §9：v16 已结算样本达 n=150 后 z<-2.5 或 '
              '整组命中率<50% 即执行回滚。反向应用 %s 的 v16 语义补丁（RULE_VERSION 回 v15、'
              'normalize_response 取消弃位冷热覆盖、prompt/面板文案回退），保留 91f10f4 的 '
              'max_tokens 16384 与失败日志改进；freeze Guard --renew 重开冻结窗口。\n'
              '回滚命令: scripts/kill3_v16_rollback.py --execute') % V16_COMMIT


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args, check=False):
    proc = subprocess.run(args, cwd=str(ROOT), text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError('%s 失败 (rc=%d): %s' % (' '.join(args), proc.returncode,
                                                    (proc.stderr or proc.stdout).strip()[:500]))
    return proc


def _git(*args, check=False):
    return _run(['git', *args], check=check)


def _rule_version():
    try:
        text = (ROOT / 'lib' / 'kill3_tail.py').read_text(encoding='utf-8')
    except OSError:
        return None
    match = re.search(r"^RULE_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    return match.group(1) if match else None


def _gate_report():
    sys.path.insert(0, str(ROOT / 'lib'))
    sys.path.insert(0, str(ROOT / 'scripts'))
    import kill3_v16_guard
    return kill3_v16_guard.evaluate(ROOT)


def _print_plan(report, backup_dir):
    print('== kill3 v16 回滚（%s）==' % ('dry-run' if backup_dir is None else 'execute'))
    print('现行口径: %s' % (_rule_version() or '-'))
    print('门禁: status=%s n=%s rate=%s z=%s p_two=%s' % (
        report.get('status'), report.get('n'), report.get('rate'),
        report.get('z'), report.get('p_two')))
    print('判定: %s' % report.get('verdict'))
    print('反向补丁: git show %s -- %s | git apply -R' % (V16_COMMIT, ' '.join(FILES)))
    print('冻结重开: python3 scripts/freeze_guard.py init --framework kill3_tail --renew')


def _backup():
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = Path('/root/ops') / ('kill3-v16-rollback-%s' % stamp)
    backup_dir.mkdir(parents=True, exist_ok=False)
    meta = {'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'v16_commit': V16_COMMIT, 'files': {}}
    for rel in FILES + FREEZE_FILES:
        source = ROOT / rel
        if not source.exists():
            continue
        target = backup_dir / rel.replace('/', '__')
        shutil.copy2(source, target)
        meta['files'][rel] = {'sha256': _sha256(source), 'backup': str(target)}
    (backup_dir / 'meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('备份目录: %s' % backup_dir)
    return backup_dir, meta


def _restore(backup_dir, meta):
    print('恢复备份: %s' % backup_dir)
    for rel, info in (meta.get('files') or {}).items():
        source = Path(info.get('backup') or '')
        if source.exists():
            shutil.copy2(source, ROOT / rel)
    print('已从备份恢复受控文件（git 工作区状态请人工复核）')


def _make_reverse_patch(backup_dir):
    proc = _git('show', V16_COMMIT, '--', *FILES)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError('git show %s 失败: %s' % (V16_COMMIT, (proc.stderr or '').strip()[:300]))
    patch_path = Path(backup_dir) / ('%s-v16.patch' % V16_COMMIT)
    patch_path.write_text(proc.stdout, encoding='utf-8')
    return patch_path


def _apply_reverse_patch(patch_path):
    check = _git('apply', '-R', '--check', str(patch_path))
    if check.returncode != 0:
        raise RuntimeError('反向补丁预检失败（kill3 文件已被并行改动？）: %s'
                           % (check.stderr or check.stdout).strip()[:500])
    _git('apply', '-R', str(patch_path), check=True)
    print('已反向应用 %s 的 v16 语义补丁' % V16_COMMIT)


def _verify_files():
    rule = _rule_version()
    if rule != V15_RULE:
        raise RuntimeError('RULE_VERSION 回滚后为 %r，期望 %r' % (rule, V15_RULE))
    tail_text = (ROOT / 'lib' / 'kill3_tail.py').read_text(encoding='utf-8')
    for token in ('cold_dropped_form', 'dropped_form_cold'):
        if token in tail_text:
            raise RuntimeError('lib/kill3_tail.py 仍残留 v16 代码: %s' % token)
    view_text = (ROOT / 'lib' / 'kill_three_view.py').read_text(encoding='utf-8')
    if 'dropped_form_cold' in view_text:
        raise RuntimeError('lib/kill_three_view.py 仍残留 v16 展示分支')
    for rel in FILES:
        py_compile.compile(str(ROOT / rel), doraise=True)
    print('校验通过: RULE_VERSION=%s，py_compile %d 文件' % (rule, len(FILES)))


def _restamp_freeze():
    proc = _run([sys.executable, 'scripts/freeze_guard.py', 'init',
                 '--framework', 'kill3_tail', '--renew'])
    if proc.returncode != 0:
        raise RuntimeError('freeze_guard init 失败: %s'
                           % (proc.stderr or proc.stdout).strip()[:500])
    print(proc.stdout.strip())
    document = json.loads((ROOT / 'ops' / 'freeze-guards.json').read_text(encoding='utf-8'))
    stamp = ((document.get('guards') or {}).get('kill3_tail') or {})
    legacy_path = ROOT / 'ops' / 'kill3-freeze.json'
    legacy = {}
    try:
        legacy = json.loads(legacy_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        legacy = {}
    payload = {
        'version': 1,
        'rule_version': stamp.get('rule_version'),
        'files': stamp.get('files') or {},
        'frozen_at': stamp.get('frozen_at'),
        'frozen_commit': stamp.get('frozen_commit'),
        'freeze_ref_period': stamp.get('freeze_ref_period'),
        'freeze_hours': stamp.get('freeze_hours'),
        'min_periods': stamp.get('min_periods'),
        'hash_updates': list(stamp.get('hash_updates') or []),
    }
    if legacy.get('policy'):
        payload['policy'] = legacy['policy']
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=str(legacy_path.parent),
                                         delete=False, suffix='.tmp')
    try:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, str(legacy_path))
    print('legacy ops/kill3-freeze.json 已同步（rule=%s ref=%s）'
          % (payload.get('rule_version'), payload.get('freeze_ref_period')))


def _write_guard_inactive():
    sys.path.insert(0, str(ROOT / 'lib'))
    import kill3_v16_guard
    payload = kill3_v16_guard.write_report(ROOT)
    print('门禁报告已写: status=%s %s' % (payload.get('status'), payload.get('verdict')))


def _commit():
    staged = _git('diff', '--cached', '--name-only')
    if staged.stdout.strip():
        raise RuntimeError('暂存区已有其他改动（并行窗口/上次未提交），拒绝提交: %s'
                           % staged.stdout.strip().replace('\n', ', ')[:400])
    paths = FILES + FREEZE_FILES + [
        'lib/kill3_v16_guard.py', 'scripts/kill3_v16_rollback.py',
        'ops/kill3-stability-plan.md', 'ops/README.md',
    ]
    _git('add', '--', *paths, check=True)
    staged = _git('diff', '--cached', '--name-only')
    if not staged.stdout.strip():
        print('没有需要提交的变更')
        return
    proc = _git('-c', 'user.name=penta5', '-c', 'user.email=penta5@local',
                'commit', '-m', COMMIT_MSG, check=True)
    print((proc.stdout or '').strip()[:800])
    print('HEAD=%s' % _git('rev-parse', '--short', 'HEAD').stdout.strip())


def main():
    parser = argparse.ArgumentParser(description='kill3 v16 预登记回滚（默认 dry-run）')
    parser.add_argument('--execute', action='store_true', help='真正执行回滚并 commit')
    parser.add_argument('--force', action='store_true', help='门禁未触发时也强制回滚（人工确认后）')
    args = parser.parse_args()
    report = _gate_report()
    if not args.execute:
        _print_plan(report, None)
        patch_probe = Path(tempfile.mkdtemp(prefix='kill3-v16-dryrun-'))
        try:
            patch_path = _make_reverse_patch(patch_probe)
            check = _git('apply', '-R', '--check', str(patch_path))
            print('反向补丁预检: %s' % ('OK' if check.returncode == 0 else 'FAIL'))
            if check.returncode != 0:
                print((check.stderr or '').strip()[:400])
        finally:
            shutil.rmtree(patch_probe, ignore_errors=True)
        return 0
    if report.get('status') != 'triggered' and not args.force:
        print('拒绝执行: 门禁 status=%s，未触发回滚条件。确需人工回滚请加 --force。'
              % report.get('status'))
        return 2
    backup_dir = meta = None
    try:
        backup_dir, meta = _backup()
        patch_path = _make_reverse_patch(backup_dir)
        _apply_reverse_patch(patch_path)
        _verify_files()
        _restamp_freeze()
        _write_guard_inactive()
        _commit()
    except Exception as error:
        print('回滚失败: %s: %s' % (type(error).__name__, error))
        if backup_dir is not None and meta is not None:
            _restore(backup_dir, meta)
        return 1
    print('回滚完成。下一步: python3 scripts/ops_health_check.py 复查（kill3_v16_gate 应为 inactive），'
          '并确认面板 kill3 卡出现「v16 已回滚」提示。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
