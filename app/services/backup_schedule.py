from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import BASE_DIR, settings
from app.core.exceptions import BusinessError


TASK_NAME = "SERVICE-Daily-Verified-Backup"


def _uses_primary_database() -> bool:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return False
    database = Path(url.database)
    database = database.resolve() if database.is_absolute() else (BASE_DIR / database).resolve()
    return database == (BASE_DIR / "repair_management.db").resolve()


def sync_windows_backup_task(*, run_at: str, enabled: bool) -> dict:
    """Keep the real host task aligned with the admin schedule without touching test databases."""
    if os.name != "nt":
        return {"managed": False, "reason": "non_windows"}
    if not _uses_primary_database():
        return {"managed": False, "reason": "non_primary_database"}
    installer = (BASE_DIR / "scripts" / "install_backup_task.ps1").resolve()
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer),
        "-Time", run_at, "-TaskName", TASK_NAME,
    ]
    if not enabled:
        command.append("-Disabled")
    try:
        result = subprocess.run(command, cwd=BASE_DIR, capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BusinessError("更新 Windows 自动备份任务失败", code="backup_task_update_failed", status_code=500) from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "计划任务更新失败").strip()[-800:]
        raise BusinessError(message, code="backup_task_update_failed", status_code=500)
    return {"managed": True, "task_name": TASK_NAME, "enabled": enabled, "time": run_at}
