@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "ServiceManager.exe" (
  echo 恢复失败：程序包不完整，缺少 ServiceManager.exe
  pause
  exit /b 1
)
echo 本工具只能在管理主机本机运行，将先创建并校验数据库备份。
echo 不会在命令行、日志或数据库中保存明文密码。
echo.
"ServiceManager.exe" recover-admin
set "SERVICE_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%SERVICE_EXIT_CODE%"=="0" echo 恢复未完成，账户没有被修改。
pause
exit /b %SERVICE_EXIT_CODE%
