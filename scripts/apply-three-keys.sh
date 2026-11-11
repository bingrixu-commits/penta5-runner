#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
KEYS=/root/keys-tmp.json
chmod 600 "$KEYS" 2>/dev/null || true

echo "=== 1. 校验新 key ==="
python3 - <<'PY'
import json, urllib.request
keys = json.load(open('/root/keys-tmp.json', encoding='utf-8'))
for name in ('penta5-kill10', 'penta5-kill12', 'penta5-kill13'):
    key = keys.get(name)
    if not key:
        print(f'  {name:16s} 缺失'); continue
    req = urllib.request.Request('https://api.deepseek.com/user/balance',
                                 headers={'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'replace'))
        infos = payload.get('balance_infos') or [{}]
        print(f'  {name:16s} OK   余额 {infos[0].get("total_balance")}')
    except Exception as error:
        print(f'  {name:16s} FAIL {type(error).__name__}')
PY

echo
echo "=== 2. 写入 .env ==="
python3 - <<'PY'
import json, re
from pathlib import Path
keys = json.load(open('/root/keys-tmp.json', encoding='utf-8'))
mapping = {
    'PENTA5_API_KEY_KILL10': 'penta5-kill10',
    'PENTA5_API_KEY_KILL12': 'penta5-kill12',
    'PENTA5_API_KEY_KILL13': 'penta5-kill13',
}
path = Path('/root/penta5-runner/.env')
lines = path.read_text(encoding='utf-8').splitlines()
for env_name, key_name in mapping.items():
    new_line = '%s=%s' % (env_name, keys[key_name])
    pattern = re.compile(r'^#?\s*' + re.escape(env_name) + r'=.*$')
    if any(pattern.match(line) for line in lines):
        lines = [new_line if pattern.match(line) else line for line in lines]
    else:
        lines.append(new_line)
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('已写入 %d 个变量' % len(mapping))
PY

echo
echo "=== 3. shred 临时文件 ==="
shred -u "$KEYS" 2>/dev/null || rm -f "$KEYS"
echo "removed: $([ -f "$KEYS" ] && echo no || echo yes)"

echo
echo "=== 4. 全作用域独立 key 核对 ==="
python3 - <<'PY'
import sys
sys.path.insert(0, 'lib')
import ai_scope
rows = ai_scope.describe()
own = [r for r in rows if r['own_key']]
shared = [r for r in rows if not r['own_key']]
print(f'独立 key: {len(own)} 个 -> ' + ', '.join(r['scope'] for r in own))
print(f'仍共享  : {len(shared)} 个 -> ' + (', '.join(r['scope'] for r in shared) or '无'))
PY

echo
echo "=== 5. 面板接口是否同步 ==="
set -a; . ./.env; set +a
curl -s -u "${PANEL_USER:-}:${PANEL_PASSWORD:-}" http://127.0.0.1:3010/api/ai-scope \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
own=[r['scope'] for r in d['scopes'] if r['own_key']]
print('接口报告独立 key:', len(own), '个')
"
