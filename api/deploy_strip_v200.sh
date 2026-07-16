#!/bin/bash
set -e
cp -f /tmp/panel.html /root/penta5-runner/panel.html
grep -n "strip_full_keep_segments_v200\|Object.keys(seen).length>=10" /root/penta5-runner/panel.html | head -10
# unit-ish check of strip function via node
node -e '
const fs=require("fs");
const t=fs.readFileSync("/root/penta5-runner/panel.html","utf8");
const m=t.match(/function stripUntouchedFrameworkPositionSegments\(text\)\{[\s\S]*?\n  \}/);
if(!m){console.error("fn missing"); process.exit(1)}
eval(m[0]);
const raw="千123456780百1234567890十134567890个1234567890四定千百十合134567890百十个合234567890除双单单单除小小大小二数合0246三数合01348各";
const out=stripUntouchedFrameworkPositionSegments(raw);
console.log(out);
if(out.indexOf("百1234567890")>=0||out.indexOf("个1234567890")>=0){console.error("FAIL still full"); process.exit(1)}
if(out.indexOf("千123456780")<0||out.indexOf("十134567890")<0){console.error("FAIL removed real"); process.exit(1)}
console.log("strip ok");
'
pm2 restart penta5-runner --update-env
sleep 2
cd /root/penta5-runner
set -a
source <(grep -E '^(PANEL_USER|PANEL_PASSWORD)=' .env | sed 's/\r$//')
set +a
COOKIE=/tmp/pc.txt
curl -s -c "$COOKIE" -H 'Content-Type: application/json' -d "{\"password\":\"${PANEL_PASSWORD}\"}" http://127.0.0.1:3001/login >/dev/null
curl -s -b "$COOKIE" http://127.0.0.1:3001/panel | grep -o "strip_full_keep_segments_v200" | head -1
