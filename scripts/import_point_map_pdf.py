from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.core.database import SessionLocal, create_schema
from app.models.entities import Attachment, PointMap
from app.services.point_map_assets import render_pdf_page_png
from app.storage.local import LocalStorageService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将本机位号图 PDF 的指定页面导入设备点位图库")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--category", default="无人机")
    parser.add_argument("--series")
    parser.add_argument("--model", required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--board-code")
    parser.add_argument("--title", required=True)
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--access-level", default="restricted", choices=["internal", "restricted"])
    parser.add_argument("--no-crop", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.pdf.resolve()
    if not source.is_file():
        raise SystemExit(f"PDF 不存在：{source}")
    content = source.read_bytes()
    rendered = render_pdf_page_png(content, args.page, auto_crop=not args.no_crop)
    create_schema()
    with SessionLocal() as db:
        existing = db.scalar(select(PointMap).where(
            PointMap.brand == args.brand,
            PointMap.model_pattern == args.model,
            PointMap.module_name == args.module,
            PointMap.title == args.title,
            PointMap.version == args.version,
        ))
        if existing:
            print(f"exists:{existing.id}")
            return 0
        storage = LocalStorageService()
        stored_source = storage.save_bytes(source.name, content, folder="point_map_sources")
        source_attachment = Attachment(
            attachment_type="point_map_source_pdf",
            original_filename=stored_source.original_filename,
            storage_path=stored_source.storage_path,
            content_type="application/pdf",
            file_size=stored_source.file_size,
            sha256=stored_source.sha256,
        )
        db.add(source_attachment)
        db.flush()
        image_name = f"{source.stem}-page-{args.page}.png"
        stored_image = storage.save_bytes(image_name, rendered, folder="point_maps")
        image_attachment = Attachment(
            attachment_type="point_map_image",
            original_filename=stored_image.original_filename,
            storage_path=stored_image.storage_path,
            content_type="image/png",
            file_size=stored_image.file_size,
            sha256=stored_image.sha256,
        )
        db.add(image_attachment)
        db.flush()
        point_map = PointMap(
            brand=args.brand,
            product_category=args.category,
            series=args.series,
            model_pattern=args.model,
            module_name=args.module,
            board_code=args.board_code,
            title=args.title,
            version=args.version,
            status="published",
            image_attachment_id=image_attachment.id,
            source_attachment_id=source_attachment.id,
            source_page=args.page,
            source_reference=str(source),
            access_level=args.access_level,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(point_map)
        db.commit()
        print(f"created:{point_map.id};page:{args.page};image_bytes:{stored_image.file_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
