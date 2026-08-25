from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.models.entities import BackupRecord


class BackupService:
    @staticmethod
    def _source_path() -> Path:
        url = make_url(settings.database_url)
        if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
            raise BusinessError("当前数据库不是可备份的本地 SQLite 文件", code="backup_not_supported", status_code=409)
        path = Path(url.database)
        return path if path.is_absolute() else (Path.cwd() / path).resolve()

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _integrity(path: Path) -> str:
        try:
            with closing(sqlite3.connect(path)) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
            return str(result[0]) if result else "unknown"
        except sqlite3.DatabaseError as exc:
            return f"failed: {exc}"

    @classmethod
    def create(cls, db: Session, *, created_by: int | None, notes: str | None = None) -> BackupRecord:
        source = cls._source_path()
        if not source.is_file():
            raise BusinessError("数据库文件不存在", code="database_missing", status_code=404)
        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final_path = (settings.backup_dir / f"repair-management-{stamp}.db").resolve()
        temp_path = final_path.with_suffix(".db.tmp")
        try:
            with closing(sqlite3.connect(source)) as source_db:
                with closing(sqlite3.connect(temp_path)) as target_db:
                    source_db.backup(target_db)
            integrity = cls._integrity(temp_path)
            if integrity.lower() != "ok":
                raise BusinessError(f"备份完整性检查失败：{integrity}", code="backup_integrity_failed", status_code=500)
            os.replace(temp_path, final_path)
        finally:
            temp_path.unlink(missing_ok=True)
        record = BackupRecord(
            filename=final_path.name,
            storage_path=str(final_path),
            file_size=final_path.stat().st_size,
            sha256=cls._checksum(final_path),
            database_kind="sqlite",
            status="verified",
            integrity_result="ok",
            created_by=created_by,
            notes=(notes or "").strip()[:500] or None,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    @classmethod
    def verify(cls, db: Session, record: BackupRecord) -> BackupRecord:
        path = Path(record.storage_path).resolve()
        root = settings.backup_dir.resolve()
        if root not in path.parents or not path.is_file():
            record.status = "missing"
            record.integrity_result = "file_missing_or_outside_backup_directory"
        else:
            integrity = cls._integrity(path)
            checksum = cls._checksum(path)
            record.integrity_result = integrity
            record.status = "verified" if integrity.lower() == "ok" and checksum == record.sha256 else "invalid"
        record.verified_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(record)
        return record
