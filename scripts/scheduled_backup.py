from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import BackupRecord, SystemSetting
from app.services.backup import BackupService


def setting(rows: dict[str, str], key: str, default: str) -> str:
    return rows.get(key, default).strip()


def copy_offsite(record: BackupRecord, target_root: Path) -> Path:
    target_root.mkdir(parents=True, exist_ok=True)
    source = Path(record.storage_path)
    target = (target_root / source.name).resolve()
    temp = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(source, temp)
    if BackupService._checksum(temp) != record.sha256 or BackupService._integrity(temp).lower() != "ok":
        temp.unlink(missing_ok=True)
        raise RuntimeError("异地副本校验失败")
    os.replace(temp, target)
    return target


def prune(records: list[BackupRecord], *, retention_days: int, keep_count: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for index, record in enumerate(records):
        if index < keep_count or record.created_at >= cutoff:
            continue
        path = Path(record.storage_path).resolve()
        if settings.backup_dir.resolve() in path.parents:
            path.unlink(missing_ok=True)
            record.status = "pruned"
            removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Create, verify, retain, and optionally copy a scheduled SQLite backup")
    parser.add_argument("--force", action="store_true", help="Run even when backup.auto_enabled is false")
    args = parser.parse_args()
    with SessionLocal() as db:
        rows = {row.key: row.value for row in db.scalars(select(SystemSetting).where(SystemSetting.key.like("backup.%")))}
        enabled = setting(rows, "backup.auto_enabled", "false").lower() == "true"
        if not enabled and not args.force:
            print("Scheduled backup is disabled; nothing to do.")
            return 0
        retention_days = max(1, int(setting(rows, "backup.retention_days", "30")))
        keep_count = max(1, int(setting(rows, "backup.keep_count", "30")))
        record = BackupService.create(db, created_by=None, notes="每日自动校验备份")
        offsite = setting(rows, "backup.offsite_dir", "")
        offsite_path = copy_offsite(record, Path(offsite).expanduser().resolve()) if offsite else None
        records = list(db.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc())))
        removed = prune(records, retention_days=retention_days, keep_count=keep_count)
        db.commit()
        print({
            "backup": record.storage_path,
            "sha256": record.sha256,
            "integrity": record.integrity_result,
            "offsite": str(offsite_path) if offsite_path else None,
            "pruned": removed,
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
