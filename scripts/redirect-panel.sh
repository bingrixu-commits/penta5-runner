#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== /panel 也跳 /f/，长版移到 /full ==="
python3 - <<'PY'
from pathlib import Path
p = Path('/root/penta5-runner/penta5_backend_runner.js')
text = p.read_text(encoding='utf-8')

old = '''    if (req.method === "GET" && pathname === "/panel") {
      sendPanelHtml(req, res);
      return;
    }'''
new = '''    if (req.method === "GET" && pathname === "/panel") {
      res.writeHead(302, {"Location": "/f/", "Cache-Control": "no-store"});
      res.end();
      return;
    }
    if (req.method === "GET" && pathname === "/full") {
      sendPanelHtml(req, res);
      return;
    }'''
if old not in text:
    raise SystemExit('panel route anchor missing')
text = text.replace(old, new, 1)

# 登录白名单：允许 /full；/panel 仍允许（随后再跳 /f/）
old2 = '  if (text === "/attack-txt" || text === "/gongjiyu" || text === "/panel" || isAccuracyPath(text) || isHitratePath(text) || isStatsPath(text)) return text;'
new2 = '  if (text === "/attack-txt" || text === "/gongjiyu" || text === "/panel" || text === "/full" || isAccuracyPath(text) || isHitratePath(text) || isStatsPath(text)) return text;'
if old2 not in text:
    raise SystemExit('whitelist anchor missing')
text = text.replace(old2, new2, 1)

p.write_text(text, encoding='utf-8')
print('已修改')
PY

echo
echo "=== 语法与重启 ==="
node --check penta5_backend_runner.js && echo "syntax OK"
pm2 restart penta5-runner --update-env 2>&1 | tail -1
sleep 6

echo
echo "=== 验证 ==="
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"
for u in / /panel /full /f/ /f/kill15; do
  printf '  %-12s -> ' "$u"
  curl -s -o /dev/null -w '%{http_code} %{redirect_url} %{size_download}B\n' -u "$A" "http://127.0.0.1:3001$u"
done

echo
echo "=== 提交 ==="
git add penta5_backend_runner.js
git -c user.name=ops-bot -c user.email=ops@local commit -m "feat(panel): /panel 也跳转框架总览，长版改为 /full" 2>&1 | tail -2
git push origin main 2>&1 | tail -1
