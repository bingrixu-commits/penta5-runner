#!/usr/bin/env python3
"""把 kill3 纸面跟投账本块渲染进 panel.html（只读影子，不改封存/结算）。

做法与 scripts/update_joint_form_shadow_panel.py 一致：读 data/kill3_shadow_ledger.json，
生成 START/END 区块，在 panel.html 里按 <!-- KILL3_SHADOW_START/END --> 原位替换；
缺失时插到 <!-- KILL_THREE_END -->（kill3 卡下方）之后，再退化到最后一个 </body> 前。
原子替换 + 0o644，重复执行幂等。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))

from lib.kill3_shadow_view import START, END, apply_kill3_shadow_block  # noqa: E402


def main(argv=None):
    root = Path(argv[1]) if argv and len(argv) > 1 else ROOT
    panel = root / 'panel.html'
    try:
        html = panel.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 0
    updated = apply_kill3_shadow_block(html, root=root)
    if updated == html:
        print('kill3-shadow panel unchanged')
        return 0
    temporary = tempfile.NamedTemporaryFile('w', dir=str(panel.parent), delete=False,
                                            encoding='utf-8')
    try:
        temporary.write(updated)
        temporary.flush()
        os.fsync(temporary.fileno())
    finally:
        temporary.close()
    os.chmod(temporary.name, 0o644)
    os.replace(temporary.name, panel)
    print('kill3-shadow panel updated')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
