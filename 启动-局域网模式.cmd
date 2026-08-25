@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto venv
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" goto localpython
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 goto py
)
where python >nul 2>nul
if %errorlevel%==0 goto python
echo Python was not found. See START_HERE.md for setup instructions.
pause
exit /b 1
:venv
".venv\Scripts\python.exe" scripts\windows_launcher.py host --allow-lan --no-browser
goto finish
:localpython
"%LocalAppData%\Programs\Python\Python311\python.exe" scripts\windows_launcher.py host --allow-lan --no-browser
goto finish
:py
py -3 scripts\windows_launcher.py host --allow-lan --no-browser
goto finish
:python
python scripts\windows_launcher.py host --allow-lan --no-browser
goto finish
:finish
set "SERVICE_EXIT_CODE=%ERRORLEVEL%"
if "%SERVICE_EXIT_CODE%"=="0" exit /b 0
echo.
pause
exit /b %SERVICE_EXIT_CODE%
