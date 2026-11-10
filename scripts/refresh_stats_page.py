"""手动刷新独立统计与候选对照页 /stats（生产环境由 run_stats 与影子任务自动重建）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'lib') not in sys.path:
    sys.path.append(str(ROOT / 'lib'))

import stats_page_view


if __name__ == '__main__':
    print('stats page written: %s' % stats_page_view.write_page(compute_model_call=True))
