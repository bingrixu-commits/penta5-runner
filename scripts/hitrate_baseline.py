#!/usr/bin/env python3
"""CLI 入口：生成 data/hitrate_baseline.json（面板「整组命中率 vs 随机期望」数据源）。

逻辑在 lib/hitrate_baseline.py；面板由 /root/run_stats.py 每轮刷新时调用同一 main()。
手动执行：python3 scripts/hitrate_baseline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

import hitrate_baseline  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(hitrate_baseline.main())
