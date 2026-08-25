from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal, create_schema
from app.services.backup import BackupService


def main() -> None:
    parser = argparse.ArgumentParser(description="创建并校验维修管理数据库备份")
    parser.add_argument("--notes", default="命令行手动备份")
    args = parser.parse_args()
    create_schema()
    with SessionLocal() as db:
        record = BackupService.create(db, created_by=None, notes=args.notes)
    print(f"备份完成：{record.storage_path}")
    print(f"SHA-256：{record.sha256}")


if __name__ == "__main__":
    main()
