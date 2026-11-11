#!/bin/bash
set -u
cd /root/penta5-runner || exit 1

echo "=== 登录成功后的跳转目标 ==="
grep -n "sendPasswordLoginHtml\|/login\|redirect" penta5_backend_runner.js | grep -iE "login|redirect" | head -20

echo
echo "=== 登录处理函数 ==="
grep -n "function sendPasswordLoginHtml\|function handleLogin\|passwordLogin" penta5_backend_runner.js | head -10

echo
echo "=== 是否有写死的 /panel 回跳 ==="
grep -n '"/panel"' penta5_backend_runner.js | head -10
