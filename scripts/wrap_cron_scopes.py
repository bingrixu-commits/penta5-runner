#!/usr/bin/env python3
"""把现有 crontab 里的 AI 任务逐个包上 with_scope.sh，实现按框架隔离。

幂等：已包过的不会再包。改前自动备份 crontab。
"""
import re
import subprocess
import sys

# 脚本名 -> 作用域
MAPPING = {
    'run_kill1_tail.py': 'kill1',
    'run_kill3_tail.py': 'kill3',
    'run_kill10_tail.py': 'kill10',
    'run_kill11_tail.py': 'kill11',
    'run_kill12_tail.py': 'kill12',
    'run_kill13_tail.py': 'kill13',
    'run_kill14_tail.py': 'kill14',
    'run_kill15_tail.py': 'kill15',
    'run_cold15_tail.py': 'cold15',
    'run_cold30_tail.py': 'cold30',
    'run_he3_refine.py': 'he3',
}
# run_ai_ready_job.py 带参数
READY = {'killer': 'killer', 'framework': 'framework'}

WRAPPER = 'bash scripts/with_scope.sh '


def main(apply=False):
    current = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if current.returncode != 0:
        sys.exit('cannot read crontab: ' + current.stderr[:200])
    lines = current.stdout.splitlines()
    changed = []

    out = []
    for line in lines:
        if 'with_scope.sh' in line or line.strip().startswith('#'):
            out.append(line)
            continue
        new = line
        for script, scope in MAPPING.items():
            needle = 'python3 -u scripts/' + script
            if needle in new:
                new = new.replace(needle, WRAPPER + scope + ' ' + needle, 1)
                break
        else:
            for job, scope in READY.items():
                needle = 'python3 -u scripts/run_ai_ready_job.py ' + job
                if needle in new:
                    new = new.replace(needle, WRAPPER + scope + ' ' + needle, 1)
                    break
        if new != line:
            changed.append((line.strip()[:80], scope if 'scope' in dir() else ''))
        out.append(new)

    print('待修改行数:', len(changed))
    for before, _ in changed[:15]:
        print('  -', before)

    text = '\n'.join(out) + '\n'
    if not apply:
        print('\n(dry-run，未写入；加 --apply 生效)')
        return 0

    subprocess.run(['crontab', '-'], input=text, text=True, check=True)
    print('\n已写入 crontab')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(apply='--apply' in sys.argv))
