@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 本工具只能在管理主机本机运行，将先创建并校验数据库备份。
echo 不会在命令行、日志或数据库中保存明文密码。
echo.
if exist ".venv\Scripts\python.exe" goto venv
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" goto localpython
where py >nul 2>nul
if %errorlevel%==0 goto py
where python >nul 2>nul
if %errorlevel%==0 goto python
echo 未找到 Python，请改用免 Python 发布包中的 ServiceManager.exe recover-admin。
pause
exit /b 1
:venv
".venv\Scripts\python.exe" scripts\windows_launcher.py recover-admin
goto finish
:localpython
"%LocalAppData%\Programs\Python\Python311\python.exe" scripts\windows_launcher.py recover-admin
goto finish
:py
py -3 scripts\windows_launcher.py recover-admin
goto finish
:python
python scripts\windows_launcher.py recover-admin
:finish
set "SERVICE_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%SERVICE_EXIT_CODE%"=="0" echo 恢复未完成，账户没有被修改。
pause
exit /b %SERVICE_EXIT_CODE%
