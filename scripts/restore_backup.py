from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.engine import make_url

from app.core.config import settings


def integrity(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"


def sqlite_copy(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source)) as source_db:
        with closing(sqlite3.connect(target)) as target_db:
            source_db.backup(target_db)


def main() -> None:
    parser = argparse.ArgumentParser(description="离线恢复 SQLite 备份；执行前必须停止应用")
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--confirm", required=True, help="必须填写 RESTORE")
    args = parser.parse_args()
    if args.confirm != "RESTORE":
        raise SystemExit("确认文本不正确，未执行恢复")
    backup = args.backup.expanduser().resolve()
    url = make_url(settings.database_url)
    target = (args.target or Path(url.database or "repair_management.db")).expanduser().resolve()
    if not backup.is_file() or integrity(backup).lower() != "ok":
        raise SystemExit("备份不存在或完整性检查失败")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety = target.with_name(f"{target.stem}.before-restore-{stamp}{target.suffix}")
    temp = target.with_suffix(target.suffix + ".restore.tmp")
    if target.exists():
        sqlite_copy(target, safety)
    try:
        sqlite_copy(backup, temp)
        if integrity(temp).lower() != "ok":
            raise SystemExit("恢复临时文件完整性检查失败")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    print(f"恢复完成：{target}")
    if safety.exists():
        print(f"恢复前保护副本：{safety}")


if __name__ == "__main__":
    main()
