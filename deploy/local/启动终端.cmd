@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
python -m alembic upgrade head
if errorlevel 1 pause & exit /b 1
start "周期同步" /min python scripts\run_sync_node.py --watch
python scripts\run_host.py --standalone
