#!/usr/bin/env python3
"""项目根入口：转发到 algorithms/run.py。"""

from pathlib import Path
import runpy
import sys

TARGET = Path(__file__).resolve().parent / "algorithms" / "run.py"
sys.path.insert(0, str(TARGET.parent))
runpy.run_path(str(TARGET), run_name="__main__")
