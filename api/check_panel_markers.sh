#!/bin/bash
grep -oE 'killFrameworkBox|自动推荐使用6项|今日六项最高连中|PANEL_CANONICAL_PATH' /root/penta5-runner/panel.html | sort | uniq -c
grep -o 'PANEL_UI_VERSION="[^"]*"' /root/penta5-runner/panel.html | head -1
curl -s -o /dev/null -w 'panel_public=%{http_code}\n' http://8.210.5.221:3001/panel
# source-proxy(:3000) 已废弃；开奖源健康看官网 SOURCE_API_URL / runner health
curl -s -o /dev/null -w 'runner_health=%{http_code}\n' http://127.0.0.1:3001/health
