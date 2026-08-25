from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integrity_check(path: Path) -> str:
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        row = db.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"


def backup_one(source: Path, backup_dir: Path, stamp: str) -> dict[str, object]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    destination = (backup_dir / f"{source.stem}-{stamp}.db").resolve()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=backup_dir
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with closing(sqlite3.connect(source)) as source_db:
            with closing(sqlite3.connect(temp_path)) as target_db:
                source_db.backup(target_db)

        integrity = integrity_check(temp_path)
        if integrity.lower() != "ok":
            raise RuntimeError(f"SQLite integrity_check failed for {source}: {integrity}")

        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
        Path(f"{temp_path}-wal").unlink(missing_ok=True)
        Path(f"{temp_path}-shm").unlink(missing_ok=True)

    return {
        "source": str(source),
        "backup": str(destination),
        "size": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "integrity_check": "ok",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Online-back up SQLite databases, verify them, and write an atomic manifest."
    )
    parser.add_argument("--source", action="append", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path, default=Path("backups/pre-migration"))
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    backup_dir = args.backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    records = [backup_one(source, backup_dir, stamp) for source in args.source]
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "sqlite3.Connection.backup + PRAGMA integrity_check + SHA-256",
        "notes": args.notes,
        "databases": records,
    }

    final_manifest = backup_dir / f"backup-manifest-{stamp}.json"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{final_manifest.stem}-", suffix=".tmp", dir=backup_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, final_manifest)
    finally:
        Path(temp_name).unlink(missing_ok=True)

    print(json.dumps({"manifest": str(final_manifest), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
