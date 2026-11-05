#!/usr/bin/env python3
"""pro 影子请求的薄包装：由 lib/kill1_ab.spawn_pro_shadow 以独立进程调用。

只负责解析 sys.path 并把参数转交给 lib/kill1_ab.main；真正请求与写入台账在 lib 内。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT), str(ROOT / 'lib')):
    if path not in sys.path:
        sys.path.insert(0, path)

from kill1_ab import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
