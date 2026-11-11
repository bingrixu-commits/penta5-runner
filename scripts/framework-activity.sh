#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 各框架最近活动 ==="
python3 - <<'PY'
import json, os
from pathlib import Path

ROOT = Path('/root/penta5-runner/data')
rows = []
for d in sorted(ROOT.iterdir()):
    if not d.is_dir():
        continue
    seals = d / 'seals.json'
    if not seals.exists():
        continue
    try:
        recs = json.loads(seals.read_text(encoding='utf-8')).get('records') or []
    except Exception:
        continue
    if not recs:
        rows.append((d.name, 0, '-', '-'))
        continue
    last = recs[-1]
    rows.append((d.name, len(recs), str(last.get('target_period') or '-'),
                 str(last.get('generated_at') or '')[:19]))

print(f'{"平台":<16}{"封存数":>8}  {"最近目标期":<14}{"最近生成时间"}')
for name, count, target, when in rows:
    print(f'{name:<16}{count:>8}  {target:<14}{when}')
PY

echo
echo "=== 作用域开关 ==="
python3 scripts/ai_scope_control.py status 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
for r in d['scopes']:
    print('  %-10s %-5s own_key=%-5s gen=%s' % (r['scope'], 'ON' if r['enabled'] else 'OFF', r['own_key'], r['generation']))
"
