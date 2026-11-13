#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 入口：三数共现避开 1/2/3/4 组 · 业务日核验统计。

用法：
  python3 scripts/three_avoid2_daily.py            # 实算并写 data/three_avoid2_daily.json
  python3 scripts/three_avoid2_daily.py --now '2026-11-01 04:52:00' --dry   # 回放核验（不落盘）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import three_avoid2_stats as stats  # noqa: E402


if __name__ == '__main__':
    sys.exit(stats.main())
