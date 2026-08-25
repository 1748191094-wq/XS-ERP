@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "ServiceManager.exe" (
  echo 启动失败：程序包不完整，缺少 ServiceManager.exe
  pause
  exit /b 1
)

if not exist ".env" (
  echo 首次运行，正在创建主机配置和随机同步密钥……
  "ServiceManager.exe" configure-host
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

"ServiceManager.exe" host --allow-lan
if errorlevel 1 pause
