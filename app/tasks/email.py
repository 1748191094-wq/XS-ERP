from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.integrations.email.service import EmailPayload, get_email_service
from app.models.entities import EmailDelivery, Quote, RepairOrder, TaskRecord, utcnow

logger = logging.getLogger(__name__)


def send_quote_email_task(delivery_id: int) -> None:
    with SessionLocal() as db:
        delivery = db.get(EmailDelivery, delivery_id)
        if not delivery:
            return
        task = db.get(TaskRecord, delivery.task_record_id) if delivery.task_record_id else None
        try:
            delivery.status = "sending"
            if task:
                task.status, task.progress, task.started_at = "running", 20, utcnow()
            db.commit()
            quote = db.scalar(
                select(Quote).where(Quote.id == delivery.quote_id).options(
                    selectinload(Quote.repair_order).selectinload(RepairOrder.customer),
                    selectinload(Quote.repair_order).selectinload(RepairOrder.device),
                )
            )
            if not quote:
                raise RuntimeError("报价记录不存在")
            order, device = quote.repair_order, quote.repair_order.device
            device_name = device.model if device.model.lower().startswith(device.brand.lower()) else f"{device.brand} {device.model}"
            result = get_email_service(db).send_quote(EmailPayload(
                recipient=delivery.recipient, subject=delivery.subject, customer_name=order.customer.name,
                order_no=order.order_no, quote_no=quote.quote_no, device_name=device_name,
                serial_number=device.serial_number, total_amount=f"{quote.total_amount:.2f}",
                attachment_path=Path(delivery.attachment_path), message=delivery.message,
            ))
            delivery.provider = result.provider
            if result.success:
                delivery.status, delivery.sent_at, delivery.error_message = "sent", utcnow(), None
                if task:
                    task.status, task.progress, task.finished_at, task.message = "completed", 100, utcnow(), result.message
            else:
                delivery.status, delivery.error_message = "failed", result.message
                if task:
                    task.status, task.progress, task.finished_at, task.message = "failed", 100, utcnow(), result.message
            db.commit()
        except Exception as exc:
            logger.exception("报价邮件任务失败")
            delivery.status, delivery.error_message = "failed", str(exc)
            if task:
                task.status, task.progress, task.finished_at, task.message = "failed", 100, utcnow(), str(exc)
            db.commit()
