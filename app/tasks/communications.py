from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from app.core.database import SessionLocal
from app.integrations.email.service import MessagePayload, get_email_service
from app.models.entities import OutboundEmail, ServiceTicketTimeline, TaskRecord, utcnow


logger = logging.getLogger(__name__)


def _record_timeline(db, delivery: OutboundEmail, event_type: str, summary: str) -> None:
    if delivery.service_ticket_id:
        db.add(ServiceTicketTimeline(
            ticket_id=delivery.service_ticket_id,
            event_type=event_type,
            summary=summary[:300],
            actor_id=delivery.created_by,
            details_json={"email_id": delivery.id, "email_no": delivery.email_no},
        ))


def send_outbound_email_task(email_id: int) -> None:
    with SessionLocal() as db:
        delivery = db.get(OutboundEmail, email_id)
        if not delivery:
            return
        task = db.get(TaskRecord, delivery.task_record_id) if delivery.task_record_id else None
        try:
            delivery.status = "sending"
            delivery.attempts += 1
            delivery.last_attempt_at = utcnow()
            delivery.next_retry_at = None
            if task:
                task.status, task.progress, task.started_at, task.finished_at = "running", 20, utcnow(), None
            db.commit()

            snapshots = delivery.attachment_snapshot_json or []
            result = get_email_service(db).send_message(MessagePayload(
                recipients=[delivery.recipient],
                cc=list(delivery.cc_json or []),
                bcc=list(delivery.bcc_json or []),
                subject=delivery.subject_snapshot,
                body_text=delivery.body_snapshot,
                attachments=[Path(item["snapshot_path"]) for item in snapshots],
                template_type=delivery.template_type,
            ))
            delivery.provider = result.provider
            if result.success:
                delivery.status, delivery.sent_at, delivery.error_message = "sent", utcnow(), None
                if task:
                    task.status, task.progress, task.finished_at, task.message = "completed", 100, utcnow(), result.message
                _record_timeline(db, delivery, "email_sent", f"外发邮件 {delivery.email_no} 已发送")
            else:
                delivery.error_message = result.message
                delivery.status = "retry_wait" if delivery.attempts < delivery.max_attempts else "failed"
                if delivery.status == "retry_wait":
                    delivery.next_retry_at = utcnow() + timedelta(minutes=min(30, 2 ** delivery.attempts))
                if task:
                    task.status, task.progress, task.finished_at, task.message = delivery.status, 100, utcnow(), result.message
                _record_timeline(db, delivery, "email_failed", f"外发邮件 {delivery.email_no} 发送失败，已保留快照")
            db.commit()
        except Exception as exc:
            logger.exception("外发邮件任务失败")
            delivery.error_message = str(exc)
            delivery.status = "retry_wait" if delivery.attempts < delivery.max_attempts else "failed"
            if delivery.status == "retry_wait":
                delivery.next_retry_at = utcnow() + timedelta(minutes=min(30, 2 ** max(1, delivery.attempts)))
            if task:
                task.status, task.progress, task.finished_at, task.message = delivery.status, 100, utcnow(), str(exc)
            _record_timeline(db, delivery, "email_failed", f"外发邮件 {delivery.email_no} 发送异常，已保留快照")
            db.commit()
