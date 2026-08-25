from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Attachment, PointMap, TaskRecord
from app.services.point_map_assets import render_pdf_page_png
from app.services.point_map_import import discover_point_map_sources, infer_point_map_metadata
from app.storage.local import LocalStorageService


logger = logging.getLogger(__name__)


def import_point_map_library_task(task_id: int, uploaded_by: int | None = None) -> None:
    with SessionLocal() as db:
        task = db.get(TaskRecord, task_id)
        if not task:
            return
        imported = skipped = failed = processed = 0
        try:
            task.status, task.progress, task.started_at = "running", 2, datetime.now(timezone.utc)
            task.message = "正在扫描点位图资料目录"
            db.commit()
            sources = discover_point_map_sources(
                settings.point_map_reference_root,
                settings.point_map_import_batch_limit,
            )
            total_pages = sum(source.page_count for source in sources)
            if not total_pages:
                task.status, task.progress, task.finished_at = "completed", 100, datetime.now(timezone.utc)
                task.message = "资料目录中没有找到名称含位号图、位置图、丝印图或点位图的 PDF"
                db.commit()
                return

            storage = LocalStorageService()
            for source in sources:
                content = source.path.read_bytes()
                source_attachment = db.scalar(
                    select(Attachment)
                    .where(
                        Attachment.attachment_type == "point_map_source_pdf",
                        Attachment.sha256 == source.sha256,
                    )
                    .order_by(Attachment.id)
                )
                for page_number in range(1, source.page_count + 1):
                    processed += 1
                    existing = db.scalar(
                        select(PointMap)
                        .join(Attachment, PointMap.source_attachment_id == Attachment.id)
                        .where(Attachment.sha256 == source.sha256, PointMap.source_page == page_number)
                    )
                    if existing:
                        skipped += 1
                    else:
                        try:
                            if source_attachment is None:
                                stored_source = storage.save_bytes(
                                    source.path.name, content, folder="point_map_sources"
                                )
                                source_attachment = Attachment(
                                    attachment_type="point_map_source_pdf",
                                    original_filename=stored_source.original_filename,
                                    storage_path=stored_source.storage_path,
                                    content_type="application/pdf",
                                    file_size=stored_source.file_size,
                                    sha256=stored_source.sha256,
                                    uploaded_by=uploaded_by,
                                )
                                db.add(source_attachment)
                                db.flush()
                            rendered = render_pdf_page_png(content, page_number, auto_crop=True)
                            image_name = f"{source.path.stem}-page-{page_number}.png"
                            stored_image = storage.save_bytes(image_name, rendered, folder="point_maps")
                            image_attachment = Attachment(
                                attachment_type="point_map_image",
                                original_filename=stored_image.original_filename,
                                storage_path=stored_image.storage_path,
                                content_type="image/png",
                                file_size=stored_image.file_size,
                                sha256=stored_image.sha256,
                                uploaded_by=uploaded_by,
                            )
                            db.add(image_attachment)
                            db.flush()
                            metadata = infer_point_map_metadata(
                                source.path, source.sha256, page_number, source.page_count
                            )
                            db.add(PointMap(
                                **metadata,
                                status="published",
                                image_attachment_id=image_attachment.id,
                                source_attachment_id=source_attachment.id,
                                source_page=page_number,
                                source_reference=str(source.path),
                                access_level="restricted",
                                created_by=uploaded_by,
                            ))
                            db.commit()
                            imported += 1
                        except Exception:
                            db.rollback()
                            failed += 1
                            logger.exception("导入点位图失败：%s 第 %s 页", source.path, page_number)
                            source_attachment = db.scalar(
                                select(Attachment)
                                .where(
                                    Attachment.attachment_type == "point_map_source_pdf",
                                    Attachment.sha256 == source.sha256,
                                )
                                .order_by(Attachment.id)
                            )
                    task = db.get(TaskRecord, task_id)
                    if not task:
                        return
                    task.progress = min(99, max(3, int(processed * 96 / total_pages)))
                    task.message = f"正在处理 {processed}/{total_pages}：{source.path.name} · 第 {page_number} 页"
                    db.commit()

            task = db.get(TaskRecord, task_id)
            if not task:
                return
            task.status, task.progress, task.finished_at = "completed", 100, datetime.now(timezone.utc)
            task.message = f"导入完成：新增 {imported} 张，跳过重复 {skipped} 张，失败 {failed} 张"
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("点位图资料库导入失败")
            task = db.get(TaskRecord, task_id)
            if task:
                task.status, task.progress, task.finished_at = "failed", 100, datetime.now(timezone.utc)
                task.message = str(exc)
                db.commit()
