#!/bin/bash
set -e
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
USER_NAME="${PANEL_USER:-admin}"
PASS="${PANEL_PASSWORD}"
URL="http://${USER_NAME}:${PASS}@127.0.0.1:3001/panel"
OUT=/tmp/panel_dom.html
google-chrome --headless --disable-gpu --no-sandbox --dump-dom "$URL" > "$OUT" 2>/tmp/chrome_err.txt || true
sleep 1
# Wait JS? dump-dom may not wait for progressive render. Check static markers first.
echo "=== static markers ==="
grep -o "txtThreeRemainTopBox\|three_remain_pure_v189\|只回删旧码，不加排除码\|五路横财数据分析\|panel-hero__center\|再叠加替补定位杀码" "$OUT" | sort | uniq -c
echo "=== title/h1 ==="
python3 - <<'PY'
from html.parser import HTMLParser
import re
html=open("/tmp/panel_dom.html","r",encoding="utf-8",errors="ignore").read()
print("len",len(html))
m=re.search(r"<h1[^>]*>(.*?)</h1>",html,re.I|re.S)
print("h1", re.sub(r"<[^>]+>","",m.group(1)).strip() if m else None)
print("hasTopBox", "txtThreeRemainTopBox" in html)
print("hasPure", "只回删旧码，不加排除码" in html)
print("hasOldPhrase", "再叠加替补定位杀码" in html)
print("version", "three_remain_pure_v189" in html)
PY
