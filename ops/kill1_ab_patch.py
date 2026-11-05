"""幂等安装 kill1 A/B 挂钩到 scripts/run_kill1_tail.py（一次性部署脚本）。

用法：python3 kill1_ab_patch.py [--check]
- --check 只验证锚点，不写文件。
"""
from pathlib import Path
import os
import py_compile
import shutil
import sys
import time

ROOT = Path('/root/penta5-runner')
TARGET = ROOT / 'scripts' / 'run_kill1_tail.py'
BACKUP_DIR = Path('/root/backups') / ('kill1-ab-' + time.strftime('%Y%m%d-%H%M%S'))

HELPER_ANCHOR = '''        raise SystemExit(0)


def main():
'''

HELPER_INSERT = '''        raise SystemExit(0)


# --- kill1 pro-vs-flash 影子 A/B 挂钩（只读；任何异常都不影响主体发布） ---
def _ab_record_raw_and_shadow(prediction, meta, reference, target, prompt):
    """落 flash 原始预测到 A/B 台账，并 spawn pro 影子；本函数永不抛异常。"""
    import os
    try:
        import kill1_ab
        kill1_ab.record_flash_raw(
            prediction, meta, reference, target, prompt,
            log=lambda message: print(message))
        kill1_ab.spawn_pro_shadow(
            prompt, reference, target,
            log=lambda message: print(message))
    except Exception as error:
        try:
            print('kill1 ab hook failed: %s: %s'
                  % (type(error).__name__, str(error)[:120]))
        except Exception:
            pass
# --- A/B 挂钩结束 ---


def main():
'''

HOOK_ANCHOR = '''        print(f"kill1 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    fresh = load_snapshot(persist=False)
'''

HOOK_INSERT = '''        print(f"kill1 request failed: {type(error).__name__}: {str(error)[:140]}")
        return 1
    _ab_record_raw_and_shadow(prediction, meta, reference, target, prompt)
    fresh = load_snapshot(persist=False)
'''


def main(argv):
    check_only = '--check' in argv
    text = TARGET.read_text(encoding='utf-8')
    already = ('_ab_record_raw_and_shadow' in text)
    helper_count = text.count(HELPER_ANCHOR)
    hook_count = text.count(HOOK_ANCHOR)
    print('target=%s lines=%d bytes=%d' % (TARGET, text.count(chr(10)) + 1, len(text)))
    print('helper_anchor_count=%d hook_anchor_count=%d already_patched=%s'
          % (helper_count, hook_count, already))
    if already and hook_count == 1:
        print('ALREADY_PATCHED')
        return 0
    if helper_count != 1 or hook_count != 1:
        print('ANCHOR_MISMATCH: refuse to patch')
        return 2
    if check_only:
        print('CHECK_OK')
        return 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / 'run_kill1_tail.py'
    shutil.copy2(TARGET, backup)
    new_text = text.replace(HELPER_ANCHOR, HELPER_INSERT, 1)
    new_text = new_text.replace(HOOK_ANCHOR, HOOK_INSERT, 1)
    if new_text == text or new_text.count(HELPER_INSERT) != 1 or new_text.count(HOOK_INSERT) != 1:
        print('PATCH_RESULT_INVALID: abort')
        return 2
    tmp = TARGET.with_suffix('.py.ab_tmp')
    tmp.write_text(new_text, encoding='utf-8')
    os.replace(tmp, TARGET)
    py_compile.compile(str(TARGET), doraise=True)
    print('PATCH_OK backup=%s new_lines=%d' % (backup, new_text.count(chr(10)) + 1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
