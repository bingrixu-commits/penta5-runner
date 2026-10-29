"""One non-overlapping operational snapshot; no paid calls or service actions."""
from pathlib import Path
import fcntl
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))


def main():
    lock_path = ROOT / 'data/runtime-observability.lock'
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"already-running"}', flush=True)
            return
        from runtime_operations import run
        result = run(root=ROOT)
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
