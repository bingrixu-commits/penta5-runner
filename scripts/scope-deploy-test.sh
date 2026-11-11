#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 1. .env 追加独立密钥模板（已有则跳过）==="
python3 - <<'PY'
from pathlib import Path
path = Path('/root/penta5-runner/.env')
text = path.read_text(encoding='utf-8') if path.exists() else ''
if 'PENTA5_API_KEY_KILL15' in text:
    print('模板已存在')
else:
    block = []
    block.append('')
    block.append('# ---- 按框架独立额度（可选）：填了哪条，哪个框架就用独立 key ----')
    block.append('# 未填的框架回退到共享 key（DEEPSEEK_API_KEY / PENTA5_KILLER_API_KEY）')
    for scope in ('KILL1', 'KILL3', 'KILL10', 'KILL11', 'KILL12', 'KILL13',
                  'KILL14', 'KILL15', 'COLD15', 'COLD30', 'KILLER',
                  'FRAMEWORK', 'HE3', 'REPLAY'):
        block.append('# PENTA5_API_KEY_%s=' % scope)
    path.write_text(text + '\n'.join(block) + '\n', encoding='utf-8')
    print('已追加 %d 行模板' % len(block))
PY

echo
echo "=== 2. 余额守卫 dry-run（无独立 key 时应跳过 scope 段）==="
timeout 60 python3 -u scripts/ai_balance_guard.py --dry-run 2>&1 | tail -3

echo
echo "=== 3. 通过 with_scope 跑一期 kill15（应正常出号或按窗口跳过）==="
timeout 120 bash scripts/with_scope.sh kill15 python3 -u scripts/run_kill15_tail.py 2>&1 | grep -vE "Warning|warnings.warn" | head -6
