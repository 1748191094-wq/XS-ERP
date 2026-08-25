@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "ServiceManager.exe" (
  echo 启动失败：程序包不完整，缺少 ServiceManager.exe
  pause
  exit /b 1
)

if not exist ".env" (
  echo 首次运行，需要填写终端名称、主机局域网 IP 和同步密钥。
  "ServiceManager.exe" configure-terminal
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

"ServiceManager.exe" terminal
if errorlevel 1 pause
