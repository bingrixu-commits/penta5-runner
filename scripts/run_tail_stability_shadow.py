"""合尾稳定化候选影子：每期重建回放/前瞻对照并刷新面板区块（只读衍生）。

cron 每 5 分钟；只写 data/tail_stability_shadow.json 与 panel.html 的
TAIL_STABILITY_SHADOW 区块，不触碰封存/正式统计/攻击语。
"""
from pathlib import Path
import json
import sys

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from tail_stability_shadow import refresh


def main():
    try:
        data = refresh(root=ROOT)
    except Exception as error:
        print('TAIL_STABILITY_SHADOW_FAILURE error_type=' + type(error).__name__, flush=True)
        return 1
    frameworks = data.get('frameworks') or {}
    summary = {}
    for name, item in frameworks.items():
        backfill = (item.get('backfill') or {})
        actual = backfill.get('actual') or {}
        policy = backfill.get('policy') or {}
        forward = item.get('forward') or {}
        summary[name] = {
            'backfill_n': actual.get('n'),
            'backfill_actual_rate': actual.get('rate'),
            'backfill_policy_rate': policy.get('rate'),
            'forward_periods': forward.get('periods'),
        }
    print(json.dumps({'status': 'ok', 'generated_at': data.get('generated_at'),
                      'start_period': data.get('start_period'), 'frameworks': summary},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':')), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
