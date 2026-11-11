#!/bin/bash
set -u
cd /root/penta5-runner || exit 1
set -a; . ./.env; set +a
A="${PANEL_USER:-}:${PANEL_PASSWORD:-}"

echo "=== 13 个框架页 + 总览，逐个直连测试 ==="
for s in "" kill1 kill3 kill10 kill11 kill12 kill13 kill14 kill15 cold15 cold30 he3 killer framework; do
  url="http://127.0.0.1:3001/f/$s"
  code=$(curl -s -o /tmp/p.html -w '%{http_code}' -u "$A" "$url")
  size=$(wc -c < /tmp/p.html)
  title=$(grep -o '<title>[^<]*</title>' /tmp/p.html | head -1 | sed 's/<[^>]*>//g')
  printf '  %-24s %s  %6sB  %s\n' "/f/$s" "$code" "$size" "$title"
done
