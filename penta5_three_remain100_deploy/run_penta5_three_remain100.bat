@echo off
chcp 65001 >nul
title Penta5 三定四组合真实旧码回删100
setlocal

set "SSH_EXE=%SystemRoot%\System32\OpenSSH\ssh.exe"
set "SCP_EXE=%SystemRoot%\System32\OpenSSH\scp.exe"
set "SSH_KEY=C:\Users\Administrator\.ssh\id_ed25519"
set "SERVER=admin@8.210.5.221"
set "LOCAL_SCRIPT=%~dp0penta5_apply_three_remain100.sh"
set "REMOTE_SCRIPT=/tmp/penta5_apply_three_remain100.sh"

if not exist "%SSH_EXE%" goto NO_SSH
if not exist "%SCP_EXE%" goto NO_SCP
if not exist "%SSH_KEY%" goto NO_KEY
if not exist "%LOCAL_SCRIPT%" goto NO_SCRIPT

echo [1/2] 上传安全修复脚本...
"%SCP_EXE%" -o IdentitiesOnly=yes -i "%SSH_KEY%" "%LOCAL_SCRIPT%" "%SERVER%:%REMOTE_SCRIPT%"
if errorlevel 1 goto FAILED

echo.
echo [2/2] 核验并只修改 /root/penta5-runner/panel.html ...
"%SSH_EXE%" -t -o IdentitiesOnly=yes -i "%SSH_KEY%" "%SERVER%" "sudo bash %REMOTE_SCRIPT% /root/penta5-runner; rc=$?; rm -f %REMOTE_SCRIPT%; exit $rc"
if errorlevel 1 goto FAILED

echo.
echo 完成。没有重启 PM2，没有修改 RDS、后端、接口或 PM2 配置。
pause
exit /b 0

:NO_SSH
echo ERROR: 找不到 Windows OpenSSH: %SSH_EXE%
goto FAILED
:NO_SCP
echo ERROR: 找不到 Windows SCP: %SCP_EXE%
goto FAILED
:NO_KEY
echo ERROR: 找不到 SSH 私钥: %SSH_KEY%
goto FAILED
:NO_SCRIPT
echo ERROR: 找不到同目录脚本: %LOCAL_SCRIPT%
goto FAILED
:FAILED
echo.
echo 执行失败或安全校验拒绝。服务器 panel.html 未通过时会自动回滚；源 SHA 不匹配时不会修改。
pause
exit /b 1
