#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== panel.html 解析函数上下文 ==="
sed -n '15215,15235p' penta5_backend_runner.js

echo
echo "=== 谁调用它 ==="
grep -n "panelPath\|resolvePanel\|panelHtmlPath" penta5_backend_runner.js | head -10

echo
echo "=== '/' 的分发处 ==="
grep -n 'requestPath === "/"\|pathname === "/"\|=== "/index.html"' penta5_backend_runner.js | head -10
