"""位置杀码稳定化候选影子：每期重建回放/前瞻对照并刷新面板区块（只读衍生）。

cron 每 5 分钟；只写 data/position_kill_shadow.json 与 panel.html 的
POSITION_KILL_SHADOW 区块，不触碰封存/正式统计/攻击语。
"""
from pathlib import Path
import json
import sys

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from position_kill_shadow import refresh


def main():
    try:
        data = refresh(root=ROOT)
    except Exception as error:
        print('POSITION_KILL_SHADOW_FAILURE error_type=' + type(error).__name__, flush=True)
        return 1
    frameworks = data.get('frameworks') or {}
    summary = {}
    for name, item in frameworks.items():
        back = item.get('backfill') or {}
        actual = back.get('actual') or {}
        policies = back.get('policies') or {}
        forward = item.get('forward') or {}
        summary[name] = {
            'backfill_n': actual.get('n'),
            'backfill_actual_rate': actual.get('rate'),
            'backfill_changes': actual.get('changes'),
            'policies': {key: {'rate': p.get('rate'), 'changes': p.get('changes')}
                         for key, p in policies.items()},
            'forward_periods': forward.get('periods'),
        }
    print(json.dumps({'status': 'ok', 'generated_at': data.get('generated_at'),
                      'start_period': data.get('start_period'), 'frameworks': summary},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':')), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
