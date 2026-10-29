"""Run the independently locked, no-model-API statistical comparison."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stat50_compare import main

if __name__ == '__main__':
    raise SystemExit(main())
