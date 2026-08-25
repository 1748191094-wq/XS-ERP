from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import ok
from app.core.auth import admin_access, finance_access, inventory_access, require_authenticated_user
from app.core.config import settings
from app.core.database import get_db
from app.core.enums import RepairOrderStatus
from app.core.exceptions import BusinessError
from app.models.entities import (
    Customer,
    FinanceTransaction,
    FollowUpTask,
    InventoryItem,
    InventoryTransaction,
    OutboundCall,
    OutboundEmail,
    PurchaseOrder,
    Quote,
    RepairOrder,
    RepairOrderStatusHistory,
    ServiceTicket,
    ServiceTicketNote,
    ServiceTicketTimeline,
    Shipment,
    ShipmentEvent,
    SpecialistEscalation,
    Stocktake,
    Supplier,
    SystemSetting,
    TaskRecord,
    User,
)
from app.services.access import (
    can_access_order,
    can_access_service_ticket,
    is_admin,
    require_order_access,
    require_service_ticket_access,
    scope_orders,
    scope_service_tickets,
)
from app.services.email_config import load_email_config
from app.services.backup_schedule import TASK_NAME, sync_windows_backup_task
from app.schemas.domain import FinanceCreate, InventoryTransactionRead, TicketAssignmentUpdate, TicketStatusChange
from app.core.inventory_quantity import inventory_quantity_int
from app.services.finance import FinanceService
from app.services.orders import RepairOrderService
from app.services.tickets import TicketService
from app.services.procurement import (
    commit_stocktake,
    create_purchase_order,
    create_stocktake,
    load_purchase_order,
    receive_purchase_order,
    return_purchase_item,
    serialize_purchase_order,
    serialize_supplier,
)


router = APIRouter(prefix="/api", dependencies=[Depends(require_authenticated_user)])


FINAL_ORDER_STATUSES = {"completed", "cancelled"}
FINAL_TICKET_STATUSES = {"resolved", "closed", "cancelled"}
TICKET_STATUSES = {"open", "assigned", "in_progress", "waiting_customer", "waiting_internal", "resolved", "closed", "cancelled"}
ORDER_STATUSES = {status.value for status in RepairOrderStatus}


class WorkBulkItem(BaseModel):
    kind: str
    id: int


class WorkBulkUpdate(BaseModel):
    items: list[WorkBulkItem] = Field(min_length=1, max_length=100)
    action: str
    status: str | None = None
    owner_id: int | None = None
    due_at: datetime | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self):
        if self.action not in {"assign", "status", "due"}:
            raise ValueError("不支持的批量操作")
        if self.action == "status":
            if not self.status:
                raise ValueError("批量更新状态时必须选择新状态")
            if not (self.reason or "").strip():
                raise ValueError("批量更新状态时必须填写原因")
        if self.action == "due" and self.due_at is None:
            raise ValueError("批量设置时限时必须填写时间")
        return self


class SupplierInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    contact: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class PurchaseLineInput(BaseModel):
    inventory_item_id: int
    quantity: int = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)
    remarks: str | None = None


class PurchaseOrderInput(BaseModel):
    supplier_id: int
    expected_at: datetime | None = None
    notes: str | None = None
    items: list[PurchaseLineInput] = Field(min_length=1, max_length=200)


class ReceiptLineInput(BaseModel):
    purchase_order_item_id: int
    quantity: int = Field(gt=0)
    lot_no: str | None = Field(default=None, max_length=64)
    serial_numbers: list[str] = Field(default_factory=list, max_length=500)


class PurchaseReceiptInput(BaseModel):
    lines: list[ReceiptLineInput] = Field(min_length=1, max_length=200)


class PurchaseReturnInput(BaseModel):
    purchase_order_item_id: int
    quantity: int = Field(gt=0)
    remarks: str | None = None


class PurchasePaymentInput(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: str | None = Field(default=None, max_length=50)
    paid_at: datetime | None = None
    description: str | None = None


class StocktakeLineInput(BaseModel):
    inventory_item_id: int
    counted_quantity: int = Field(ge=0)
    remarks: str | None = None


class StocktakeInput(BaseModel):
    notes: str | None = None
    items: list[StocktakeLineInput] = Field(min_length=1, max_length=2000)


class BackupScheduleInput(BaseModel):
    enabled: bool = True
    time: str = Field(default="02:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    retention_days: int = Field(default=30, ge=1, le=3650)
    keep_count: int = Field(default=30, ge=1, le=1000)
    offsite_dir: str | None = Field(default=None, max_length=600)


def _commit(db: Session, message: str = "数据存在重复或关联冲突") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(message, code="data_conflict", status_code=409) from exc


def _work_item(kind: str, record_id: int, number: str, title: str, status: str, priority: str = "normal", owner_id: int | None = None, due_at: datetime | None = None, category: str = "待处理", customer_id: int | None = None) -> dict:
    now = datetime.now(timezone.utc)
    overdue = bool(due_at and due_at < now)
    return {
        "key": f"{kind}:{record_id}",
        "kind": kind,
        "id": record_id,
        "number": number,
        "title": title,
        "status": status,
        "priority": priority,
        "owner_id": owner_id,
        "due_at": due_at,
        "overdue": overdue,
        "category": category,
        "customer_id": customer_id,
    }


@router.get("/work-center")
def work_center(
    view: str = Query(default="all", pattern="^(all|mine|unassigned|overdue)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    items: list[dict] = []
    orders = list(db.scalars(scope_orders(
        select(RepairOrder).where(RepairOrder.status.not_in(FINAL_ORDER_STATUSES)), current_user
    ).order_by(RepairOrder.expected_finish_at, RepairOrder.updated_at.desc()).limit(500)))
    order_ids = [row.id for row in orders]
    for order in orders:
        items.append(_work_item(
            "repair_order", order.id, order.order_no, order.fault_description[:180], order.status,
            order.priority, order.engineer_id, order.expected_finish_at, "维修流程", order.customer_id,
        ))

    tickets = list(db.scalars(scope_service_tickets(
        select(ServiceTicket).where(ServiceTicket.status.not_in(FINAL_TICKET_STATUSES)), current_user
    ).order_by(ServiceTicket.due_at, ServiceTicket.updated_at.desc()).limit(500)))
    ticket_ids = [row.id for row in tickets]
    for ticket in tickets:
        items.append(_work_item(
            "service_ticket", ticket.id, ticket.ticket_no, ticket.title, ticket.status,
            ticket.priority, ticket.current_owner_id, ticket.due_at, "服务工单", ticket.customer_id,
        ))

    call_stmt = select(OutboundCall).where(OutboundCall.status.in_(["planned", "callback"]))
    if not is_admin(current_user):
        call_stmt = call_stmt.where(or_(OutboundCall.assigned_to == current_user.id, OutboundCall.assigned_to.is_(None)))
        call_stmt = call_stmt.where(or_(
            OutboundCall.repair_order_id.in_(order_ids or [-1]),
            OutboundCall.service_ticket_id.in_(ticket_ids or [-1]),
            (OutboundCall.repair_order_id.is_(None) & OutboundCall.service_ticket_id.is_(None)),
        ))
    for call in db.scalars(call_stmt.order_by(OutboundCall.planned_at).limit(300)):
        items.append(_work_item("outbound_call", call.id, call.call_no, call.purpose, call.status, "normal", call.assigned_to, call.planned_at, "客户联系", call.customer_id))

    followup_stmt = select(FollowUpTask).where(
        FollowUpTask.status == "pending",
        FollowUpTask.deleted_at.is_(None),
    )
    if not is_admin(current_user):
        followup_stmt = followup_stmt.where(FollowUpTask.repair_order_id.in_(order_ids or [-1]))
    for task in db.scalars(followup_stmt.order_by(FollowUpTask.scheduled_at, FollowUpTask.id).limit(300)):
        items.append(_work_item("followup", task.id, f"回访 #{task.id}", task.content or task.follow_up_type, task.status, "normal", None, task.scheduled_at, "客户回访", task.customer_id))

    shipment_stmt = select(Shipment).where(Shipment.logistics_status.in_(["pending_submit", "exception"]))
    if not is_admin(current_user):
        shipment_stmt = shipment_stmt.where(Shipment.repair_order_id.in_(order_ids or [-1]))
    for shipment in db.scalars(shipment_stmt.order_by(Shipment.updated_at.desc()).limit(200)):
        items.append(_work_item("shipment", shipment.id, shipment.tracking_no or f"物流 #{shipment.id}", "物流待提交" if shipment.logistics_status == "pending_submit" else "物流异常待处理", shipment.logistics_status, "high" if shipment.logistics_status == "exception" else "normal", None, None, "物流异常"))

    email_stmt = select(OutboundEmail).where(OutboundEmail.status.in_(["failed", "retry_wait"]))
    if not is_admin(current_user):
        email_stmt = email_stmt.where(or_(
            OutboundEmail.repair_order_id.in_(order_ids or [-1]),
            OutboundEmail.service_ticket_id.in_(ticket_ids or [-1]),
        ))
    for email in db.scalars(email_stmt.order_by(OutboundEmail.updated_at.desc()).limit(200)):
        items.append(_work_item("email", email.id, email.email_no, email.subject_snapshot, email.status, "high", email.created_by, email.next_retry_at, "邮件失败", email.customer_id))

    escalation_stmt = select(SpecialistEscalation).where(SpecialistEscalation.status.not_in(["completed", "cancelled"]))
    if not is_admin(current_user):
        escalation_stmt = escalation_stmt.where(or_(
            SpecialistEscalation.assigned_specialist_id == current_user.id,
            SpecialistEscalation.assigned_specialist_id.is_(None),
        ), SpecialistEscalation.service_ticket_id.in_(ticket_ids or [-1]))
    for escalation in db.scalars(escalation_stmt.order_by(SpecialistEscalation.updated_at.desc()).limit(200)):
        items.append(_work_item("escalation", escalation.id, escalation.escalation_no, escalation.problem_summary[:180], escalation.status, escalation.urgency, escalation.assigned_specialist_id, None, "专员升级"))

    if current_user.role in {"admin", "manager", "warehouse"}:
        for inventory in db.scalars(select(InventoryItem).where(
            InventoryItem.enabled.is_(True),
            InventoryItem.deleted_at.is_(None),
            InventoryItem.stock_quantity <= InventoryItem.safety_stock,
        ).limit(200)):
            items.append(_work_item("inventory", inventory.id, inventory.sku, f"{inventory.name} · 当前 {inventory_quantity_int(inventory.stock_quantity)} {inventory.unit}", "low_stock", "high", None, None, "库存预警"))

    if view == "mine":
        items = [item for item in items if item["owner_id"] == current_user.id]
    elif view == "unassigned":
        items = [item for item in items if item["owner_id"] is None]
    elif view == "overdue":
        items = [item for item in items if item["overdue"]]
    priority_rank = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    items.sort(key=lambda item: (
        not item["overdue"],
        priority_rank.get(item["priority"], 2),
        item["due_at"] or datetime.max.replace(tzinfo=timezone.utc),
        item["kind"],
        item["id"],
    ))
    summary = {
        "total": len(items),
        "mine": sum(item["owner_id"] == current_user.id for item in items),
        "unassigned": sum(item["owner_id"] is None for item in items),
        "overdue": sum(item["overdue"] for item in items),
        "categories": dict(sorted({category: sum(item["category"] == category for item in items) for category in {item["category"] for item in items}}.items())),
    }
    return ok({"summary": summary, "items": items[:1000]})


@router.post("/work-center/bulk")
def bulk_update_work(
    payload: WorkBulkUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    can_reassign = current_user.role in {"admin", "manager"}
    if payload.action == "assign" and not can_reassign and payload.owner_id != current_user.id:
        raise BusinessError("非管理账号只能领取给自己，不能转派或公开工单", code="bulk_assign_denied", status_code=403)
    if payload.action == "assign" and payload.owner_id is not None:
        owner = db.get(User, payload.owner_id)
        if not owner or not owner.enabled:
            raise BusinessError("负责人不存在或已停用", code="owner_not_found", status_code=404)

    unique_refs: list[WorkBulkItem] = []
    seen: set[tuple[str, int]] = set()
    targets: list[tuple[str, RepairOrder | ServiceTicket]] = []
    for ref in payload.items:
        key = (ref.kind, ref.id)
        if key in seen:
            continue
        seen.add(key)
        if ref.kind == "service_ticket":
            ticket = db.get(ServiceTicket, ref.id)
            if not ticket:
                raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
            require_service_ticket_access(db, ticket, current_user)
            if payload.action == "status" and payload.status not in TICKET_STATUSES:
                raise BusinessError("服务工单状态无效", code="ticket_status_invalid")
            targets.append((ref.kind, ticket))
        elif ref.kind == "repair_order":
            order = db.get(RepairOrder, ref.id)
            if not order:
                raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
            require_order_access(db, order, current_user)
            if payload.action == "status" and payload.status not in ORDER_STATUSES:
                raise BusinessError("维修工单状态无效", code="order_status_invalid")
            targets.append((ref.kind, order))
        else:
            raise BusinessError("该类型暂不支持批量修改", code="bulk_kind_invalid")
        unique_refs.append(ref)

    for kind, target in targets:
        if kind == "service_ticket":
            ticket = target
            if payload.action == "assign":
                TicketService.assign(
                    db,
                    ticket,
                    TicketAssignmentUpdate(
                        current_owner_id=payload.owner_id,
                        processing_group_id=ticket.processing_group_id,
                        reason=payload.reason or "待办中心批量分派",
                    ),
                    actor_id=current_user.id,
                )
            elif payload.action == "status":
                TicketService.change_status(
                    db,
                    ticket,
                    TicketStatusChange(status=payload.status, reason=payload.reason),
                    actor_id=current_user.id,
                )
            else:
                ticket.due_at = payload.due_at
                if ticket.repair_order_id:
                    linked_order = db.get(RepairOrder, ticket.repair_order_id)
                    if linked_order and linked_order.deleted_at is None:
                        linked_order.expected_finish_at = payload.due_at
            db.add(ServiceTicketTimeline(
                ticket_id=ticket.id,
                event_type="bulk_updated",
                summary=f"批量操作：{payload.action}",
                actor_id=current_user.id,
                details_json=payload.model_dump(mode="json"),
            ))
        else:
            order = target
            if payload.action == "assign":
                linked_ticket = TicketService.ensure_for_repair_order(db, order, created_by=current_user.id)
                TicketService.assign(
                    db,
                    linked_ticket,
                    TicketAssignmentUpdate(
                        current_owner_id=payload.owner_id,
                        processing_group_id=order.processing_group_id,
                        reason=payload.reason or "待办中心批量分派",
                    ),
                    actor_id=current_user.id,
                )
            elif payload.action == "status":
                RepairOrderService.change_status(
                    db,
                    order,
                    payload.status,
                    changed_by=current_user.id,
                    reason=payload.reason,
                )
            else:
                order.expected_finish_at = payload.due_at
                linked_ticket = TicketService.ensure_for_repair_order(db, order, created_by=current_user.id)
                linked_ticket.due_at = payload.due_at
    _commit(db)
    return ok({"processed": len(unique_refs), "errors": []})


def _timeline_event(event_type: str, title: str, content: str | None, occurred_at: datetime, **extra) -> dict:
    return {"event_type": event_type, "title": title, "content": content, "occurred_at": occurred_at, **extra}


@router.get("/customers/{customer_id}/timeline")
def customer_timeline(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    customer = db.get(Customer, customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    orders = list(db.scalars(scope_orders(select(RepairOrder).where(RepairOrder.customer_id == customer_id), current_user)))
    tickets = list(db.scalars(scope_service_tickets(select(ServiceTicket).where(ServiceTicket.customer_id == customer_id), current_user)))
    order_ids = [row.id for row in orders]
    ticket_ids = [row.id for row in tickets]
    events: list[dict] = []
    for history in db.scalars(select(RepairOrderStatusHistory).where(RepairOrderStatusHistory.repair_order_id.in_(order_ids or [-1]))):
        events.append(_timeline_event("order_status", "维修工单状态更新", history.reason, history.changed_at, repair_order_id=history.repair_order_id, status=history.to_status))
    for event in db.scalars(select(ServiceTicketTimeline).where(ServiceTicketTimeline.ticket_id.in_(ticket_ids or [-1]))):
        events.append(_timeline_event("ticket_timeline", event.summary, None, event.created_at, service_ticket_id=event.ticket_id, status=event.to_status))
    for note in db.scalars(select(ServiceTicketNote).where(ServiceTicketNote.ticket_id.in_(ticket_ids or [-1]))):
        events.append(_timeline_event("ticket_note", "客户可见备注" if note.visibility == "customer" else "内部备注", note.content, note.created_at, service_ticket_id=note.ticket_id, visibility=note.visibility))
    call_stmt = select(OutboundCall).where(OutboundCall.customer_id == customer_id)
    email_stmt = select(OutboundEmail).where(OutboundEmail.customer_id == customer_id)
    if not is_admin(current_user):
        linked_scope = or_(
            OutboundCall.repair_order_id.in_(order_ids or [-1]),
            OutboundCall.service_ticket_id.in_(ticket_ids or [-1]),
        )
        call_stmt = call_stmt.where(linked_scope, or_(OutboundCall.assigned_to == current_user.id, OutboundCall.assigned_to.is_(None)))
        email_stmt = email_stmt.where(or_(
            OutboundEmail.repair_order_id.in_(order_ids or [-1]),
            OutboundEmail.service_ticket_id.in_(ticket_ids or [-1]),
        ))
    for call in db.scalars(call_stmt):
        events.append(_timeline_event("outbound_call", f"外呼：{call.purpose}", call.summary, call.actual_at or call.planned_at or call.created_at, call_id=call.id, status=call.status, result=call.result))
    for email in db.scalars(email_stmt):
        events.append(_timeline_event("outbound_email", f"邮件：{email.subject_snapshot}", email.body_snapshot[:500], email.sent_at or email.created_at, email_id=email.id, status=email.status))
    for quote in db.scalars(select(Quote).where(or_(Quote.repair_order_id.in_(order_ids or [-1]), Quote.service_ticket_id.in_(ticket_ids or [-1])), Quote.deleted_at.is_(None))):
        events.append(_timeline_event("quote", f"报价 {quote.quote_no} · V{quote.version}", f"金额 ¥{quote.total_amount}", quote.created_at, quote_id=quote.id, status=quote.status))
    shipments = list(db.scalars(select(Shipment).where(Shipment.repair_order_id.in_(order_ids or [-1]))))
    shipment_ids = [row.id for row in shipments]
    for event in db.scalars(select(ShipmentEvent).where(ShipmentEvent.shipment_id.in_(shipment_ids or [-1]))):
        events.append(_timeline_event("shipment", f"物流：{event.logistics_status}", event.description, event.occurred_at, shipment_id=event.shipment_id, status=event.logistics_status))
    for task in db.scalars(select(FollowUpTask).where(
        FollowUpTask.customer_id == customer_id,
        FollowUpTask.repair_order_id.in_(order_ids or [-1]),
        FollowUpTask.deleted_at.is_(None),
    )):
        events.append(_timeline_event("followup", "售后回访", task.result or task.content, task.completed_at or task.scheduled_at, followup_id=task.id, status=task.status))
    events.sort(key=lambda event: event["occurred_at"], reverse=True)
    return ok({"customer_id": customer_id, "events": events[:1000]})


@router.get("/suppliers", dependencies=[Depends(inventory_access)])
def list_suppliers(db: Session = Depends(get_db)) -> dict:
    return ok([serialize_supplier(row) for row in db.scalars(select(Supplier).order_by(Supplier.enabled.desc(), Supplier.name))])


@router.post("/suppliers", status_code=201, dependencies=[Depends(inventory_access)])
def create_supplier(payload: SupplierInput, db: Session = Depends(get_db)) -> dict:
    supplier = Supplier(**payload.model_dump())
    db.add(supplier)
    _commit(db, "供应商名称已经存在")
    db.refresh(supplier)
    return ok(serialize_supplier(supplier))


@router.patch("/suppliers/{supplier_id}", dependencies=[Depends(inventory_access)])
def update_supplier(supplier_id: int, payload: SupplierInput, db: Session = Depends(get_db)) -> dict:
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise BusinessError("供应商不存在", code="supplier_not_found", status_code=404)
    for key, value in payload.model_dump().items():
        setattr(supplier, key, value)
    _commit(db, "供应商名称已经存在")
    return ok(serialize_supplier(supplier))


@router.get("/purchase-orders", dependencies=[Depends(inventory_access)])
def list_purchase_orders(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(PurchaseOrder).options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.supplier)).order_by(PurchaseOrder.created_at.desc()).limit(1000)))
    return ok([serialize_purchase_order(db, row) for row in rows])


@router.post("/purchase-orders", status_code=201, dependencies=[Depends(inventory_access)])
def add_purchase_order(payload: PurchaseOrderInput, user: User = Depends(inventory_access), db: Session = Depends(get_db)) -> dict:
    order = create_purchase_order(db, supplier_id=payload.supplier_id, items=[row.model_dump() for row in payload.items], expected_at=payload.expected_at, notes=payload.notes, created_by=user.id)
    _commit(db, "采购单数据冲突")
    return ok(serialize_purchase_order(db, load_purchase_order(db, order.id)))


@router.get("/purchase-orders/{purchase_order_id}", dependencies=[Depends(inventory_access)])
def purchase_order_detail(purchase_order_id: int, db: Session = Depends(get_db)) -> dict:
    order = load_purchase_order(db, purchase_order_id)
    transactions = list(db.scalars(select(InventoryTransaction).where(InventoryTransaction.purchase_order_id == order.id).order_by(InventoryTransaction.created_at.desc())))
    payments = list(db.scalars(select(FinanceTransaction).where(
        FinanceTransaction.purchase_order_id == order.id,
        FinanceTransaction.deleted_at.is_(None),
    ).order_by(FinanceTransaction.paid_at.desc())))
    return ok({
        "order": serialize_purchase_order(db, order),
        "transactions": [InventoryTransactionRead.model_validate(row) for row in transactions],
        "payments": payments,
    })


@router.post("/purchase-orders/{purchase_order_id}/receive", dependencies=[Depends(inventory_access)])
def receive_purchase(purchase_order_id: int, payload: PurchaseReceiptInput, user: User = Depends(inventory_access), db: Session = Depends(get_db)) -> dict:
    order = load_purchase_order(db, purchase_order_id)
    transactions = receive_purchase_order(db, order, [row.model_dump() for row in payload.lines], user_id=user.id)
    _commit(db, "入库失败，批次号可能重复")
    return ok({"order": serialize_purchase_order(db, load_purchase_order(db, order.id)), "transaction_ids": [row.id for row in transactions]})


@router.post("/purchase-orders/{purchase_order_id}/return", dependencies=[Depends(inventory_access)])
def return_purchase(purchase_order_id: int, payload: PurchaseReturnInput, user: User = Depends(inventory_access), db: Session = Depends(get_db)) -> dict:
    order = load_purchase_order(db, purchase_order_id)
    transaction = return_purchase_item(db, order, payload.purchase_order_item_id, payload.quantity, user_id=user.id, remarks=payload.remarks)
    _commit(db)
    return ok({"order": serialize_purchase_order(db, load_purchase_order(db, order.id)), "transaction_id": transaction.id})


@router.post("/purchase-orders/{purchase_order_id}/pay", dependencies=[Depends(finance_access)])
def pay_purchase(purchase_order_id: int, payload: PurchasePaymentInput, db: Session = Depends(get_db)) -> dict:
    order = load_purchase_order(db, purchase_order_id)
    payment = FinanceService.create(db, FinanceCreate(
        purchase_order_id=order.id,
        transaction_type="expense",
        category="采购付款",
        amount=payload.amount,
        payment_method=payload.payment_method,
        paid_at=payload.paid_at,
        description=payload.description or f"采购单 {order.purchase_no} 付款",
    ))
    _commit(db)
    db.refresh(payment)
    return ok({"payment": payment, "order": serialize_purchase_order(db, load_purchase_order(db, order.id))})


def _serialize_stocktake(stocktake: Stocktake) -> dict:
    return {
        "id": stocktake.id,
        "stocktake_no": stocktake.stocktake_no,
        "status": stocktake.status,
        "notes": stocktake.notes,
        "created_by": stocktake.created_by,
        "created_at": stocktake.created_at,
        "committed_at": stocktake.committed_at,
        "items": [
            {
                "id": row.id,
                "inventory_item_id": row.inventory_item_id,
                "system_quantity": inventory_quantity_int(row.system_quantity),
                "counted_quantity": inventory_quantity_int(row.counted_quantity),
                "difference_quantity": inventory_quantity_int(row.difference_quantity),
                "unit_cost": row.unit_cost,
                "remarks": row.remarks,
            }
            for row in stocktake.items
        ],
    }


@router.get("/stocktakes", dependencies=[Depends(inventory_access)])
def list_stocktakes(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(Stocktake).options(selectinload(Stocktake.items)).order_by(Stocktake.created_at.desc()).limit(500)))
    return ok([_serialize_stocktake(row) for row in rows])


@router.post("/stocktakes", status_code=201, dependencies=[Depends(inventory_access)])
def add_stocktake(payload: StocktakeInput, user: User = Depends(inventory_access), db: Session = Depends(get_db)) -> dict:
    stocktake = create_stocktake(db, [row.model_dump() for row in payload.items], user_id=user.id, notes=payload.notes)
    _commit(db)
    return ok(_serialize_stocktake(stocktake))


@router.post("/stocktakes/{stocktake_id}/commit", dependencies=[Depends(inventory_access)])
def finish_stocktake(stocktake_id: int, user: User = Depends(inventory_access), db: Session = Depends(get_db)) -> dict:
    stocktake = db.scalar(select(Stocktake).where(Stocktake.id == stocktake_id).options(selectinload(Stocktake.items)))
    if not stocktake:
        raise BusinessError("盘点单不存在", code="stocktake_not_found", status_code=404)
    transactions = commit_stocktake(db, stocktake, user_id=user.id)
    _commit(db)
    return ok({"stocktake": _serialize_stocktake(stocktake), "transaction_ids": [row.id for row in transactions]})


@router.get("/analytics/operations", dependencies=[Depends(finance_access)])
def operations_analytics(days: int = Query(default=30, ge=1, le=366), db: Session = Depends(get_db)) -> dict:
    start = datetime.now(timezone.utc) - timedelta(days=days - 1)
    finance_rows = list(db.scalars(select(FinanceTransaction).where(
        FinanceTransaction.paid_at >= start,
        FinanceTransaction.deleted_at.is_(None),
    ).order_by(FinanceTransaction.paid_at)))
    orders = list(db.scalars(select(RepairOrder).where(RepairOrder.created_at >= start, RepairOrder.deleted_at.is_(None))))
    tickets = list(db.scalars(select(ServiceTicket).where(ServiceTicket.created_at >= start, ServiceTicket.deleted_at.is_(None))))
    daily: dict[str, dict] = defaultdict(lambda: {"income": Decimal("0"), "expense": Decimal("0"), "refund": Decimal("0"), "orders": 0, "tickets": 0})
    for row in finance_rows:
        daily[row.paid_at.date().isoformat()][row.transaction_type] += Decimal(row.amount)
    for row in orders:
        daily[row.created_at.date().isoformat()]["orders"] += 1
    for row in tickets:
        daily[row.created_at.date().isoformat()]["tickets"] += 1
    totals = {
        "income": sum((Decimal(row.amount) for row in finance_rows if row.transaction_type == "income"), Decimal("0")),
        "expense": sum((Decimal(row.amount) for row in finance_rows if row.transaction_type == "expense"), Decimal("0")),
        "refund": sum((Decimal(row.amount) for row in finance_rows if row.transaction_type == "refund"), Decimal("0")),
        "orders": len(orders),
        "tickets": len(tickets),
    }
    totals["net_cashflow"] = totals["income"] - totals["expense"] - totals["refund"]
    owner_counts: dict[int | None, int] = defaultdict(int)
    for row in db.scalars(select(ServiceTicket).where(ServiceTicket.status.not_in(FINAL_TICKET_STATUSES), ServiceTicket.deleted_at.is_(None))):
        owner_counts[row.current_owner_id] += 1
    users = {row.id: row.display_name for row in db.scalars(select(User))}
    workload = [{"user_id": key, "name": users.get(key, "未分派"), "open_tickets": value} for key, value in sorted(owner_counts.items(), key=lambda pair: pair[1], reverse=True)]
    return ok({"days": days, "totals": totals, "daily": [{"date": key, **value} for key, value in sorted(daily.items())], "workload": workload})


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return row.value if row else default


def _put_setting(db: Session, key: str, value: str, description: str) -> None:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row:
        row.value = value
        row.description = description
    else:
        db.add(SystemSetting(key=key, value=value, description=description, is_secret=False))


@router.get("/backups/schedule", dependencies=[Depends(admin_access)])
def backup_schedule(db: Session = Depends(get_db)) -> dict:
    return ok({
        "enabled": _setting(db, "backup.auto_enabled", "false") == "true",
        "time": _setting(db, "backup.schedule_time", "02:30"),
        "retention_days": int(_setting(db, "backup.retention_days", "30")),
        "keep_count": int(_setting(db, "backup.keep_count", "30")),
        "offsite_dir": _setting(db, "backup.offsite_dir", "") or None,
        "task_name": TASK_NAME,
    })


@router.put("/backups/schedule", dependencies=[Depends(admin_access)])
def update_backup_schedule(payload: BackupScheduleInput, db: Session = Depends(get_db)) -> dict:
    # 先更新计划任务，成功后再写入启用状态。
    scheduler = sync_windows_backup_task(run_at=payload.time, enabled=payload.enabled)
    values = {
        "backup.auto_enabled": ("true" if payload.enabled else "false", "是否启用每日校验备份"),
        "backup.schedule_time": (payload.time, "每日备份时间"),
        "backup.retention_days": (str(payload.retention_days), "本机备份保留天数"),
        "backup.keep_count": (str(payload.keep_count), "本机最少保留份数上限"),
        "backup.offsite_dir": (payload.offsite_dir or "", "可选异地副本目录"),
    }
    for key, (value, description) in values.items():
        _put_setting(db, key, value, description)
    _commit(db)
    data = backup_schedule(db)
    data["data"]["scheduler"] = scheduler
    return data


@router.get("/integrations/status", dependencies=[Depends(admin_access)])
def integration_status(db: Session = Depends(get_db)) -> dict:
    email = load_email_config(db)
    return ok({
        "smtp": {
            "mode": email.mode,
            "configured": bool(email.sender and email.smtp_host and email.password),
            "sender": email.sender,
            "host": email.smtp_host,
            "safe_test_required": email.mode == "smtp",
        },
        "wecom": {
            "mode": settings.wecom_mode,
            "configured": bool(
                settings.wecom_corp_id
                and settings.wecom_agent_id
                and settings.wecom_app_secret
            ),
            "callback_configured": bool(
                settings.wecom_corp_id
                and settings.wecom_callback_token
                and settings.wecom_callback_aes_key
            ),
            "message": (
                "企业微信真实发送已启用"
                if settings.wecom_mode == "real"
                else "企业微信处于 Mock 模式，凭据验证完成后再切换为 real"
            ),
        },
        "sf_express": {
            "mode": "configured" if settings.sf_partner_id and settings.sf_checkword else "mock",
            "configured": bool(settings.sf_partner_id and settings.sf_checkword),
            "message": "需要顺丰沙箱账号后才能建单联调" if not settings.sf_partner_id else "凭据已存在，正式建单前仍需沙箱验证",
        },
    })
