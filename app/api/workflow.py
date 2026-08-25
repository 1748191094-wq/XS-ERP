from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import ok
from app.core.auth import finance_access, inventory_access, require_authenticated_user, require_roles
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.models.entities import (
    Customer,
    CustomerNoteRevision,
    DamageAssessment,
    DroneDevice,
    FinanceTransaction,
    InventoryItem,
    ProcessingGroup,
    ProcessingGroupMember,
    RepairOrder,
    ServiceTicket,
    ServiceTicketCollaborator,
    User,
    WorkOrderGroup,
    WorkOrderGroupMember,
)
from app.services.access import require_order_access, scope_orders, scope_service_tickets
from app.services.orders import RepairOrderService
from app.services.trash import delete_resource


router = APIRouter(prefix="/api", dependencies=[Depends(require_authenticated_user)])


class ServiceGroupMembershipUpdate(BaseModel):
    group_ids: list[int] = Field(default_factory=list)


class RepairOrderServiceGroupUpdate(BaseModel):
    processing_group_id: int | None = None


class WorkOrderGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    order_ids: list[int] = Field(min_length=2)

    @model_validator(mode="after")
    def unique_orders(self):
        self.order_ids = list(dict.fromkeys(self.order_ids))
        if len(self.order_ids) < 2:
            raise ValueError("工单组至少需要两个不同工单")
        return self


class CustomerNoteUpdate(BaseModel):
    content: str = Field(default="", max_length=20000)
    service_group_id: int | None = None


def _commit(db: Session, message: str = "数据存在重复或关联冲突") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(message, code="integrity_conflict", status_code=409) from exc


def _service_group_ids(db: Session, user_id: int) -> list[int]:
    return list(db.scalars(
        select(ProcessingGroupMember.group_id)
        .join(ProcessingGroup, ProcessingGroup.id == ProcessingGroupMember.group_id)
        .where(
            ProcessingGroupMember.user_id == user_id,
            ProcessingGroup.group_type == "service",
            ProcessingGroup.enabled.is_(True),
        )
    ))


def _serialize_group(db: Session, group: WorkOrderGroup, current_user: User) -> dict:
    visible_order_ids = set(db.scalars(scope_orders(select(RepairOrder.id), current_user)))
    members = list(db.scalars(
        select(WorkOrderGroupMember)
        .where(WorkOrderGroupMember.group_id == group.id)
        .options(selectinload(WorkOrderGroupMember.repair_order))
        .order_by(WorkOrderGroupMember.added_at, WorkOrderGroupMember.id)
    ))
    return {
        "id": group.id,
        "name": group.name,
        "created_by": group.created_by,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "orders": [
            {
                "id": member.repair_order.id,
                "order_no": member.repair_order.order_no,
                "customer_id": member.repair_order.customer_id,
                "device_id": member.repair_order.device_id,
                "fault_description": member.repair_order.fault_description,
                "status": member.repair_order.status,
                "priority": member.repair_order.priority,
            }
            for member in members
            if member.repair_order.deleted_at is None and member.repair_order.id in visible_order_ids
        ],
    }


@router.get("/service-groups")
def service_groups(db: Session = Depends(get_db)) -> dict:
    groups = list(db.scalars(
        select(ProcessingGroup).where(
            ProcessingGroup.group_type == "service",
            ProcessingGroup.enabled.is_(True),
        ).order_by(ProcessingGroup.name)
    ))
    member_rows = list(db.scalars(select(ProcessingGroupMember)))
    by_group: dict[int, list[int]] = {group.id: [] for group in groups}
    for row in member_rows:
        if row.group_id in by_group:
            by_group[row.group_id].append(row.user_id)
    return ok([
        {"id": group.id, "name": group.name, "description": group.description, "member_ids": by_group[group.id]}
        for group in groups
    ])


@router.patch("/users/{user_id}/service-groups")
def update_user_service_groups(
    user_id: int,
    payload: ServiceGroupMembershipUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "manager")),
) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise BusinessError("账号不存在", code="user_not_found", status_code=404)
    group_ids = list(dict.fromkeys(payload.group_ids))
    groups = list(db.scalars(select(ProcessingGroup).where(ProcessingGroup.id.in_(group_ids or [-1]))))
    if len(groups) != len(group_ids) or any(group.group_type != "service" or not group.enabled for group in groups):
        raise BusinessError("包含不存在或不可用的服务组", code="service_group_not_found", status_code=404)
    service_group_ids = select(ProcessingGroup.id).where(ProcessingGroup.group_type == "service")
    existing = list(db.scalars(select(ProcessingGroupMember).where(
        ProcessingGroupMember.user_id == user_id,
        ProcessingGroupMember.group_id.in_(service_group_ids),
    )))
    for row in existing:
        db.delete(row)
    for group_id in group_ids:
        db.add(ProcessingGroupMember(group_id=group_id, user_id=user_id, added_by=current_user.id))
    _commit(db, "人员服务组关系重复")
    return ok({"user_id": user_id, "group_ids": group_ids})


@router.patch("/orders/{order_id}/service-group")
def update_order_service_group(
    order_id: int,
    payload: RepairOrderServiceGroupUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "manager", "engineer", "technical_support")),
) -> dict:
    order = db.get(RepairOrder, order_id)
    if not order:
        raise BusinessError("工单不存在", code="order_not_found", status_code=404)
    require_order_access(db, order, current_user)
    if payload.processing_group_id:
        group = db.get(ProcessingGroup, payload.processing_group_id)
        if not group or group.group_type != "service" or not group.enabled:
            raise BusinessError("服务组不存在或不可用", code="service_group_not_found", status_code=404)
    order.processing_group_id = payload.processing_group_id
    ticket = db.scalar(select(ServiceTicket).where(ServiceTicket.repair_order_id == order.id))
    if ticket:
        ticket.processing_group_id = payload.processing_group_id
    _commit(db)
    return ok({"order_id": order.id, "processing_group_id": order.processing_group_id})


@router.get("/staff/search")
def search_staff(
    employee_no: str = Query(min_length=1, max_length=24),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    user = db.scalar(select(User).where(func.lower(User.employee_no) == employee_no.strip().lower()))
    if not user:
        return ok(None)
    owned_order_ids = select(ServiceTicket.repair_order_id).where(ServiceTicket.current_owner_id == user.id)
    collaborated_ticket_ids = select(ServiceTicketCollaborator.ticket_id).where(
        ServiceTicketCollaborator.user_id == user.id
    )
    collaborated_order_ids = select(ServiceTicket.repair_order_id).where(
        ServiceTicket.id.in_(collaborated_ticket_ids)
    )
    stmt = scope_orders(select(RepairOrder), current_user).where(or_(
        RepairOrder.engineer_id == user.id,
        RepairOrder.id.in_(owned_order_ids),
        RepairOrder.id.in_(collaborated_order_ids),
    )).order_by(RepairOrder.updated_at.desc())
    orders = list(db.scalars(stmt.limit(200)))
    return ok({
        "user": {
            "id": user.id,
            "employee_no": user.employee_no,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "enabled": user.enabled,
            "group_ids": _service_group_ids(db, user.id),
        },
        "orders": [
            {"id": row.id, "order_no": row.order_no, "status": row.status, "fault_description": row.fault_description}
            for row in orders
        ],
    })


@router.get("/work-order-groups")
def list_work_order_groups(
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(WorkOrderGroup).where(WorkOrderGroup.deleted_at.is_(None))
    visible_order_ids = set(db.scalars(scope_orders(select(RepairOrder.id), current_user)))
    if q.strip():
        term = f"%{q.strip()[:100]}%"
        stmt = stmt.join(WorkOrderGroupMember).join(RepairOrder).where(or_(
            WorkOrderGroup.name.like(term),
            RepairOrder.order_no.like(term),
        ), RepairOrder.id.in_(visible_order_ids)).distinct()
    groups = list(db.scalars(stmt.order_by(WorkOrderGroup.updated_at.desc()).limit(200)))
    serialized = [_serialize_group(db, group, current_user) for group in groups]
    return ok([group for group in serialized if group["orders"]])


@router.get("/work-order-groups/{group_id}")
def get_work_order_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    group = db.get(WorkOrderGroup, group_id)
    if not group or group.deleted_at is not None:
        raise BusinessError("工单组不存在", code="work_order_group_not_found", status_code=404)
    serialized = _serialize_group(db, group, current_user)
    if not serialized["orders"]:
        raise BusinessError("工单组不存在或无权查看", code="work_order_group_not_found", status_code=404)
    return ok(serialized)


def _replace_group_members(
    db: Session,
    group: WorkOrderGroup,
    order_ids: list[int],
    actor: User,
) -> None:
    orders = list(db.scalars(select(RepairOrder).where(
        RepairOrder.id.in_(order_ids), RepairOrder.deleted_at.is_(None)
    )))
    if len(orders) != len(order_ids):
        raise BusinessError("包含不存在或已删除的工单", code="order_not_found", status_code=404)
    for order in orders:
        require_order_access(db, order, actor)

    target_ids = set(order_ids)
    current_members = list(db.scalars(select(WorkOrderGroupMember).where(
        WorkOrderGroupMember.group_id == group.id
    )))
    for member in current_members:
        if member.repair_order_id not in target_ids:
            db.delete(member)

    existing_by_order = {
        member.repair_order_id: member
        for member in db.scalars(select(WorkOrderGroupMember).where(
            WorkOrderGroupMember.repair_order_id.in_(target_ids or [-1])
        ))
    }
    for order_id in order_ids:
        member = existing_by_order.get(order_id)
        if member and member.group_id != group.id:
            other_group = db.get(WorkOrderGroup, member.group_id)
            if other_group and other_group.deleted_at is None:
                raise BusinessError("一个工单当前只能属于一个工单组", code="work_order_already_grouped", status_code=409)
            member.group_id = group.id
            member.added_by = actor.id
        elif not member:
            db.add(WorkOrderGroupMember(
                group_id=group.id,
                repair_order_id=order_id,
                added_by=actor.id,
            ))


def _require_group_management(db: Session, group: WorkOrderGroup, user: User) -> None:
    if user.role == "admin":
        return
    members = list(db.scalars(select(WorkOrderGroupMember).where(
        WorkOrderGroupMember.group_id == group.id
    )))
    if not members and group.created_by != user.id:
        raise BusinessError("无权管理该工单组", code="work_order_group_access_denied", status_code=403)
    for member in members:
        order = db.get(RepairOrder, member.repair_order_id)
        if not order or order.deleted_at is not None:
            continue
        require_order_access(db, order, user)


@router.post("/work-order-groups", status_code=201)
def create_work_order_group(
    payload: WorkOrderGroupInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "manager")),
) -> dict:
    group = WorkOrderGroup(name=payload.name.strip(), created_by=current_user.id)
    db.add(group)
    db.flush()
    _replace_group_members(db, group, payload.order_ids, current_user)
    _commit(db, "工单组成员存在冲突")
    db.refresh(group)
    return ok(_serialize_group(db, group, current_user))


@router.patch("/work-order-groups/{group_id}")
def update_work_order_group(
    group_id: int,
    payload: WorkOrderGroupInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "manager")),
) -> dict:
    group = db.get(WorkOrderGroup, group_id)
    if not group or group.deleted_at is not None:
        raise BusinessError("工单组不存在", code="work_order_group_not_found", status_code=404)
    _require_group_management(db, group, current_user)
    group.name = payload.name.strip()
    _replace_group_members(db, group, payload.order_ids, current_user)
    _commit(db, "工单组成员存在冲突")
    db.refresh(group)
    return ok(_serialize_group(db, group, current_user))


@router.delete("/work-order-groups/{group_id}")
def delete_work_order_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "manager")),
) -> dict:
    group = db.get(WorkOrderGroup, group_id)
    if not group or group.deleted_at is not None:
        raise BusinessError("工单组不存在", code="work_order_group_not_found", status_code=404)
    _require_group_management(db, group, current_user)
    record = delete_resource(db, "work_order_group", group_id, user=current_user)
    _commit(db)
    return ok({"deletion_id": record.id, "deleted_at": record.deleted_at})


def _note_payload(db: Session, note: CustomerNoteRevision) -> dict:
    actor = db.get(User, note.changed_by)
    group = db.get(ProcessingGroup, note.service_group_id) if note.service_group_id else None
    return {
        "id": note.id,
        "note_type": note.note_type,
        "service_group_id": note.service_group_id,
        "service_group_name": group.name if group else None,
        "previous_content": note.previous_content,
        "content": note.content,
        "changed_by": note.changed_by,
        "changed_by_name": actor.display_name if actor else f"#{note.changed_by}",
        "changed_at": note.changed_at,
    }


@router.get("/customers/{customer_id}/notes")
def customer_notes(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    customer = db.get(Customer, customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    group_ids = _service_group_ids(db, current_user.id)
    large_history = list(db.scalars(select(CustomerNoteRevision).where(
        CustomerNoteRevision.customer_id == customer_id,
        CustomerNoteRevision.note_type == "large",
    ).order_by(CustomerNoteRevision.changed_at.desc()).limit(100)))
    small_history = list(db.scalars(select(CustomerNoteRevision).where(
        CustomerNoteRevision.customer_id == customer_id,
        CustomerNoteRevision.note_type == "small",
        CustomerNoteRevision.service_group_id.in_(group_ids or [-1]),
    ).order_by(CustomerNoteRevision.changed_at.desc()).limit(200)))
    latest_small: dict[int, CustomerNoteRevision] = {}
    for note in small_history:
        latest_small.setdefault(note.service_group_id, note)
    groups = list(db.scalars(select(ProcessingGroup).where(ProcessingGroup.id.in_(group_ids or [-1]))))
    return ok({
        "large": {"content": customer.notes or "", "history": [_note_payload(db, row) for row in large_history]},
        "small": [
            {
                "service_group_id": group.id,
                "service_group_name": group.name,
                "content": latest_small[group.id].content if group.id in latest_small else "",
                "history": [_note_payload(db, row) for row in small_history if row.service_group_id == group.id],
            }
            for group in groups
        ],
    })


@router.put("/customers/{customer_id}/notes/large")
def update_large_customer_note(
    customer_id: int,
    payload: CustomerNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    customer = db.get(Customer, customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    content = payload.content.strip()
    revision = CustomerNoteRevision(
        customer_id=customer.id,
        note_type="large",
        service_group_id=None,
        previous_content=customer.notes,
        content=content,
        changed_by=current_user.id,
    )
    customer.notes = content or None
    db.add(revision)
    _commit(db)
    db.refresh(revision)
    return ok(_note_payload(db, revision))


@router.put("/customers/{customer_id}/notes/small")
def update_small_customer_note(
    customer_id: int,
    payload: CustomerNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    customer = db.get(Customer, customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    group_ids = _service_group_ids(db, current_user.id)
    if payload.service_group_id not in group_ids:
        raise BusinessError("只能查看和编辑本人所属服务组的小备注", code="small_note_group_denied", status_code=403)
    previous = db.scalar(select(CustomerNoteRevision).where(
        CustomerNoteRevision.customer_id == customer_id,
        CustomerNoteRevision.note_type == "small",
        CustomerNoteRevision.service_group_id == payload.service_group_id,
    ).order_by(CustomerNoteRevision.changed_at.desc()).limit(1))
    revision = CustomerNoteRevision(
        customer_id=customer_id,
        note_type="small",
        service_group_id=payload.service_group_id,
        previous_content=previous.content if previous else None,
        content=payload.content.strip(),
        changed_by=current_user.id,
    )
    db.add(revision)
    _commit(db)
    db.refresh(revision)
    return ok(_note_payload(db, revision))


@router.get("/call-quick/match")
def call_quick_match(
    phone: str = Query(min_length=1, max_length=32),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    query = phone.strip()
    exact = list(db.scalars(select(Customer).where(
        Customer.deleted_at.is_(None), Customer.phone == query,
    ).order_by(Customer.name)))
    fuzzy = list(db.scalars(select(Customer).where(
        Customer.deleted_at.is_(None), Customer.phone.like(f"%{query}%"),
    ).order_by(Customer.name).limit(50)))
    customers = list(dict.fromkeys([*exact, *fuzzy]))
    group_ids = _service_group_ids(db, current_user.id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    results = []
    for customer in customers:
        records = list(db.scalars(scope_service_tickets(select(ServiceTicket), current_user).where(
            ServiceTicket.customer_id == customer.id,
            ServiceTicket.updated_at >= cutoff,
        ).order_by(ServiceTicket.updated_at.desc()).limit(30)))
        recent_services = []
        for row in records:
            repair_order = db.get(RepairOrder, row.repair_order_id) if row.repair_order_id else None
            device_id = row.device_id or (repair_order.device_id if repair_order else None)
            device = db.get(DroneDevice, device_id) if device_id else None
            recent_services.append({
                "id": row.id,
                "ticket_no": row.ticket_no,
                "title": row.title,
                "status": row.status,
                "updated_at": row.updated_at,
                "repair_order_id": row.repair_order_id,
                "repair_order_no": repair_order.order_no if repair_order else None,
                "serial_number": device.serial_number if device else None,
                "device_model": device.model if device else None,
            })
        results.append({
            "customer": {
                "id": customer.id,
                "customer_no": customer.customer_no,
                "name": customer.name,
                "phone": customer.phone,
                "email": customer.email,
            },
            "match_type": "exact" if customer.phone == query else "fuzzy",
            "recent_services": recent_services,
        })
    return ok({"query": query, "service_group_ids": group_ids, "matches": results})


@router.get("/exports/orders.csv")
def export_orders_csv(
    q: str = "",
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> Response:
    stmt = scope_orders(select(RepairOrder), current_user).order_by(RepairOrder.created_at.desc())
    if status:
        stmt = stmt.where(RepairOrder.status == status)
    if q.strip():
        term = f"%{q.strip()[:100]}%"
        customer_ids = select(Customer.id).where(or_(
            Customer.name.like(term), Customer.phone.like(term), Customer.email.like(term)
        ))
        stmt = stmt.where(or_(
            RepairOrder.order_no.like(term),
            RepairOrder.fault_description.like(term),
            RepairOrder.customer_id.in_(customer_ids),
        ))
    rows = list(db.scalars(stmt.limit(5000)))
    customer_ids = {row.customer_id for row in rows}
    customers = {row.id: row for row in db.scalars(select(Customer).where(Customer.id.in_(customer_ids or [-1])))}
    tickets = {
        row.repair_order_id: row
        for row in db.scalars(select(ServiceTicket).where(
            ServiceTicket.repair_order_id.in_([item.id for item in rows] or [-1]),
            ServiceTicket.deleted_at.is_(None),
        ))
    }
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["工单号", "客户", "电话", "邮件", "服务主题"])
    for row in rows:
        customer = customers.get(row.customer_id)
        ticket = tickets.get(row.id)
        writer.writerow([
            row.order_no,
            customer.name if customer else "",
            customer.phone if customer else "",
            customer.email if customer else "",
            ticket.title if ticket else row.fault_description,
        ])
    content = "\ufeff" + stream.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="work-orders.csv"'},
    )


@router.delete("/damage-assessments/{assessment_id}")
def delete_damage_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "manager", "engineer", "technical_support")),
) -> dict:
    assessment = db.get(DamageAssessment, assessment_id)
    if not assessment:
        raise BusinessError("定损结果不存在", code="damage_assessment_not_found", status_code=404)
    order = db.get(RepairOrder, assessment.repair_order_id)
    require_order_access(db, order, current_user)
    record = delete_resource(db, "damage_assessment", assessment_id, user=current_user)
    _commit(db)
    return ok({"deletion_id": record.id, "deleted_at": record.deleted_at})


@router.delete("/inventory/items/{item_id}")
def delete_inventory_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(inventory_access),
) -> dict:
    record = delete_resource(db, "inventory_item", item_id, user=current_user)
    _commit(db)
    return ok({"deletion_id": record.id, "deleted_at": record.deleted_at})


@router.delete("/finance/{transaction_id}")
def delete_finance_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(finance_access),
) -> dict:
    transaction = db.get(FinanceTransaction, transaction_id)
    if not transaction:
        raise BusinessError("财务流水不存在", code="finance_not_found", status_code=404)
    order_id = transaction.repair_order_id
    record = delete_resource(db, "finance_transaction", transaction_id, user=current_user)
    if order_id:
        order = db.get(RepairOrder, order_id)
        if order:
            RepairOrderService.recalculate_finance(db, order)
    _commit(db)
    return ok({"deletion_id": record.id, "deleted_at": record.deleted_at})
