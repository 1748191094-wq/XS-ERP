from __future__ import annotations

import os
from pathlib import Path


def configure_test_runtime(
    tmp_path: Path,
    *,
    database_name: str = "test.db",
    sync_role: str = "standalone",
    sync_shared_secret: str = "",
) -> None:
    """Point the existing model registry at one isolated test runtime."""

    from app.core.config import settings
    from app.core.database import configure_database

    paths = {
        "upload_dir": tmp_path / "uploads",
        "report_dir": tmp_path / "reports",
        "email_snapshot_dir": tmp_path / "email-snapshots",
        "backup_dir": tmp_path / "backups",
        "point_map_reference_root": tmp_path / "point-maps",
    }
    environment_names = {
        "upload_dir": "UPLOAD_DIR",
        "report_dir": "REPORT_DIR",
        "email_snapshot_dir": "EMAIL_SNAPSHOT_DIR",
        "backup_dir": "BACKUP_DIR",
        "point_map_reference_root": "POINT_MAP_REFERENCE_ROOT",
    }
    for attribute, path in paths.items():
        if attribute != "point_map_reference_root":
            path.mkdir(parents=True, exist_ok=True)
        object.__setattr__(settings, attribute, path.resolve())
        os.environ[environment_names[attribute]] = str(path.resolve())
    object.__setattr__(settings, "email_mode", "mock")
    object.__setattr__(settings, "sync_role", sync_role)
    object.__setattr__(settings, "sync_shared_secret", sync_shared_secret)
    object.__setattr__(settings, "trusted_hosts", ("testserver", "localhost", "127.0.0.1"))
    object.__setattr__(settings, "enforce_database_revision", False)
    database_url = f"sqlite:///{(tmp_path / database_name).as_posix()}"
    os.environ["DATABASE_URL"] = database_url
    configure_database(database_url)
