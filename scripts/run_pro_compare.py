"""Run independently locked Pro four-position exclusions on the server."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pro_position_model import main

if __name__ == '__main__':
    raise SystemExit(main())
