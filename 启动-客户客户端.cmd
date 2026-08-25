@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"
title ERP 客户客户端

if /I "%~1"=="--check" goto :check

rem If the service is already running, open the client directly.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
    start "" "http://127.0.0.1:8000/client"
    exit /b 0
)

call :find_python
if not defined ERP_PYTHON goto :python_missing

echo 正在启动 ERP 服务，请勿关闭此窗口……
echo 服务就绪后将自动打开客户客户端。
echo.

start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$health = 'http://127.0.0.1:8000/api/health'; $client = 'http://127.0.0.1:8000/client'; for ($i = 0; $i -lt 120; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -Uri $health -TimeoutSec 2; if ($r.StatusCode -eq 200) { Start-Process $client; exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; exit 1"

%ERP_PYTHON% scripts\run_host.py --standalone
set "ERP_EXIT_CODE=%ERRORLEVEL%"
if not "%ERP_EXIT_CODE%"=="0" (
    echo.
    echo 启动失败，错误码：%ERP_EXIT_CODE%
    pause
)
exit /b %ERP_EXIT_CODE%

:check
call :find_python
if not defined ERP_PYTHON goto :python_missing
%ERP_PYTHON% -c "from pathlib import Path; import app.main; assert Path('app/static/client/index.html').is_file(); print('CLIENT_LAUNCHER_CHECK_OK')"
exit /b %ERRORLEVEL%

:find_python
set "ERP_PYTHON="
where python >nul 2>nul
if not errorlevel 1 set "ERP_PYTHON=python"
if defined ERP_PYTHON exit /b 0
where py >nul 2>nul
if not errorlevel 1 set "ERP_PYTHON=py -3"
exit /b 0

:python_missing
echo 未找到 Python。请先安装 Python 3，并勾选“Add Python to PATH”。
pause
exit /b 1
