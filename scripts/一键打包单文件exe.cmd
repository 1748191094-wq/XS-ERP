@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo   Service ERP - onefile exe build
echo ==============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows_onefile.ps1"
set EXITCODE=%ERRORLEVEL%

echo.
echo Build finished with exit code %EXITCODE%.
echo.
pause
