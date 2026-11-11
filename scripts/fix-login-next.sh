#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 修登录回跳白名单 ==="
python3 - <<'PY'
from pathlib import Path
p = Path('/root/penta5-runner/penta5_backend_runner.js')
text = p.read_text(encoding='utf-8')

old = '''function safeLoginNextPath(raw) {
  const text = String(raw || "").trim();
  if (text === "/attack-txt" || text === "/gongjiyu" || text === "/panel" || isAccuracyPath(text) || isHitratePath(text) || isStatsPath(text)) return text;
  return "/panel";
}'''
new = '''function safeLoginNextPath(raw) {
  const text = String(raw || "").trim();
  // 框架分页（/f/ 与 /f/<scope>）也允许作为登录回跳目标
  if (/^\\/f(?:\\/[a-z0-9]{2,16})?\\/?$/.test(text)) return text;
  if (text === "/attack-txt" || text === "/gongjiyu" || text === "/panel" || isAccuracyPath(text) || isHitratePath(text) || isStatsPath(text)) return text;
  return "/f/";
}'''
if old not in text:
    raise SystemExit('whitelist anchor missing')
text = text.replace(old, new, 1)

old2 = 'const next = safeLoginNextPath(nextPath || (req && req.url ? new URL(req.url, "http://localhost").searchParams.get("next") : "") || "/panel");'
new2 = 'const next = safeLoginNextPath(nextPath || (req && req.url ? new URL(req.url, "http://localhost").searchParams.get("next") : "") || "/f/");'
if old2 not in text:
    raise SystemExit('default anchor missing')
text = text.replace(old2, new2, 1)

p.write_text(text, encoding='utf-8')
print('已修改：登录默认回跳 /f/，且 /f/<scope> 可作为 next')
PY

echo
echo "=== 语法与重启 ==="
node --check penta5_backend_runner.js && echo "syntax OK"
pm2 restart penta5-runner --update-env 2>&1 | tail -1
sleep 6

echo
echo "=== 验证 ==="
set -a; . ./.env; set +a
echo -n "  GET /login 页面里的 next -> "
curl -s http://127.0.0.1:3010/login | grep -o 'name="next"[^>]*value="[^"]*"' | head -1
echo -n "  / 未登录跳转 -> "
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3010/
echo -n "  / 已登录 -> "
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" -u "${PANEL_USER:-}:${PANEL_PASSWORD:-}" http://127.0.0.1:3010/

echo
echo "=== 提交 ==="
git add penta5_backend_runner.js
git -c user.name=ops-bot -c user.email=ops@local commit -m "fix(panel): 登录后默认回跳框架总览 /f/（原白名单缺 /f/ 导致跳回长版）" 2>&1 | tail -2
git push origin main 2>&1 | tail -1
