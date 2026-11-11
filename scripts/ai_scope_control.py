#!/usr/bin/env python3
"""按框架查看/控制 AI 调用开关。

用法：
  python3 scripts/ai_scope_control.py status               # 全部作用域状态
  python3 scripts/ai_scope_control.py status --scope kill15
  python3 scripts/ai_scope_control.py stop  --scope kill15
  python3 scripts/ai_scope_control.py start --scope kill15
  python3 scripts/ai_scope_control.py stop  --scope kill15 --note "手工暂停测试"
  python3 scripts/ai_scope_control.py stop-all / start-all
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))

import ai_scope  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description='per-framework AI scope control')
    parser.add_argument('action', choices=('status', 'start', 'stop', 'start-all', 'stop-all'))
    parser.add_argument('--scope', default=None)
    parser.add_argument('--note', default=None)
    args = parser.parse_args(argv)

    if args.action == 'status':
        rows = ai_scope.describe()
        if args.scope:
            rows = [row for row in rows if row['scope'] == args.scope]
        print(json.dumps({'global': _global_state(), 'scopes': rows},
                         ensure_ascii=False, indent=2))
        return 0

    if args.action in ('start-all', 'stop-all'):
        enabled = args.action == 'start-all'
        changed = []
        for scope in ai_scope.SCOPES:
            entry = ai_scope.set_scope_enabled(scope, enabled, note=args.note)
            changed.append({'scope': scope, 'enabled': entry['enabled'],
                            'generation': entry['generation']})
        print(json.dumps({'action': args.action, 'changed': changed}, ensure_ascii=False))
        return 0

    if not args.scope:
        parser.error('--scope 必填（或用 start-all/stop-all）')
    entry = ai_scope.set_scope_enabled(args.scope, args.action == 'start', note=args.note)
    print(json.dumps({'scope': args.scope, 'entry': entry}, ensure_ascii=False, indent=2))
    return 0


def _global_state():
    try:
        from ai_call_control import read_control
        return read_control()
    except Exception as error:
        return {'error': type(error).__name__}


if __name__ == '__main__':
    raise SystemExit(main())
