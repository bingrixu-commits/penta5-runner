"""收集模型运行证据、生成派生汇总并发布面板区块（不产生任何付费调用）。"""
from pathlib import Path
import fcntl
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    publish = '--no-panel' not in arguments
    lock_path = ROOT / 'data' / 'model-usage.lock'
    try:
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({'status': 'already-running'}, ensure_ascii=True), flush=True)
                return 0
            try:
                from model_usage import run
                result = run(root=ROOT, publish_panel=publish)
            except Exception as error:
                print(json.dumps({'status': 'error', 'error': type(error).__name__,
                                  'detail': str(error)[:200]}, ensure_ascii=True), flush=True)
                return 1
            print(json.dumps(result, ensure_ascii=True, allow_nan=False,
                             separators=(',', ':')), flush=True)
            return 0
    except OSError as error:
        print(json.dumps({'status': 'error', 'error': type(error).__name__},
                         ensure_ascii=True), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
