"""合尾杀码 hold_max 多档候选影子：每期重建回放/前瞻对照并刷新面板区块（只读衍生）。

cron 每 5 分钟；只写 data/tail_hold_max_shadow.json 与 panel.html 的
TAIL_HOLD_MAX_SHADOW 区块，不触碰封存/正式统计/攻击语。
"""
from pathlib import Path
import json
import sys

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from tail_hold_max_shadow import refresh


def main():
    try:
        data = refresh(root=ROOT)
    except Exception as error:
        print('TAIL_HOLD_MAX_SHADOW_FAILURE error_type=' + type(error).__name__, flush=True)
        return 1
    frameworks = data.get('frameworks') or {}
    summary = {}
    for name, item in frameworks.items():
        back = item.get('backfill') or {}
        actual = back.get('actual') or {}
        policies = back.get('policies') or {}
        forward = item.get('forward') or {}
        match = back.get('hold3_sequence_match') or {}
        summary[name] = {
            'periods': back.get('periods'),
            'item_n': actual.get('n'),
            'actual_rate': actual.get('rate'),
            'actual_changes': actual.get('changes'),
            'hold3_rate': (policies.get('hold3') or {}).get('rate'),
            'hold3_changes': (policies.get('hold3') or {}).get('changes'),
            'hold3_reuse_periods': (policies.get('hold3') or {}).get('reuse_periods'),
            'hold3_sequence_match': [match.get('matched'), match.get('periods')],
            'forward_periods': forward.get('periods'),
        }
    print(json.dumps({'status': 'ok', 'generated_at': data.get('generated_at'),
                      'start_period': data.get('start_period'), 'frameworks': summary},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':')), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
