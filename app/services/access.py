from __future__ import annotations

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models.entities import (
    ProcessingGroup,
    ProcessingGroupMember,
    Quote,
    RepairOrder,
    ServiceTicket,
    ServiceTicketCollaborator,
    User,
)


def is_admin(user: User) -> bool:
    return user.role == "admin"


def can_view_all_work_orders(user: User) -> bool:
    """Roles that can see every repair/service ticket without gaining admin powers."""
    return user.role in {"admin", "call_operator"}


def _ticket_owner_for_order():
    return (
        select(ServiceTicket.current_owner_id)
        .where(
            ServiceTicket.repair_order_id == RepairOrder.id,
            ServiceTicket.deleted_at.is_(None),
        )
        .limit(1)
        .correlate(RepairOrder)
        .scalar_subquery()
    )


def _order_engineer_for_ticket():
    return (
        select(RepairOrder.engineer_id)
        .where(
            RepairOrder.id == ServiceTicket.repair_order_id,
            RepairOrder.deleted_at.is_(None),
        )
        .limit(1)
        .correlate(ServiceTicket)
        .scalar_subquery()
    )


def scope_orders(stmt, user: User):
    stmt = stmt.where(RepairOrder.deleted_at.is_(None))
    if can_view_all_work_orders(user):
        return stmt

    active_ticket_exists = exists(
        select(ServiceTicket.id).where(
            ServiceTicket.repair_order_id == RepairOrder.id,
            ServiceTicket.deleted_at.is_(None),
        )
    )
    accessible_ticket_orders = scope_service_tickets(
        select(ServiceTicket.repair_order_id).where(ServiceTicket.repair_order_id.is_not(None)),
        user,
    )
    group_member = exists(
        select(ProcessingGroupMember.id)
        .join(ProcessingGroup, ProcessingGroup.id == ProcessingGroupMember.group_id)
        .where(
            ProcessingGroupMember.group_id == RepairOrder.processing_group_id,
            ProcessingGroupMember.user_id == user.id,
            ProcessingGroup.enabled.is_(True),
        )
    )
    return stmt.where(or_(
        RepairOrder.id.in_(accessible_ticket_orders),
        and_(~active_ticket_exists, or_(
            RepairOrder.engineer_id == user.id,
            group_member,
            and_(
                RepairOrder.engineer_id.is_(None),
                RepairOrder.processing_group_id.is_(None),
            ),
        )),
    ))


def scope_service_tickets(stmt, user: User):
    stmt = stmt.where(ServiceTicket.deleted_at.is_(None))
    if can_view_all_work_orders(user):
        return stmt
    order_engineer = _order_engineer_for_ticket()
    active_order_exists = exists(
        select(RepairOrder.id).where(
            RepairOrder.id == ServiceTicket.repair_order_id,
            RepairOrder.deleted_at.is_(None),
        )
    )
    collaborator = exists(
        select(ServiceTicketCollaborator.id).where(
            ServiceTicketCollaborator.ticket_id == ServiceTicket.id,
            ServiceTicketCollaborator.user_id == user.id,
        )
    )
    group_member = exists(
        select(ProcessingGroupMember.id)
        .join(ProcessingGroup, ProcessingGroup.id == ProcessingGroupMember.group_id)
        .where(
            ProcessingGroupMember.group_id == ServiceTicket.processing_group_id,
            ProcessingGroupMember.user_id == user.id,
            ProcessingGroup.enabled.is_(True),
        )
    )
    return stmt.where(or_(
        ServiceTicket.current_owner_id == user.id,
        collaborator,
        group_member,
        and_(
            ServiceTicket.current_owner_id.is_(None),
            ServiceTicket.processing_group_id.is_(None),
            or_(
                ServiceTicket.repair_order_id.is_(None),
                order_engineer == user.id,
                and_(active_order_exists, order_engineer.is_(None)),
            ),
        ),
    ))


def can_access_order(db: Session, order: RepairOrder, user: User) -> bool:
    return db.scalar(
        scope_orders(select(RepairOrder.id), user).where(RepairOrder.id == order.id)
    ) is not None


def can_access_service_ticket(db: Session, ticket: ServiceTicket, user: User) -> bool:
    return db.scalar(
        scope_service_tickets(select(ServiceTicket.id), user).where(ServiceTicket.id == ticket.id)
    ) is not None


def require_order_access(db: Session, order: RepairOrder, user: User) -> RepairOrder:
    if order.deleted_at is not None:
        raise BusinessError("工单不存在", code="order_not_found", status_code=404)
    if not can_access_order(db, order, user):
        raise BusinessError("无权访问该维修工单", code="order_access_denied", status_code=403)
    return order


def require_service_ticket_access(db: Session, ticket: ServiceTicket, user: User) -> ServiceTicket:
    if ticket.deleted_at is not None:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    if not can_access_service_ticket(db, ticket, user):
        raise BusinessError("无权访问该服务工单", code="ticket_access_denied", status_code=403)
    return ticket


def require_quote_access(db: Session, quote: Quote, user: User) -> Quote:
    if quote.deleted_at is not None:
        raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
    if quote.repair_order_id:
        order = quote.repair_order or db.get(RepairOrder, quote.repair_order_id)
        if not order:
            raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
        require_order_access(db, order, user)
    elif quote.service_ticket_id:
        ticket = quote.service_ticket or db.get(ServiceTicket, quote.service_ticket_id)
        if not ticket:
            raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
        require_service_ticket_access(db, ticket, user)
    else:
        raise BusinessError("报价未关联业务工单", code="quote_target_missing", status_code=409)
    return quote


def scope_quotes(stmt, user: User):
    stmt = stmt.where(Quote.deleted_at.is_(None))
    if is_admin(user):
        return stmt
    accessible_orders = scope_orders(select(RepairOrder.id), user)
    accessible_tickets = scope_service_tickets(select(ServiceTicket.id), user)
    return stmt.where(or_(
        Quote.repair_order_id.in_(accessible_orders),
        Quote.service_ticket_id.in_(accessible_tickets),
    ))
