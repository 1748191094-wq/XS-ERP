from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (PROJECT_ROOT / "build", PROJECT_ROOT / "deploy")
QUARANTINE_ROOT = PROJECT_ROOT / "backups" / "quarantine" / "release-runtime-data"
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidates() -> list[Path]:
    rows: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        resolved_root = root.resolve()
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved_root not in resolved.parents:
                raise RuntimeError(f"候选文件越出扫描目录：{resolved}")
            if path.name == ".env" or path.suffix.lower() in DATABASE_SUFFIXES:
                rows.append(resolved)
    return sorted(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move .env and database files out of build/deploy into a recoverable quarantine."
    )
    parser.add_argument("--apply", action="store_true", help="实际移动；省略时只预览")
    args = parser.parse_args()
    rows = candidates()
    preview = [str(path.relative_to(PROJECT_ROOT)) for path in rows]
    if not args.apply:
        print(json.dumps({"apply": False, "count": len(rows), "files": preview}, ensure_ascii=False, indent=2))
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination_root = (QUARANTINE_ROOT / stamp).resolve()
    destination_root.mkdir(parents=True, exist_ok=False)
    manifest_rows: list[dict[str, object]] = []
    for source in rows:
        relative = source.relative_to(PROJECT_ROOT)
        target = (destination_root / relative).resolve()
        if destination_root not in target.parents:
            raise RuntimeError(f"隔离目标越出目录：{target}")
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        checksum = sha256_file(source)
        size = source.stat().st_size
        shutil.move(str(source), str(target))
        manifest_rows.append(
            {
                "original": str(relative),
                "quarantined": str(target.relative_to(PROJECT_ROOT)),
                "bytes": size,
                "sha256": checksum,
                "kind": "environment" if source.name == ".env" else "database",
            }
        )
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "operation": "move_only_no_delete",
        "count": len(manifest_rows),
        "restore": "Move each quarantined path back to its original path after confirming the target is absent.",
        "files": manifest_rows,
    }
    manifest_path = destination_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"quarantine": str(destination_root), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
