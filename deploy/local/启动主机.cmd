@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
python -m alembic upgrade head
if errorlevel 1 pause & exit /b 1
python scripts\run_host.py --allow-lan
