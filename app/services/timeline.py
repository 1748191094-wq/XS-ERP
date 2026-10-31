from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.entities import ServiceTicket, ServiceTicketTimeline


def _order_timeline(db: Session, order_id: int, event_type: str, summary: str, actor_id: int | None, details: dict | None = None) -> None:
    ticket = db.scalar(select(ServiceTicket).where(ServiceTicket.repair_order_id == order_id))
    if ticket:
        db.add(ServiceTicketTimeline(
            ticket_id=ticket.id,
            event_type=event_type,
            summary=summary[:300],
            actor_id=actor_id,
            details_json=details,
        ))

