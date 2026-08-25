from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import EmailDelivery, Quote, TaskRecord
from app.services.numbering import make_no


def queue_quote_email(db: Session, quote: Quote, *, recipient: str, subject: str, message: str | None, attachment_path: str) -> tuple[EmailDelivery, TaskRecord]:
    task = TaskRecord(task_no=make_no("TASK"), task_type="quote_email", related_type="quote", related_id=quote.id, status="queued", progress=0)
    db.add(task)
    db.flush()
    delivery = EmailDelivery(
        delivery_no=make_no("MAIL"), quote_id=quote.id, repair_order_id=quote.repair_order_id,
        task_record_id=task.id, recipient=recipient, subject=subject, message=message,
        provider="pending", status="queued", attachment_path=attachment_path,
    )
    db.add(delivery)
    db.flush()
    return delivery, task
