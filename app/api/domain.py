from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import ok
from app.core.config import settings
from app.core.auth import admin_access, finance_access, inventory_access, require_authenticated_user, require_roles
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.integrations.wecom import WeComAPIError, get_wecom_service
from app.models.entities import (
    Attachment,
    CalibrationRecord,
    Customer,
    DamageAssessment,
    DamageAssessmentResult,
    DamageSopStep,
    DamageSopTemplate,
    DeletedRecord,
    Diagnosis,
    DroneDevice,
    EmailDelivery,
    OutboundCall,
    OutboundEmail,
    FinanceTransaction,
    FlightLog,
    FollowUpTask,
    InventoryItem,
    InventoryTransaction,
    Quote,
    RepairOrder,
    RepairOrderStatusHistory,
    ProcessingGroup,
    ProcessingGroupMember,
    PointMap,
    PointMarker,
    ServiceTicket,
    ServiceTicketCollaborator,
    ServiceTicketNote,
    ServiceTicketTimeline,
    SpecialistEscalation,
    Shipment,
    ShipmentEvent,
    SystemSetting,
    TaskRecord,
    User,
    WorkOrderGroup,
    WorkOrderGroupMember,
)
from app.reports.pdf import PdfReportService
from app.services.branding import load_brand_name
from app.schemas.domain import (
    CalibrationCreate,
    CalibrationLabSimulationRequest,
    CustomerCreate,
    CustomerUpdate,
    CustomerRead,
    DamageAssessmentComplete,
    DamageAssessmentCreate,
    DamageAssessmentResultUpdate,
    DamageSopStepInput,
    DamageSopTemplateCreate,
    DeviceCreate,
    DeviceRead,
    DiagnosisCreate,
    EmailSendRequest,
    EmailPreviewRequest,
    EmailTemplateCreate,
    EmailTemplateUpdate,
    EmailConfigUpdate,
    OutboundCallCreate,
    OutboundCallComplete,
    OutboundEmailCreate,
    FinanceCreate,
    FinanceUpdate,
    FinanceRead,
    FollowUpUpdate,
    FollowUpCreate,
    InventoryClientVisibilityUpdate,
    InventoryItemCreate,
    InventoryItemRead,
    InventoryTransactionRead,
    QuoteCreate,
    QuoteRead,
    QuickEntryCreate,
    ReplacementTicketUpdate,
    RepairOrderCreate,
    RepairInspectionUpdate,
    RepairOrderRead,
    ProcessingGroupCreate,
    ProcessingGroupRead,
    PointMapCreate,
    PointMarkerCreate,
    PointMarkerUpdate,
    ServiceTicketCreate,
    ServiceTicketRead,
    SpecialistEscalationCreate,
    SpecialistEscalationUpdate,
    SettingInput,
    ShipmentCreate,
    ShipmentUpdate,
    StatusChange,
    StockChange,
    TicketAssignmentUpdate,
    TicketCollaboratorAdd,
    TicketDescriptionUpdate,
    TicketNoteCreate,
    TicketReminder,
    TicketStatusChange,
    TicketTypeChange,
)
from app.services.finance import FinanceService
from app.services.access import (
    is_admin,
    require_order_access,
    require_quote_access,
    require_service_ticket_access,
    scope_orders,
    scope_quotes,
    scope_service_tickets,
)
from app.services.email import queue_quote_email
from app.services.inventory import InventoryService
from app.services.numbering import allocate_repair_order_no, make_no
from app.services.orders import RepairOrderService
from app.services.quotes import QuoteService
from app.services.quick_entry import QuickEntryService
from app.services.tickets import TicketService
from app.services.trash import delete_resource, list_deleted_records, restore_record
from app.services.communications import (
    complete_call,
    create_call,
    queue_outbound_email,
    render_email_preview,
    resolve_email_context,
)
from app.services.email_template_library import (
    create_custom_email_template,
    custom_template_payload,
    delete_custom_email_template,
    email_template_library_metadata,
    list_email_template_library,
    restore_custom_email_template,
    update_custom_email_template,
)
from app.integrations.calibration.dji import DJIOfficialWorkflowProvider, gimbal_calibration_capability
from app.integrations.calibration.gimbal_engine import CalibrationKind, GimbalCalibrationEngine
from app.integrations.calibration.device_discovery import discover_connected_dji_devices
from app.integrations.calibration.profiles import list_profiles
from app.integrations.calibration.transport import list_serial_ports
from app.services.recommendations import QuoteRecommendationService
from app.services.damage_assessment import (
    assessment_detail,
    complete_assessment,
    create_assessment,
    load_assessment,
    template_applies,
    update_result,
)
from app.services.point_map_assets import render_pdf_page_png
from app.storage.local import LocalStorageService, safe_attachment_content_type
from app.tasks.flight_log import parse_flight_log_task
from app.tasks.email import send_quote_email_task
from app.tasks.communications import send_outbound_email_task
from app.tasks.point_map_import import import_point_map_library_task
from app.integrations.email.service import email_config_status
from app.services.email_config import load_email_config, safe_email_config, save_email_config


router = APIRouter(prefix="/api", dependencies=[Depends(require_authenticated_user)])


def _commit(db: Session, message: str = "数据存在重复或关联冲突") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(message, code="data_conflict", status_code=409) from exc


def _deletion_payload(record: DeletedRecord) -> dict:
    return {
        "id": record.id,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "label": record.label,
        "deleted_by": record.deleted_by,
        "deleted_at": record.deleted_at,
        "restored_at": record.restored_at,
    }


FINANCE_DETAIL_ROLES = {"admin", "manager", "finance"}
INVENTORY_COST_ROLES = {"admin", "manager", "warehouse"}
REPAIR_OWNER_ROLES = {"admin", "manager", "engineer", "technical_support"}


def _same_stock_request(existing: InventoryTransaction, payload: StockChange) -> bool:
    if (
        existing.inventory_item_id != payload.inventory_item_id
        or existing.transaction_type != payload.transaction_type
        or Decimal(existing.quantity) != Decimal(payload.quantity)
        or existing.repair_order_id != payload.repair_order_id
        or existing.operator_id != payload.operator_id
        or existing.remarks != payload.remarks
    ):
        return False
    # Repair returns intentionally inherit the weighted cost of prior issues.
    return (
        payload.unit_cost is None
        or payload.transaction_type == "repair_return"
        or Decimal(existing.unit_cost) == Decimal(payload.unit_cost)
    )


def _same_finance_request(existing: FinanceTransaction, payload: FinanceCreate) -> bool:
    if (
        existing.quote_id != payload.quote_id
        or existing.purchase_order_id != payload.purchase_order_id
        or existing.transaction_type != payload.transaction_type
        or existing.category != payload.category
        or Decimal(existing.amount) != Decimal(payload.amount)
        or existing.payment_method != payload.payment_method
        or existing.description != payload.description
        or existing.attachment_id != payload.attachment_id
    ):
        return False
    if payload.repair_order_id is not None and existing.repair_order_id != payload.repair_order_id:
        return False
    if payload.customer_id is not None and existing.customer_id != payload.customer_id:
        return False
    if payload.paid_at is not None and existing.paid_at != payload.paid_at:
        return False
    return True


def _order_read_for_user(order: RepairOrder, user: User) -> dict:
    payload = RepairOrderRead.model_validate(order).model_dump(mode="json")
    payload["device_serial_number"] = order.device.serial_number if order.device else None
    if user.role not in FINANCE_DETAIL_ROLES:
        for field in ("total_cost", "total_received", "gross_profit"):
            payload.pop(field, None)
    return payload


def _active_order(db: Session, order_id: int, user: User) -> RepairOrder:
    order = db.get(RepairOrder, order_id)
    if not order or order.deleted_at is not None:
        raise BusinessError("工单不存在", code="order_not_found", status_code=404)
    return require_order_access(db, order, user)


def _require_attachment_access(db: Session, attachment: Attachment, user: User) -> Attachment:
    if user.role in {"admin", "manager"}:
        return attachment
    if attachment.repair_order_id:
        _active_order(db, attachment.repair_order_id, user)
        return attachment
    if attachment.uploaded_by == user.id:
        return attachment
    raise BusinessError("无权访问该附件", code="attachment_access_denied", status_code=403)


def _require_call_access(db: Session, call: OutboundCall, user: User) -> OutboundCall:
    if user.role in {"admin", "manager"}:
        return call
    if call.service_ticket_id:
        ticket = db.get(ServiceTicket, call.service_ticket_id)
        if not ticket:
            raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
        require_service_ticket_access(db, ticket, user)
    if call.repair_order_id:
        _active_order(db, call.repair_order_id, user)
    if not call.service_ticket_id and not call.repair_order_id:
        if call.assigned_to != user.id and call.created_by != user.id:
            raise BusinessError("无权处理该外呼任务", code="call_access_denied", status_code=403)
    return call


def _register_quote_pdf(
    db: Session, quote: Quote, path: Path, *, uploaded_by: int | None = None
) -> Attachment:
    path = path.resolve()
    existing = db.scalar(select(Attachment).where(Attachment.storage_path == str(path)))
    if existing:
        return existing
    content = path.read_bytes()
    customer_id = quote.repair_order.customer_id if quote.repair_order else quote.service_ticket.customer_id
    attachment = Attachment(
        repair_order_id=quote.repair_order_id, customer_id=customer_id,
        attachment_type="quote_pdf", original_filename=path.name, storage_path=str(path),
        content_type="application/pdf", file_size=len(content), sha256=hashlib.sha256(content).hexdigest(),
        uploaded_by=uploaded_by,
    )
    db.add(attachment)
    db.flush()
    return attachment


def _register_service_ticket_pdf(
    db: Session, ticket: ServiceTicket, path: Path, *, uploaded_by: int | None = None
) -> Attachment:
    path = path.resolve()
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(select(Attachment).where(Attachment.storage_path == str(path)))
    if existing:
        existing.original_filename = path.name
        existing.file_size = len(content)
        existing.sha256 = digest
        existing.content_type = "application/pdf"
        if uploaded_by is not None:
            existing.uploaded_by = uploaded_by
        return existing
    attachment = Attachment(
        repair_order_id=ticket.repair_order_id,
        customer_id=ticket.customer_id,
        attachment_type="service_ticket_pdf",
        original_filename=path.name,
        storage_path=str(path),
        content_type="application/pdf",
        file_size=len(content),
        sha256=digest,
        uploaded_by=uploaded_by,
    )
    db.add(attachment)
    db.flush()
    return attachment


def _register_order_report(
    db: Session,
    order: RepairOrder,
    path: Path,
    *,
    attachment_type: str,
    uploaded_by: int,
) -> Attachment:
    path = path.resolve()
    content = path.read_bytes()
    existing = db.scalar(select(Attachment).where(Attachment.storage_path == str(path)))
    if existing:
        existing.original_filename = path.name
        existing.file_size = len(content)
        existing.sha256 = hashlib.sha256(content).hexdigest()
        existing.content_type = "application/pdf"
        existing.uploaded_by = uploaded_by
        return existing
    attachment = Attachment(
        repair_order_id=order.id,
        customer_id=order.customer_id,
        attachment_type=attachment_type,
        original_filename=path.name,
        storage_path=str(path),
        content_type="application/pdf",
        file_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        uploaded_by=uploaded_by,
    )
    db.add(attachment)
    db.flush()
    return attachment


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


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    open_orders_stmt = scope_orders(
        select(func.count(RepairOrder.id)).where(RepairOrder.status.not_in(["completed", "cancelled"])),
        current_user,
    )
    data = {
        "customers": db.scalar(select(func.count(Customer.id)).where(Customer.deleted_at.is_(None))) or 0,
        "devices": db.scalar(select(func.count(DroneDevice.id)).where(DroneDevice.deleted_at.is_(None))) or 0,
        "open_orders": db.scalar(open_orders_stmt) or 0,
        "low_stock": db.scalar(select(func.count(InventoryItem.id)).where(
            InventoryItem.stock_quantity <= InventoryItem.safety_stock,
            InventoryItem.enabled.is_(True),
            InventoryItem.deleted_at.is_(None),
        )) or 0,
        "pending_followups": db.scalar(select(func.count(FollowUpTask.id)).where(
            FollowUpTask.status == "pending",
            FollowUpTask.deleted_at.is_(None),
        )) or 0,
        "income": (
            db.scalar(select(func.coalesce(func.sum(FinanceTransaction.amount), 0)).where(
                FinanceTransaction.transaction_type == "income",
                FinanceTransaction.deleted_at.is_(None),
            )) or 0
        ) if current_user.role in FINANCE_DETAIL_ROLES else None,
    }
    recent = list(db.scalars(scope_orders(
        select(RepairOrder).options(selectinload(RepairOrder.device)), current_user
    ).order_by(RepairOrder.created_at.desc()).limit(8)))
    data["recent_orders"] = [_order_read_for_user(x, current_user) for x in recent]
    return ok(data)


@router.get("/search")
def global_search(
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    query = q.strip()[:100]
    if not query:
        return ok([])
    term = f"%{query}%"
    results: list[dict] = []

    order_stmt = scope_orders(select(RepairOrder), current_user).where(or_(
        RepairOrder.order_no.like(term),
        RepairOrder.fault_description.like(term),
    )).order_by(RepairOrder.updated_at.desc()).limit(10)
    for order in db.scalars(order_stmt):
        results.append({"kind": "repair_order", "id": order.id, "number": order.order_no,
                        "title": order.fault_description[:120], "category": "维修工单"})

    ticket_stmt = scope_service_tickets(select(ServiceTicket), current_user).where(or_(
        ServiceTicket.ticket_no.like(term),
        ServiceTicket.title.like(term),
        ServiceTicket.description.like(term),
    )).order_by(ServiceTicket.updated_at.desc()).limit(10)
    for ticket in db.scalars(ticket_stmt):
        results.append({"kind": "service_ticket", "id": ticket.id, "number": ticket.ticket_no,
                        "title": ticket.title, "category": "服务工单"})

    quote_stmt = scope_quotes(select(Quote), current_user).where(Quote.quote_no.like(term)).order_by(Quote.created_at.desc()).limit(8)
    for quote in db.scalars(quote_stmt):
        results.append({"kind": "quote", "id": quote.id, "number": quote.quote_no,
                        "title": f"V{quote.version} · ¥{quote.total_amount:.2f}", "category": "报价"})

    for customer in db.scalars(select(Customer).where(
        Customer.deleted_at.is_(None),
        or_(
            Customer.customer_no.like(term), Customer.name.like(term), Customer.phone.like(term),
            Customer.email.like(term), Customer.company_name.like(term),
        ),
    ).order_by(Customer.created_at.desc()).limit(8)):
        results.append({"kind": "customer", "id": customer.id, "number": customer.customer_no,
                        "title": " · ".join(x for x in [customer.name, customer.phone] if x), "category": "客户"})

    for device in db.scalars(select(DroneDevice).where(
        DroneDevice.deleted_at.is_(None),
        or_(
            DroneDevice.serial_number.like(term), DroneDevice.brand.like(term), DroneDevice.model.like(term),
        ),
    ).order_by(DroneDevice.updated_at.desc()).limit(8)):
        results.append({"kind": "device", "id": device.id, "number": device.serial_number,
                        "title": f"{device.brand} {device.model}".strip(), "category": "设备"})

    for delivery in db.scalars(select(OutboundEmail).where(or_(
        OutboundEmail.email_no.like(term), OutboundEmail.recipient.like(term),
        OutboundEmail.subject_snapshot.like(term),
    )).order_by(OutboundEmail.created_at.desc()).limit(8)):
        results.append({"kind": "email", "id": delivery.id, "number": delivery.email_no,
                        "title": delivery.subject_snapshot, "category": "外发邮件"})
    visible_order_ids = set(db.scalars(scope_orders(select(RepairOrder.id), current_user)))
    group_stmt = (
        select(WorkOrderGroup)
        .join(WorkOrderGroupMember, WorkOrderGroupMember.group_id == WorkOrderGroup.id)
        .join(RepairOrder, RepairOrder.id == WorkOrderGroupMember.repair_order_id)
        .where(
            WorkOrderGroup.deleted_at.is_(None),
            RepairOrder.deleted_at.is_(None),
            RepairOrder.id.in_(visible_order_ids),
            or_(WorkOrderGroup.name.like(term), RepairOrder.order_no.like(term)),
        )
        .order_by(WorkOrderGroup.updated_at.desc())
        .distinct()
        .limit(8)
    )
    for group in db.scalars(group_stmt):
        member_orders = [
            member.repair_order
            for member in group.members
            if member.repair_order
            and member.repair_order.deleted_at is None
            and member.repair_order.id in visible_order_ids
        ]
        if not any(order.id in visible_order_ids for order in member_orders):
            continue
        numbers = " / ".join(order.order_no for order in member_orders[:3])
        results.append({"kind": "work_order_group", "id": group.id, "number": group.name,
                        "title": numbers, "category": "工单组合"})
    return ok(results[:40])


@router.get("/customers")
def list_customers(
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(Customer).where(Customer.deleted_at.is_(None)).order_by(Customer.created_at.desc())
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.name.like(term), Customer.phone.like(term), Customer.email.like(term), Customer.customer_no.like(term), Customer.company_name.like(term)))
    customers = list(db.scalars(stmt.limit(500)))
    customer_ids = [row.id for row in customers]
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_by_customer: dict[int, list[dict]] = {customer_id: [] for customer_id in customer_ids}
    if customer_ids:
        recent_tickets = db.scalars(
            scope_service_tickets(select(ServiceTicket), current_user).where(
                ServiceTicket.customer_id.in_(customer_ids),
                ServiceTicket.updated_at >= cutoff,
            ).order_by(ServiceTicket.updated_at.desc())
        )
        for ticket in recent_tickets:
            bucket = recent_by_customer[ticket.customer_id]
            if len(bucket) < 3:
                bucket.append({
                    "ticket_id": ticket.id,
                    "ticket_no": ticket.ticket_no,
                    "title": ticket.title,
                    "status": ticket.status,
                    "updated_at": ticket.updated_at,
                })
    return ok([
        {**CustomerRead.model_validate(row).model_dump(), "recent_services": recent_by_customer[row.id]}
        for row in customers
    ])


@router.post("/customers", status_code=201)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)) -> dict:
    customer = Customer(customer_no=make_no("CU"), **payload.model_dump())
    db.add(customer)
    _commit(db, "手机号已被其他客户使用")
    db.refresh(customer)
    return ok(CustomerRead.model_validate(customer))


@router.delete("/customers/{customer_id}")
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    record = delete_resource(db, "customer", customer_id, user=current_user)
    _commit(db)
    return ok(_deletion_payload(record))


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: int, payload: CustomerUpdate, db: Session = Depends(get_db)) -> dict:
    customer = db.get(Customer, customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, key, value)
    _commit(db, "手机号已被其他客户使用")
    db.refresh(customer)
    return ok(CustomerRead.model_validate(customer))


@router.get("/customers/{customer_id}")
def customer_detail(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    customer = db.get(Customer, customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    devices = list(db.scalars(select(DroneDevice).where(
        DroneDevice.customer_id == customer_id,
        DroneDevice.deleted_at.is_(None),
    )))
    orders = list(db.scalars(scope_orders(
        select(RepairOrder).options(selectinload(RepairOrder.device)), current_user
    ).where(
        RepairOrder.customer_id == customer_id,
    ).order_by(RepairOrder.created_at.desc())))
    return ok({
        "customer": CustomerRead.model_validate(customer),
        "devices": [DeviceRead.model_validate(x) for x in devices],
        "orders": [_order_read_for_user(x, current_user) for x in orders],
    })


@router.get("/devices")
def list_devices(customer_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    stmt = select(DroneDevice).where(DroneDevice.deleted_at.is_(None)).order_by(DroneDevice.created_at.desc())
    if customer_id:
        stmt = stmt.where(DroneDevice.customer_id == customer_id)
    return ok([DeviceRead.model_validate(x) for x in db.scalars(stmt.limit(500))])


@router.post("/devices", status_code=201)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)) -> dict:
    customer = db.get(Customer, payload.customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    device = DroneDevice(**payload.model_dump())
    db.add(device)
    _commit(db, "设备信息存在关联冲突")
    db.refresh(device)
    return ok(DeviceRead.model_validate(device))


@router.delete("/devices/{device_id}")
def delete_device(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    record = delete_resource(db, "drone_device", device_id, user=current_user)
    _commit(db)
    return ok(_deletion_payload(record))


@router.get("/orders")
def list_orders(
    status: str | None = None,
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(RepairOrder).options(selectinload(RepairOrder.device)).order_by(RepairOrder.created_at.desc())
    stmt = scope_orders(stmt, current_user)
    if status:
        stmt = stmt.where(RepairOrder.status == status)
    if q.strip():
        term = f"%{q.strip()[:100]}%"
        customer_ids = select(Customer.id).where(or_(
            Customer.name.like(term), Customer.phone.like(term), Customer.email.like(term)
        ))
        device_ids = select(DroneDevice.id).where(or_(
            DroneDevice.serial_number.like(term),
            DroneDevice.brand.like(term),
            DroneDevice.model.like(term),
        ))
        stmt = stmt.where(or_(
            RepairOrder.order_no.like(term),
            RepairOrder.fault_description.like(term),
            RepairOrder.customer_id.in_(customer_ids),
            RepairOrder.device_id.in_(device_ids),
        ))
    return ok([_order_read_for_user(x, current_user) for x in db.scalars(stmt.limit(500))])


@router.post("/orders", status_code=201)
def create_order(
    payload: RepairOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    customer = db.get(Customer, payload.customer_id)
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    device = db.get(DroneDevice, payload.device_id)
    if not device or device.deleted_at is not None:
        raise BusinessError("设备不存在", code="device_not_found", status_code=404)
    if device.customer_id != payload.customer_id:
        raise BusinessError("设备不存在或不属于该客户", code="device_customer_mismatch")
    if payload.engineer_id:
        engineer = db.get(User, payload.engineer_id)
        if not engineer or not engineer.enabled or engineer.role not in REPAIR_OWNER_ROLES:
            raise BusinessError("负责人不存在、已停用或角色不可接维修工单", code="engineer_not_available", status_code=409)
    order_values = payload.model_dump()
    matched_group_id = payload.processing_group_id
    if not matched_group_id and payload.engineer_id:
        candidate_group_ids = list(db.scalars(
            select(ProcessingGroupMember.group_id)
            .join(ProcessingGroup, ProcessingGroup.id == ProcessingGroupMember.group_id)
            .where(
                ProcessingGroupMember.user_id == payload.engineer_id,
                ProcessingGroup.group_type == "service",
                ProcessingGroup.enabled.is_(True),
            )
            .order_by(ProcessingGroup.id)
            .limit(2)
        ))
        if len(candidate_group_ids) == 1:
            matched_group_id = candidate_group_ids[0]
            order_values["processing_group_id"] = matched_group_id
    if matched_group_id:
        group = db.get(ProcessingGroup, matched_group_id)
        if not group or not group.enabled or group.group_type != "service":
            raise BusinessError("服务组不存在或不可用", code="service_group_not_found", status_code=404)
        if payload.engineer_id and not db.scalar(select(ProcessingGroupMember.id).where(
            ProcessingGroupMember.group_id == matched_group_id,
            ProcessingGroupMember.user_id == payload.engineer_id,
        )):
            raise BusinessError("负责人不是所选服务组成员", code="engineer_group_mismatch", status_code=409)
    order = RepairOrder(order_no=allocate_repair_order_no(db), **order_values)
    db.add(order)
    db.flush()
    db.add(RepairOrderStatusHistory(repair_order_id=order.id, from_status=None, to_status=order.status, changed_by=payload.engineer_id, reason="创建工单"))
    TicketService.ensure_for_repair_order(db, order, created_by=current_user.id)
    _commit(db)
    db.refresh(order)
    return ok(_order_read_for_user(order, current_user))


@router.delete("/orders/{order_id}")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    record = delete_resource(db, "repair_order", order_id, user=current_user)
    _commit(db)
    return ok(_deletion_payload(record))


@router.get("/orders/{order_id}")
def order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    order = db.scalar(select(RepairOrder).where(
        RepairOrder.id == order_id,
        RepairOrder.deleted_at.is_(None),
    ).options(selectinload(RepairOrder.customer), selectinload(RepairOrder.device)))
    if not order:
        raise BusinessError("工单不存在", code="order_not_found", status_code=404)
    require_order_access(db, order, current_user)
    related = {
        "service_ticket": db.scalar(select(ServiceTicket).where(
            ServiceTicket.repair_order_id == order_id,
            ServiceTicket.deleted_at.is_(None),
        )),
        "status_history": list(db.scalars(select(RepairOrderStatusHistory).where(RepairOrderStatusHistory.repair_order_id == order_id).order_by(RepairOrderStatusHistory.changed_at))),
        "quotes": list(db.scalars(select(Quote).where(
            Quote.repair_order_id == order_id,
            Quote.deleted_at.is_(None),
        ).order_by(Quote.version.desc()).options(selectinload(Quote.items)))),
        "attachments": list(db.scalars(select(Attachment).where(Attachment.repair_order_id == order_id))),
        "flight_logs": list(db.scalars(select(FlightLog).where(FlightLog.repair_order_id == order_id))),
        "diagnoses": list(db.scalars(select(Diagnosis).where(Diagnosis.repair_order_id == order_id))),
        "inventory_transactions": [
            InventoryTransactionRead.model_validate(row)
            for row in db.scalars(select(InventoryTransaction).where(
                InventoryTransaction.repair_order_id == order_id
            ).order_by(InventoryTransaction.created_at.desc(), InventoryTransaction.id.desc()))
        ] if current_user.role in INVENTORY_COST_ROLES else [],
        "finance_transactions": list(db.scalars(select(FinanceTransaction).where(
            FinanceTransaction.repair_order_id == order_id,
            FinanceTransaction.deleted_at.is_(None),
        ))) if current_user.role in FINANCE_DETAIL_ROLES else [],
        "calibrations": list(db.scalars(select(CalibrationRecord).where(CalibrationRecord.repair_order_id == order_id))),
        "shipments": list(db.scalars(select(Shipment).where(Shipment.repair_order_id == order_id))),
        "followups": list(db.scalars(select(FollowUpTask).where(
            FollowUpTask.repair_order_id == order_id,
            FollowUpTask.deleted_at.is_(None),
        ).order_by(
            case((FollowUpTask.status == "pending", 0), else_=1),
            FollowUpTask.scheduled_at,
            FollowUpTask.id,
        ))),
    }
    return ok({
        "order": _order_read_for_user(order, current_user),
        "customer": CustomerRead.model_validate(order.customer),
        "device": DeviceRead.model_validate(order.device),
        **related,
    })


@router.post("/orders/{order_id}/status")
def change_order_status(
    order_id: int, payload: StatusChange,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    order = db.get(RepairOrder, order_id)
    if not order:
        raise BusinessError("工单不存在", code="order_not_found", status_code=404)
    require_order_access(db, order, current_user)
    RepairOrderService.change_status(db, order, payload.status, changed_by=current_user.id, reason=payload.reason)
    _commit(db)
    return ok(_order_read_for_user(order, current_user))


@router.patch("/orders/{order_id}/inspection")
def update_order_inspection(
    order_id: int,
    payload: RepairInspectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "manager", "engineer", "technical_support")),
) -> dict:
    order = db.get(RepairOrder, order_id)
    if not order:
        raise BusinessError("工单不存在", code="order_not_found", status_code=404)
    require_order_access(db, order, current_user)
    order.internal_notes = payload.internal_notes
    _order_timeline(
        db,
        order.id,
        "inspection_updated",
        "已更新维修工单人工检测结果",
        current_user.id,
        {"text_length": len(order.internal_notes)},
    )
    _commit(db)
    db.refresh(order)
    return ok(_order_read_for_user(order, current_user))


@router.get("/processing-groups")
def list_processing_groups(db: Session = Depends(get_db)) -> dict:
    groups = list(db.scalars(select(ProcessingGroup).order_by(ProcessingGroup.name)))
    return ok([
        {
            "group": ProcessingGroupRead.model_validate(group),
            "member_ids": list(db.scalars(
                select(ProcessingGroupMember.user_id).where(ProcessingGroupMember.group_id == group.id)
            )),
        }
        for group in groups
    ])


@router.get("/team-members")
def list_team_members(db: Session = Depends(get_db)) -> dict:
    members = list(db.scalars(select(User).where(User.enabled.is_(True)).order_by(User.display_name)))
    memberships: dict[int, list[int]] = {member.id: [] for member in members}
    for row in db.scalars(select(ProcessingGroupMember)):
        if row.user_id in memberships:
            memberships[row.user_id].append(row.group_id)
    return ok([
        {
            "id": member.id,
            "display_name": member.display_name,
            "employee_no": member.employee_no,
            "role": member.role,
            "group_ids": memberships[member.id],
        }
        for member in members
    ])


@router.post(
    "/processing-groups", status_code=201,
    dependencies=[Depends(require_roles("admin", "manager"))],
)
def create_processing_group(payload: ProcessingGroupCreate, db: Session = Depends(get_db)) -> dict:
    group = ProcessingGroup(
        name=payload.name, group_type=payload.group_type, description=payload.description
    )
    db.add(group)
    db.flush()
    for user_id in dict.fromkeys(payload.member_ids):
        if not db.get(User, user_id):
            raise BusinessError(f"成员 #{user_id} 不存在", code="group_member_not_found", status_code=404)
        db.add(ProcessingGroupMember(group_id=group.id, user_id=user_id))
    _commit(db, "处理组名称已存在或成员重复")
    db.refresh(group)
    return ok({"group": ProcessingGroupRead.model_validate(group), "member_ids": payload.member_ids})


@router.get("/service-tickets")
def list_service_tickets(
    ticket_type: str | None = None, status: str | None = None,
    owner_id: int | None = None, overdue: bool = False, q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(ServiceTicket).order_by(ServiceTicket.created_at.desc())
    stmt = scope_service_tickets(stmt, current_user)
    if ticket_type:
        stmt = stmt.where(ServiceTicket.ticket_type == ticket_type)
    if status:
        stmt = stmt.where(ServiceTicket.status == status)
    if owner_id:
        stmt = stmt.where(ServiceTicket.current_owner_id == owner_id)
    query = q.strip()[:100]
    if query:
        term = f"%{query}%"
        customer_ids = select(Customer.id).where(or_(
            Customer.customer_no.like(term),
            Customer.name.like(term),
            Customer.phone.like(term),
            Customer.email.like(term),
        ))
        device_ids = select(DroneDevice.id).where(or_(
            DroneDevice.serial_number.like(term),
            DroneDevice.brand.like(term),
            DroneDevice.model.like(term),
        ))
        order_ids = select(RepairOrder.id).where(or_(
            RepairOrder.order_no.like(term),
            RepairOrder.fault_description.like(term),
        ))
        stmt = stmt.where(or_(
            ServiceTicket.ticket_no.like(term),
            ServiceTicket.title.like(term),
            ServiceTicket.description.like(term),
            ServiceTicket.replacement_inspection_result.like(term),
            ServiceTicket.return_reference.like(term),
            ServiceTicket.outbound_to_customer_tracking_no.like(term),
            ServiceTicket.customer_id.in_(customer_ids),
            ServiceTicket.device_id.in_(device_ids),
            ServiceTicket.repair_order_id.in_(order_ids),
        ))
    now = datetime.now(timezone.utc)
    if overdue:
        stmt = stmt.where(ServiceTicket.due_at.is_not(None), ServiceTicket.due_at < now).where(
            ServiceTicket.status.not_in({"resolved", "closed", "cancelled"})
        )
    tickets = list(db.scalars(stmt.limit(500)))
    return ok([
        {
            **ServiceTicketRead.model_validate(ticket).model_dump(),
            "overdue": bool(
                ticket.due_at and ticket.due_at < now
                and ticket.status not in {"resolved", "closed", "cancelled"}
            ),
        }
        for ticket in tickets
    ])


@router.post("/service-tickets", status_code=201)
def create_service_ticket(
    payload: ServiceTicketCreate, db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    if payload.repair_order_id:
        _active_order(db, payload.repair_order_id, current_user)
    if payload.device_id:
        device = db.get(DroneDevice, payload.device_id)
        if not device or device.deleted_at is not None:
            raise BusinessError("设备不存在", code="device_not_found", status_code=404)
    ticket = TicketService.create(db, payload, created_by=current_user.id)
    _commit(db, "服务工单编号、维修工单关联或协作成员冲突")
    db.refresh(ticket)
    return ok(ServiceTicketRead.model_validate(ticket))


@router.delete("/service-tickets/{ticket_id}")
def delete_service_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket or ticket.deleted_at is not None:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    if ticket.repair_order_id:
        raise BusinessError(
            "维修工单自动生成的服务工单不能单独删除，请从维修工单统一处理",
            code="linked_repair_ticket_delete_denied",
            status_code=409,
        )
    record = delete_resource(db, "service_ticket", ticket_id, user=current_user)
    _commit(db)
    return ok(_deletion_payload(record))


@router.get("/service-tickets/{ticket_id}")
def service_ticket_detail(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket or ticket.deleted_at is not None:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    return ok({
        "ticket": ServiceTicketRead.model_validate(ticket),
        "collaborators": list(db.scalars(
            select(ServiceTicketCollaborator).where(ServiceTicketCollaborator.ticket_id == ticket.id)
            .order_by(ServiceTicketCollaborator.added_at)
        )),
        "notes": list(db.scalars(
            select(ServiceTicketNote).where(ServiceTicketNote.ticket_id == ticket.id)
            .order_by(ServiceTicketNote.created_at)
        )),
        "timeline": list(db.scalars(
            select(ServiceTicketTimeline).where(ServiceTicketTimeline.ticket_id == ticket.id)
            .order_by(ServiceTicketTimeline.created_at)
        )),
        "escalations": list(db.scalars(
            select(SpecialistEscalation).where(SpecialistEscalation.service_ticket_id == ticket.id)
            .order_by(SpecialistEscalation.created_at.desc())
        )),
        "quotes": list(db.scalars(
            select(Quote).where(
                Quote.service_ticket_id == ticket.id,
                Quote.deleted_at.is_(None),
            )
            .order_by(Quote.version.desc()).options(selectinload(Quote.items))
        )),
    })


@router.post("/service-tickets/{ticket_id}/pdf")
def generate_service_ticket_pdf(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.scalar(
        select(ServiceTicket)
        .where(ServiceTicket.id == ticket_id)
        .options(
            selectinload(ServiceTicket.customer),
            selectinload(ServiceTicket.device),
            selectinload(ServiceTicket.current_owner),
            selectinload(ServiceTicket.processing_group),
        )
    )
    if not ticket or ticket.deleted_at is not None:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    customer_notes = list(db.scalars(
        select(ServiceTicketNote)
        .where(
            ServiceTicketNote.ticket_id == ticket.id,
            ServiceTicketNote.visibility == "customer",
        )
        .order_by(ServiceTicketNote.created_at)
    ))
    path = PdfReportService(brand_name=load_brand_name(db)).service_ticket(ticket, customer_notes=customer_notes)
    attachment = _register_service_ticket_pdf(db, ticket, path, uploaded_by=current_user.id)
    db.add(ServiceTicketTimeline(
        ticket_id=ticket.id,
        event_type="pdf_generated",
        summary="已生成客户可见的服务工单 PDF",
        actor_id=current_user.id,
        details_json={"attachment_id": attachment.id, "filename": path.name},
    ))
    _commit(db)
    return ok({
        "path": str(path),
        "download_url": f"/api/files/report/{path.name}",
        "attachment_id": attachment.id,
    })


@router.patch("/service-tickets/{ticket_id}/assignment")
def assign_service_ticket(
    ticket_id: int, payload: TicketAssignmentUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    if current_user.role not in {"admin", "manager"}:
        if payload.current_owner_id != current_user.id or payload.processing_group_id != ticket.processing_group_id:
            raise BusinessError(
                "非管理账号只能领取给自己，不能转派、取消分派或改变处理组",
                code="ticket_assign_denied",
                status_code=403,
            )
    TicketService.assign(db, ticket, payload, actor_id=current_user.id)
    _commit(db)
    return ok(ServiceTicketRead.model_validate(ticket))


@router.patch("/service-tickets/{ticket_id}/replacement")
def update_replacement_ticket(
    ticket_id: int, payload: ReplacementTicketUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket or ticket.deleted_at is not None:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    TicketService.update_replacement(db, ticket, payload, actor_id=current_user.id)
    _commit(db)
    db.refresh(ticket)
    return ok(ServiceTicketRead.model_validate(ticket))


@router.post("/service-tickets/{ticket_id}/collaborators", status_code=201)
def add_service_ticket_collaborator(
    ticket_id: int, payload: TicketCollaboratorAdd,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    collaborator = TicketService.add_collaborator(db, ticket, payload, actor_id=current_user.id)
    _commit(db, "该成员已经参与此工单")
    return ok(collaborator)


@router.post("/service-tickets/{ticket_id}/notes", status_code=201)
def add_service_ticket_note(
    ticket_id: int, payload: TicketNoteCreate,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    note = TicketService.add_note(db, ticket, payload, actor_id=current_user.id)
    _commit(db)
    return ok(note)


@router.post("/service-tickets/{ticket_id}/status")
def change_service_ticket_status(
    ticket_id: int, payload: TicketStatusChange,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    TicketService.change_status(db, ticket, payload, actor_id=current_user.id)
    _commit(db)
    return ok(ServiceTicketRead.model_validate(ticket))


@router.patch("/service-tickets/{ticket_id}/type")
def change_service_ticket_type(
    ticket_id: int, payload: TicketTypeChange,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    TicketService.change_type(db, ticket, payload, actor_id=current_user.id)
    _commit(db)
    db.refresh(ticket)
    return ok(ServiceTicketRead.model_validate(ticket))


@router.post("/service-tickets/{ticket_id}/remind")
def remind_service_ticket(
    ticket_id: int, payload: TicketReminder,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    TicketService.remind(db, ticket, actor_id=current_user.id, reason=payload.reason)
    _commit(db)
    owner = db.get(User, ticket.current_owner_id) if ticket.current_owner_id else None
    if not owner:
        notification = {
            "provider": "wecom_app",
            "status": "skipped",
            "accepted": False,
            "delivered": False,
            "message": "催办已记录，但当前工单尚未分派负责人",
        }
    elif not owner.wecom_userid:
        notification = {
            "provider": "wecom_app",
            "status": "skipped",
            "accepted": False,
            "delivered": False,
            "message": f"催办已记录，但负责人 {owner.display_name} 尚未绑定企业微信 UserID",
        }
    else:
        priority_labels = {"low": "低", "normal": "普通", "high": "加急", "urgent": "紧急"}
        due_text = ticket.due_at.astimezone().strftime("%Y-%m-%d %H:%M") if ticket.due_at else "未设置"
        message = "\n".join([
            "【服务工单催办】",
            "",
            f"工单：{ticket.ticket_no}",
            f"标题：{ticket.title}",
            f"优先级：{priority_labels.get(ticket.priority, ticket.priority)}",
            f"处理时限：{due_text}",
            f"催办人：{current_user.display_name}",
            f"催办原因：{payload.reason}",
            "",
            "请尽快处理并更新工单状态。",
        ])
        try:
            notification = get_wecom_service().send_app_text(owner.wecom_userid, message)
        except WeComAPIError as exc:
            notification = {
                "provider": "wecom_app",
                "status": "failed",
                "accepted": False,
                "delivered": False,
                "errcode": exc.errcode,
                "recipient_userid": owner.wecom_userid,
                "message": str(exc),
            }
    TicketService.record_reminder_notification(
        db,
        ticket,
        actor_id=current_user.id,
        result=notification,
    )
    _commit(db)
    response = ServiceTicketRead.model_validate(ticket).model_dump()
    response["wecom_notification"] = notification
    return ok(response)


@router.patch("/service-tickets/{ticket_id}/description")
def update_service_ticket_description(
    ticket_id: int,
    payload: TicketDescriptionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    TicketService.update_description(db, ticket, payload, actor_id=current_user.id)
    _commit(db)
    db.refresh(ticket)
    return ok(ServiceTicketRead.model_validate(ticket))


@router.get("/specialist-escalations")
def list_specialist_escalations(
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(SpecialistEscalation).order_by(SpecialistEscalation.created_at.desc())
    accessible_ticket_ids = scope_service_tickets(select(ServiceTicket.id), current_user)
    specialist_group_ids = select(ProcessingGroupMember.group_id).join(
        ProcessingGroup, ProcessingGroup.id == ProcessingGroupMember.group_id
    ).where(
        ProcessingGroupMember.user_id == current_user.id,
        ProcessingGroup.enabled.is_(True),
        ProcessingGroup.group_type == "specialist",
    )
    stmt = stmt.where(or_(
        SpecialistEscalation.service_ticket_id.in_(accessible_ticket_ids),
        SpecialistEscalation.assigned_specialist_id == current_user.id,
        SpecialistEscalation.specialist_group_id.in_(specialist_group_ids),
    ))
    if status:
        stmt = stmt.where(SpecialistEscalation.status == status)
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/specialist-escalations", status_code=201)
def create_specialist_escalation(
    payload: SpecialistEscalationCreate, db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    ticket = db.get(ServiceTicket, payload.service_ticket_id)
    if not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    require_service_ticket_access(db, ticket, current_user)
    escalation = TicketService.create_escalation(db, payload, actor_id=current_user.id)
    _commit(db)
    db.refresh(escalation)
    return ok(escalation)


@router.patch("/specialist-escalations/{escalation_id}")
def update_specialist_escalation(
    escalation_id: int, payload: SpecialistEscalationUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(require_authenticated_user),
) -> dict:
    escalation = db.get(SpecialistEscalation, escalation_id)
    if not escalation:
        raise BusinessError("升级记录不存在", code="escalation_not_found", status_code=404)
    ticket = db.get(ServiceTicket, escalation.service_ticket_id)
    if not ticket or ticket.deleted_at is not None:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    specialist_group_member = False
    if escalation.specialist_group_id:
        specialist_group_member = bool(db.scalar(
            select(ProcessingGroupMember.id)
            .join(ProcessingGroup, ProcessingGroup.id == ProcessingGroupMember.group_id)
            .where(
                ProcessingGroupMember.group_id == escalation.specialist_group_id,
                ProcessingGroupMember.user_id == current_user.id,
                ProcessingGroup.enabled.is_(True),
                ProcessingGroup.group_type == "specialist",
            )
        ))
    if (
        current_user.role not in {"admin", "manager"}
        and escalation.assigned_specialist_id != current_user.id
        and not specialist_group_member
    ):
        raise BusinessError("仅指定专员或专员组成员可更新升级流程", code="escalation_update_denied", status_code=403)
    TicketService.update_escalation(db, escalation, payload, actor_id=current_user.id)
    _commit(db)
    return ok(escalation)


@router.get("/quotes")
def list_quotes(
    repair_order_id: int | None = None,
    service_ticket_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(Quote).options(selectinload(Quote.items)).order_by(Quote.created_at.desc())
    stmt = scope_quotes(stmt, current_user)
    if repair_order_id:
        stmt = stmt.where(Quote.repair_order_id == repair_order_id)
    if service_ticket_id:
        stmt = stmt.where(Quote.service_ticket_id == service_ticket_id)
    return ok([QuoteRead.model_validate(x) for x in db.scalars(stmt.limit(500)).unique()])


@router.get("/quotes/{quote_id}")
def quote_detail(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    quote = db.scalar(select(Quote).where(
        Quote.id == quote_id,
        Quote.deleted_at.is_(None),
    ).options(selectinload(Quote.items)))
    if not quote:
        raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
    require_quote_access(db, quote, current_user)
    return ok(QuoteRead.model_validate(quote))


@router.post("/quotes", status_code=201)
def create_quote(
    payload: QuoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    if payload.repair_order_id:
        order = db.get(RepairOrder, payload.repair_order_id)
        if not order:
            raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
        require_order_access(db, order, current_user)
    else:
        ticket = db.get(ServiceTicket, payload.service_ticket_id)
        if not ticket:
            raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
        require_service_ticket_access(db, ticket, current_user)
    quote = QuoteService.create_version(db, payload)
    _commit(db)
    quote = db.scalar(select(Quote).where(Quote.id == quote.id).options(selectinload(Quote.items)))
    return ok(QuoteRead.model_validate(quote))


@router.delete("/quotes/{quote_id}")
def delete_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    quote = db.get(Quote, quote_id)
    if not quote or quote.deleted_at is not None:
        raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
    order_id = quote.repair_order_id
    record = delete_resource(db, "quote", quote_id, user=current_user)
    if order_id:
        order = db.get(RepairOrder, order_id)
        if order and order.deleted_at is None:
            QuoteService.recalculate_order_total(db, order)
    _commit(db)
    return ok(_deletion_payload(record))


@router.get("/trash")
def trash(
    db: Session = Depends(get_db),
    _current_user: User = Depends(admin_access),
) -> dict:
    return ok(list_deleted_records(db))


@router.post("/trash/{deletion_id}/restore")
def restore_from_trash(
    deletion_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    record = restore_record(db, deletion_id, user=current_user)
    if record.resource_type == "finance_transaction":
        transaction = db.get(FinanceTransaction, record.resource_id)
        if transaction and transaction.repair_order_id:
            order = db.get(RepairOrder, transaction.repair_order_id)
            if order:
                RepairOrderService.recalculate_finance(db, order)
    elif record.resource_type == "quote":
        quote = db.get(Quote, record.resource_id)
        if quote and quote.repair_order_id:
            order = db.get(RepairOrder, quote.repair_order_id)
            if order and order.deleted_at is None:
                QuoteService.recalculate_order_total(db, order)
    elif record.resource_type == "repair_order":
        order = db.get(RepairOrder, record.resource_id)
        if order and order.deleted_at is None:
            QuoteService.recalculate_order_total(db, order)
            RepairOrderService.recalculate_finance(db, order)
    _commit(db)
    return ok(_deletion_payload(record))


@router.post("/orders/{order_id}/recommended-quote", status_code=201)
def create_recommended_quote(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    order = db.get(RepairOrder, order_id)
    if not order:
        raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
    require_order_access(db, order, current_user)
    payload = QuoteRecommendationService.build(db, order_id)
    quote = QuoteService.create_version(db, payload)
    _commit(db)
    quote = db.scalar(select(Quote).where(Quote.id == quote.id).options(selectinload(Quote.items)))
    return ok({"quote": QuoteRead.model_validate(quote), "auto_generated": True, "message": "仅生成草稿，必须人工复核后再发送或确认"})


@router.post("/quotes/{quote_id}/confirm")
def confirm_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(
        selectinload(Quote.items), selectinload(Quote.repair_order), selectinload(Quote.service_ticket)
    ))
    if not quote:
        raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
    require_quote_access(db, quote, current_user)
    QuoteService.confirm(db, quote)
    _commit(db)
    return ok(QuoteRead.model_validate(quote))


@router.post("/quotes/{quote_id}/pdf")
def generate_quote_pdf(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(
        selectinload(Quote.items),
        selectinload(Quote.repair_order).selectinload(RepairOrder.customer),
        selectinload(Quote.repair_order).selectinload(RepairOrder.device),
        selectinload(Quote.service_ticket).selectinload(ServiceTicket.customer),
        selectinload(Quote.service_ticket).selectinload(ServiceTicket.device),
    ))
    if not quote:
        raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
    require_quote_access(db, quote, current_user)
    path = PdfReportService(brand_name=load_brand_name(db)).quote(quote)
    _register_quote_pdf(db, quote, path, uploaded_by=current_user.id)
    _commit(db)
    return ok({"path": str(path), "download_url": f"/api/files/report/{path.name}"})


@router.post("/quick-entry", status_code=201)
def quick_entry(
    payload: QuickEntryCreate, background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    if payload.send_email and not payload.email:
        raise BusinessError("勾选发送邮件时必须填写客户邮箱", code="email_required")
    if idempotency_key:
        existing_order = db.scalar(select(RepairOrder).where(
            RepairOrder.source_request_key == idempotency_key,
        ))
        if existing_order:
            require_order_access(db, existing_order, current_user)
    customer, device, order, quote, repeated = QuickEntryService.create(
        db, payload, request_key=idempotency_key, created_by=current_user.id
    )
    require_order_access(db, order, current_user)
    _commit(db, "快捷录入数据冲突，请检查手机号、序列号或重复提交标识")
    quote = db.scalar(select(Quote).where(Quote.id == quote.id).options(
        selectinload(Quote.items),
        selectinload(Quote.repair_order).selectinload(RepairOrder.customer),
        selectinload(Quote.repair_order).selectinload(RepairOrder.device),
    ))
    pdf_info = None
    email_info = None
    if payload.generate_pdf or payload.send_email:
        path = PdfReportService(brand_name=load_brand_name(db)).quote(quote)
        _register_quote_pdf(db, quote, path, uploaded_by=current_user.id)
        _commit(db)
        pdf_info = {"path": str(path), "download_url": f"/api/files/report/{path.name}"}
        if payload.send_email and not repeated:
            email_config = load_email_config(db)
            subject = f"{email_config.from_name}服务报价单 - {quote.repair_order.device.model} ({quote.quote_no})"
            delivery, task = queue_quote_email(db, quote, recipient=payload.email, subject=subject, message=payload.customer_notes, attachment_path=str(path))
            _commit(db)
            background_tasks.add_task(send_quote_email_task, delivery.id)
            email_info = {"delivery_id": delivery.id, "task_id": task.id, "status": "queued"}
    return ok({
        "customer": CustomerRead.model_validate(customer), "device": DeviceRead.model_validate(device),
        "order": _order_read_for_user(order, current_user), "quote": QuoteRead.model_validate(quote),
        "pdf": pdf_info, "email": email_info, "repeated": repeated,
    })


@router.get("/email/config-status")
def get_email_config_status(db: Session = Depends(get_db)) -> dict:
    return ok(email_config_status(db))


@router.get("/email/config", dependencies=[Depends(admin_access)])
def get_email_config(db: Session = Depends(get_db)) -> dict:
    return ok(safe_email_config(load_email_config(db)))


@router.put("/email/config", dependencies=[Depends(admin_access)])
def update_email_config(payload: EmailConfigUpdate, db: Session = Depends(get_db)) -> dict:
    return ok(safe_email_config(save_email_config(db, payload)))


@router.get("/email/deliveries")
def list_email_deliveries(
    quote_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(EmailDelivery).order_by(EmailDelivery.created_at.desc())
    if not is_admin(current_user):
        stmt = stmt.where(EmailDelivery.quote_id.in_(scope_quotes(select(Quote.id), current_user)))
    if quote_id:
        stmt = stmt.where(EmailDelivery.quote_id == quote_id)
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/quotes/{quote_id}/email", status_code=202)
def send_quote_email(
    quote_id: int,
    payload: EmailSendRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).options(
        selectinload(Quote.items),
        selectinload(Quote.repair_order).selectinload(RepairOrder.customer),
        selectinload(Quote.repair_order).selectinload(RepairOrder.device),
    ))
    if not quote:
        raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
    require_quote_access(db, quote, current_user)
    if not quote.repair_order:
        raise BusinessError("服务工单报价请使用统一邮件发送入口", code="unified_email_required")
    recipient = payload.recipient or quote.repair_order.customer.email
    if not recipient:
        raise BusinessError("客户未填写邮箱", code="email_required")
    path = PdfReportService(brand_name=load_brand_name(db)).quote(quote)
    _register_quote_pdf(db, quote, path, uploaded_by=current_user.id)
    email_config = load_email_config(db)
    subject = payload.subject or f"{email_config.from_name}服务报价单 - {quote.repair_order.device.model} ({quote.quote_no})"
    delivery, task = queue_quote_email(db, quote, recipient=recipient, subject=subject, message=payload.message, attachment_path=str(path))
    _commit(db)
    background_tasks.add_task(send_quote_email_task, delivery.id)
    return ok({"delivery_id": delivery.id, "task_id": task.id, "status": "queued", "mode": email_config.mode})


@router.get("/outbound-calls")
def list_outbound_calls(
    customer_id: int | None = None,
    service_ticket_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(OutboundCall).order_by(OutboundCall.created_at.desc())
    if current_user.role not in {"admin", "manager"}:
        accessible_order_ids = scope_orders(select(RepairOrder.id), current_user)
        accessible_ticket_ids = scope_service_tickets(select(ServiceTicket.id), current_user)
        stmt = stmt.where(or_(
            OutboundCall.repair_order_id.in_(accessible_order_ids),
            OutboundCall.service_ticket_id.in_(accessible_ticket_ids),
            (
                OutboundCall.repair_order_id.is_(None)
                & OutboundCall.service_ticket_id.is_(None)
                & or_(
                    OutboundCall.assigned_to == current_user.id,
                    OutboundCall.created_by == current_user.id,
                )
            ),
        ))
    if customer_id:
        stmt = stmt.where(OutboundCall.customer_id == customer_id)
    if service_ticket_id:
        stmt = stmt.where(OutboundCall.service_ticket_id == service_ticket_id)
    if status:
        stmt = stmt.where(OutboundCall.status == status)
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/outbound-calls", status_code=201)
def create_outbound_call(
    payload: OutboundCallCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    if payload.service_ticket_id:
        ticket = db.get(ServiceTicket, payload.service_ticket_id)
        if not ticket:
            raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
        require_service_ticket_access(db, ticket, current_user)
    if payload.repair_order_id:
        order = db.get(RepairOrder, payload.repair_order_id)
        if not order:
            raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
        require_order_access(db, order, current_user)
    call = create_call(db, payload, current_user)
    _commit(db)
    db.refresh(call)
    return ok(call)


@router.post("/outbound-calls/{call_id}/complete")
def complete_outbound_call(
    call_id: int,
    payload: OutboundCallComplete,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    existing_call = db.get(OutboundCall, call_id)
    if not existing_call:
        raise BusinessError("外呼任务不存在", code="call_not_found", status_code=404)
    if current_user.role == "call_operator" and existing_call.assigned_to != current_user.id:
        raise BusinessError(
            "话务账号只能登记分配给本人的外呼任务",
            code="call_access_denied",
            status_code=403,
        )
    _require_call_access(db, existing_call, current_user)
    call = complete_call(db, call_id, payload, current_user)
    _commit(db)
    db.refresh(call)
    return ok(call)


@router.get("/email/templates")
def list_email_templates(
    include_disabled: bool = False,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    if (include_disabled or include_deleted) and not is_admin(current_user):
        raise BusinessError("只有管理员可以查看停用或已删除模板", code="permission_denied", status_code=403)
    return ok(list_email_template_library(
        db,
        include_disabled=include_disabled,
        include_deleted=include_deleted,
    ))


@router.get("/email/template-metadata")
def get_email_template_metadata(
    _current_user: User = Depends(require_authenticated_user),
) -> dict:
    return ok(email_template_library_metadata())


@router.post("/email/templates", status_code=201)
def create_email_template(
    payload: EmailTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    template = create_custom_email_template(db, payload, current_user)
    _commit(db)
    db.refresh(template)
    return ok(custom_template_payload(template))


@router.patch("/email/templates/{template_type}")
def update_email_template(
    template_type: str,
    payload: EmailTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    template = update_custom_email_template(db, template_type, payload, current_user)
    _commit(db)
    db.refresh(template)
    return ok(custom_template_payload(template))


@router.delete("/email/templates/{template_type}")
def delete_email_template(
    template_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    template = delete_custom_email_template(db, template_type, current_user)
    _commit(db)
    db.refresh(template)
    return ok(custom_template_payload(template))


@router.post("/email/templates/{template_type}/restore")
def restore_email_template(
    template_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    template = restore_custom_email_template(db, template_type, current_user)
    _commit(db)
    db.refresh(template)
    return ok(custom_template_payload(template))


@router.post("/email/preview")
def preview_outbound_email(
    payload: EmailPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    context = resolve_email_context(
        db,
        service_ticket_id=payload.service_ticket_id,
        repair_order_id=payload.repair_order_id,
        quote_id=payload.quote_id,
    )
    if context.ticket:
        require_service_ticket_access(db, context.ticket, current_user)
    if context.order:
        require_order_access(db, context.order, current_user)
    return ok(render_email_preview(
        payload.template_type,
        context,
        brand=load_email_config(db).from_name,
        sender_name=current_user.display_name,
        db=db,
    ))


@router.get("/outbound-emails")
def list_outbound_emails(
    service_ticket_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(OutboundEmail).order_by(OutboundEmail.created_at.desc())
    if not is_admin(current_user):
        accessible_order_ids = scope_orders(select(RepairOrder.id), current_user)
        accessible_ticket_ids = scope_service_tickets(select(ServiceTicket.id), current_user)
        stmt = stmt.where(or_(
            OutboundEmail.repair_order_id.in_(accessible_order_ids),
            OutboundEmail.service_ticket_id.in_(accessible_ticket_ids),
        ))
    if service_ticket_id:
        stmt = stmt.where(OutboundEmail.service_ticket_id == service_ticket_id)
    if status:
        stmt = stmt.where(OutboundEmail.status == status)
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/outbound-emails", status_code=202)
def send_outbound_email(
    payload: OutboundEmailCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    context = resolve_email_context(
        db,
        service_ticket_id=payload.service_ticket_id,
        repair_order_id=payload.repair_order_id,
        quote_id=payload.quote_id,
    )
    if context.ticket:
        require_service_ticket_access(db, context.ticket, current_user)
    if context.order:
        require_order_access(db, context.order, current_user)
    config = load_email_config(db)
    delivery, task = queue_outbound_email(db, payload, current_user, from_name=config.from_name)
    _commit(db)
    background_tasks.add_task(send_outbound_email_task, delivery.id)
    return ok({
        "email_id": delivery.id,
        "email_no": delivery.email_no,
        "task_id": task.id,
        "status": delivery.status,
        "mode": config.mode,
    })


@router.post("/outbound-emails/{email_id}/retry", status_code=202)
def retry_outbound_email(
    email_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    delivery = db.get(OutboundEmail, email_id)
    if not delivery:
        raise BusinessError("外发邮件不存在", code="email_not_found", status_code=404)
    if delivery.service_ticket_id:
        require_service_ticket_access(db, db.get(ServiceTicket, delivery.service_ticket_id), current_user)
    if delivery.repair_order_id:
        require_order_access(db, db.get(RepairOrder, delivery.repair_order_id), current_user)
    if delivery.status not in {"failed", "retry_wait"}:
        raise BusinessError("只有失败或等待重试的邮件可以重试", code="email_retry_not_allowed", status_code=409)
    if delivery.attempts >= delivery.max_attempts:
        raise BusinessError("邮件已达到最大重试次数", code="email_retry_exhausted", status_code=409)
    delivery.status = "queued"
    delivery.next_retry_at = None
    task = db.get(TaskRecord, delivery.task_record_id) if delivery.task_record_id else None
    if task:
        task.status, task.progress, task.message = "queued", 0, None
    _commit(db)
    background_tasks.add_task(send_outbound_email_task, delivery.id)
    return ok({"email_id": delivery.id, "status": "queued", "attempts": delivery.attempts})


@router.get("/inventory/items")
def list_inventory(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    result = []
    for item in db.scalars(
        select(InventoryItem)
        .where(InventoryItem.deleted_at.is_(None))
        .order_by(InventoryItem.name)
        .limit(1000)
    ):
        payload = InventoryItemRead.model_validate(item).model_dump(mode="json")
        if current_user.role not in INVENTORY_COST_ROLES:
            payload.pop("purchase_price", None)
            payload.pop("supplier_id", None)
        result.append(payload)
    return ok(result)


@router.post("/inventory/items", status_code=201, dependencies=[Depends(inventory_access)])
def create_inventory(payload: InventoryItemCreate, db: Session = Depends(get_db)) -> dict:
    item = InventoryItem(**payload.model_dump())
    db.add(item)
    _commit(db, "库存 SKU 已存在")
    db.refresh(item)
    return ok(InventoryItemRead.model_validate(item))


@router.patch(
    "/inventory/items/{item_id}/client-visibility",
    dependencies=[Depends(inventory_access)],
)
def update_inventory_client_visibility(
    item_id: int,
    payload: InventoryClientVisibilityUpdate,
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(
        select(InventoryItem).where(
            InventoryItem.id == item_id, InventoryItem.deleted_at.is_(None)
        )
    )
    if not item:
        raise BusinessError("库存项目不存在", code="inventory_not_found", status_code=404)
    item.client_visible = payload.client_visible
    _commit(db)
    return ok(
        {
            "id": item.id,
            "client_visible": item.client_visible,
            "message": "已允许客户端展示" if item.client_visible else "已设为后台专用物料",
        }
    )


@router.get("/inventory/transactions", dependencies=[Depends(inventory_access)])
def list_stock_transactions(repair_order_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    stmt = select(InventoryTransaction).order_by(InventoryTransaction.created_at.desc())
    if repair_order_id:
        stmt = stmt.where(InventoryTransaction.repair_order_id == repair_order_id)
    return ok([InventoryTransactionRead.model_validate(x) for x in db.scalars(stmt.limit(500))])


@router.post("/inventory/transactions", status_code=201, dependencies=[Depends(inventory_access)])
def stock_change(payload: StockChange, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db)) -> dict:
    if idempotency_key:
        existing = db.scalar(select(InventoryTransaction).where(InventoryTransaction.idempotency_key == idempotency_key))
        if existing:
            if not _same_stock_request(existing, payload):
                raise BusinessError(
                    "幂等键已用于不同的库存请求",
                    code="idempotency_key_reused",
                    status_code=409,
                )
            return ok(InventoryTransactionRead.model_validate(existing))
    tx = InventoryService.change_stock(db, payload)
    tx.idempotency_key = idempotency_key
    _commit(db)
    return ok(InventoryTransactionRead.model_validate(tx))


@router.get("/finance", dependencies=[Depends(finance_access)])
def list_finance(repair_order_id: int | None = None, q: str = "", db: Session = Depends(get_db)) -> dict:
    stmt = select(FinanceTransaction).where(FinanceTransaction.deleted_at.is_(None)).order_by(FinanceTransaction.paid_at.desc())
    if repair_order_id:
        stmt = stmt.where(FinanceTransaction.repair_order_id == repair_order_id)
    if q.strip():
        term = f"%{q.strip()[:100]}%"
        matching_orders = select(RepairOrder.id).where(RepairOrder.order_no.like(term))
        matching_quotes = select(Quote.id).where(Quote.quote_no.like(term))
        stmt = stmt.where(or_(
            FinanceTransaction.description.like(term),
            FinanceTransaction.repair_order_id.in_(matching_orders),
            FinanceTransaction.quote_id.in_(matching_quotes),
        ))
    return ok([FinanceRead.model_validate(x) for x in db.scalars(stmt.limit(500))])


@router.post("/finance", status_code=201, dependencies=[Depends(finance_access)])
def create_finance(payload: FinanceCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db)) -> dict:
    if idempotency_key:
        existing = db.scalar(select(FinanceTransaction).where(
            FinanceTransaction.idempotency_key == idempotency_key,
        ))
        if existing:
            if existing.deleted_at is not None:
                raise BusinessError(
                    "幂等键对应的财务流水已删除",
                    code="finance_not_found",
                    status_code=404,
                )
            if not _same_finance_request(existing, payload):
                raise BusinessError(
                    "幂等键已用于不同的财务请求",
                    code="idempotency_key_reused",
                    status_code=409,
                )
            return ok(FinanceRead.model_validate(existing))
    tx = FinanceService.create(db, payload)
    tx.idempotency_key = idempotency_key
    _commit(db)
    db.refresh(tx)
    return ok(FinanceRead.model_validate(tx))


@router.patch("/finance/{transaction_id}", dependencies=[Depends(finance_access)])
def update_finance(transaction_id: int, payload: FinanceUpdate, db: Session = Depends(get_db)) -> dict:
    tx = db.get(FinanceTransaction, transaction_id)
    if not tx:
        raise BusinessError("财务流水不存在", code="finance_not_found", status_code=404)
    tx = FinanceService.update(db, tx, payload)
    _commit(db)
    db.refresh(tx)
    return ok(FinanceRead.model_validate(tx))


@router.get("/attachments")
def list_attachments(
    repair_order_id: int | None = None,
    customer_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(Attachment).order_by(Attachment.created_at.desc())
    if repair_order_id:
        _active_order(db, repair_order_id, current_user)
        stmt = stmt.where(Attachment.repair_order_id == repair_order_id)
    elif current_user.role not in {"admin", "manager"}:
        accessible_order_ids = scope_orders(select(RepairOrder.id), current_user)
        stmt = stmt.where(or_(
            Attachment.repair_order_id.in_(accessible_order_ids),
            (Attachment.repair_order_id.is_(None) & (Attachment.uploaded_by == current_user.id)),
        ))
    if customer_id:
        stmt = stmt.where(Attachment.customer_id == customer_id)
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/attachments", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...), repair_order_id: int | None = None, customer_id: int | None = None,
    attachment_type: str = "other", db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    if repair_order_id:
        order = _active_order(db, repair_order_id, current_user)
        if customer_id is not None and customer_id != order.customer_id:
            raise BusinessError("附件客户与工单客户不一致", code="attachment_customer_mismatch", status_code=409)
        customer_id = order.customer_id
    elif customer_id:
        customer = db.get(Customer, customer_id)
        if not customer or customer.deleted_at is not None:
            raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    stored = LocalStorageService().save_bytes(file.filename or "attachment", await file.read(settings.max_upload_bytes + 1))
    attachment = Attachment(
        repair_order_id=repair_order_id,
        customer_id=customer_id,
        attachment_type=attachment_type,
        original_filename=stored.original_filename,
        storage_path=stored.storage_path,
        content_type=stored.content_type,
        file_size=stored.file_size,
        sha256=stored.sha256,
        uploaded_by=current_user.id,
    )
    db.add(attachment)
    _commit(db)
    db.refresh(attachment)
    return ok(attachment)


@router.get("/files/attachment/{attachment_id}")
def download_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> FileResponse:
    attachment = db.get(Attachment, attachment_id)
    if not attachment:
        raise BusinessError("附件不存在", code="attachment_not_found", status_code=404)
    _require_attachment_access(db, attachment, current_user)
    path = Path(attachment.storage_path)
    if not path.is_absolute():
        path = LocalStorageService().absolute_path(attachment.storage_path)
    if not path.is_file():
        raise BusinessError("附件文件已丢失", code="attachment_file_missing", status_code=404)
    return FileResponse(
        path,
        media_type=safe_attachment_content_type(attachment.original_filename),
        filename=attachment.original_filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/files/report/{filename}")
def download_report(
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> FileResponse:
    configured_root = PdfReportService().report_dir
    root = configured_root.resolve()
    path = (root / Path(filename).name).resolve()
    if root not in path.parents or not path.is_file():
        raise BusinessError("报告不存在", code="report_not_found", status_code=404)
    legacy_path = configured_root / Path(filename).name
    attachment = db.scalar(select(Attachment).where(Attachment.storage_path.in_({
        str(path),
        str(legacy_path),
    })))
    if attachment:
        _require_attachment_access(db, attachment, current_user)
    elif current_user.role not in {"admin", "manager"}:
        raise BusinessError("无权访问该报告", code="report_access_denied", status_code=403)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.post("/flight-logs", status_code=202)
async def upload_flight_log(
    background_tasks: BackgroundTasks,
    repair_order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    order = _active_order(db, repair_order_id, current_user)
    stored = LocalStorageService().save_bytes(
        file.filename or "flight-log",
        await file.read(settings.max_upload_bytes + 1),
        folder="flight_logs",
        # 飞行记录可用 .txt 承载二进制；普通附件仍校验文本内容。
        allow_binary_text=True,
    )
    absolute = LocalStorageService().absolute_path(stored.storage_path)
    suffix = absolute.suffix.lower()
    file_type = {".txt": "dji_txt", ".dat": "dji_dat", ".ulg": "px4_ulog", ".bin": "ardupilot_bin", ".csv": "csv"}.get(suffix, "unknown")
    log = FlightLog(repair_order_id=order.id, device_id=order.device_id, original_filename=stored.original_filename, storage_path=str(absolute), file_type=file_type, file_size=stored.file_size, sha256=stored.sha256, parse_status="queued")
    db.add(log)
    db.flush()
    task = TaskRecord(task_no=make_no("TASK"), task_type="flight_log_parse", related_type="flight_log", related_id=log.id)
    db.add(task)
    _commit(db)
    background_tasks.add_task(parse_flight_log_task, log.id, task.id)
    return ok({"flight_log_id": log.id, "task_id": task.id, "status": "queued"})


@router.get("/flight-logs")
def list_flight_logs(
    repair_order_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(FlightLog).order_by(FlightLog.uploaded_at.desc())
    if repair_order_id:
        _active_order(db, repair_order_id, current_user)
        stmt = stmt.where(FlightLog.repair_order_id == repair_order_id)
    else:
        stmt = stmt.where(FlightLog.repair_order_id.in_(scope_orders(select(RepairOrder.id), current_user)))
    return ok(list(db.scalars(stmt.limit(500))))


@router.get("/tasks/{task_id}")
def task_status(task_id: int, db: Session = Depends(get_db)) -> dict:
    task = db.get(TaskRecord, task_id)
    if not task:
        raise BusinessError("任务不存在", code="task_not_found", status_code=404)
    return ok(task)


def _validate_sop_step_points(db: Session, step: DamageSopStepInput) -> None:
    point_map = db.get(PointMap, step.point_map_id) if step.point_map_id else None
    if step.point_map_id and not point_map:
        raise BusinessError("关联的点位图不存在", code="point_map_not_found", status_code=404)
    if step.point_marker_id:
        marker = db.get(PointMarker, step.point_marker_id)
        if not marker:
            raise BusinessError("关联的检测点位不存在", code="point_marker_not_found", status_code=404)
        if point_map and marker.point_map_id != point_map.id:
            raise BusinessError("检测点位不属于所选点位图", code="point_marker_map_mismatch", status_code=409)


@router.get("/damage-sop/templates")
def list_damage_sop_templates(
    brand: str | None = None,
    model: str | None = None,
    status: str | None = None,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(DamageSopTemplate).options(selectinload(DamageSopTemplate.steps)).order_by(
        DamageSopTemplate.updated_at.desc()
    )
    if brand:
        stmt = stmt.where(or_(DamageSopTemplate.brand == brand, DamageSopTemplate.brand.in_(["通用", "*"])))
    if model:
        stmt = stmt.where(or_(DamageSopTemplate.model_pattern == "*", DamageSopTemplate.model_pattern == "通用", func.lower(model).contains(func.lower(DamageSopTemplate.model_pattern))))
    if status:
        stmt = stmt.where(DamageSopTemplate.status == status)
    elif current_user.role not in {"admin", "manager", "technical_support"}:
        stmt = stmt.where(DamageSopTemplate.status == "published")
    templates = list(db.scalars(stmt.limit(500)))
    return ok([{"template": item, "step_count": len(item.steps)} for item in templates])


@router.get("/damage-sop/templates/{template_id}")
def get_damage_sop_template(template_id: int, db: Session = Depends(get_db)) -> dict:
    template = db.scalar(
        select(DamageSopTemplate)
        .where(DamageSopTemplate.id == template_id)
        .options(selectinload(DamageSopTemplate.steps))
    )
    if not template:
        raise BusinessError("SOP 模板不存在", code="damage_sop_not_found", status_code=404)
    map_ids = {step.point_map_id for step in template.steps if step.point_map_id}
    point_maps = list(db.scalars(select(PointMap).where(PointMap.id.in_(map_ids)))) if map_ids else []
    return ok({"template": template, "steps": template.steps, "point_maps": point_maps})


@router.post(
    "/damage-sop/templates", status_code=201,
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def create_damage_sop_template(
    payload: DamageSopTemplateCreate,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    if len({step.step_code for step in payload.steps}) != len(payload.steps):
        raise BusinessError("同一 SOP 内步骤编号不能重复", code="duplicate_sop_step_code", status_code=409)
    for step in payload.steps:
        _validate_sop_step_points(db, step)
    data = payload.model_dump(exclude={"steps"})
    if data["status"] == "published" and not payload.steps:
        raise BusinessError("没有步骤的 SOP 不能发布", code="damage_sop_has_no_steps", status_code=409)
    template = DamageSopTemplate(
        **data,
        created_by=current_user.id,
        published_at=datetime.now(timezone.utc) if data["status"] == "published" else None,
    )
    db.add(template)
    db.flush()
    for step in payload.steps:
        db.add(DamageSopStep(template_id=template.id, **step.model_dump()))
    _commit(db, "相同品牌、型号、标题和版本的 SOP 已存在")
    return get_damage_sop_template(template.id, db)


@router.post(
    "/damage-sop/templates/{template_id}/steps", status_code=201,
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def add_damage_sop_step(template_id: int, payload: DamageSopStepInput, db: Session = Depends(get_db)) -> dict:
    template = db.get(DamageSopTemplate, template_id)
    if not template:
        raise BusinessError("SOP 模板不存在", code="damage_sop_not_found", status_code=404)
    if template.status != "draft":
        raise BusinessError("已发布模板不可修改，请创建新版本", code="published_sop_immutable", status_code=409)
    _validate_sop_step_points(db, payload)
    step = DamageSopStep(template_id=template.id, **payload.model_dump())
    db.add(step)
    _commit(db, "步骤编号已存在")
    db.refresh(step)
    return ok(step)


@router.post(
    "/damage-sop/templates/{template_id}/publish",
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def publish_damage_sop_template(template_id: int, db: Session = Depends(get_db)) -> dict:
    template = db.scalar(
        select(DamageSopTemplate)
        .where(DamageSopTemplate.id == template_id)
        .options(selectinload(DamageSopTemplate.steps))
    )
    if not template:
        raise BusinessError("SOP 模板不存在", code="damage_sop_not_found", status_code=404)
    if not template.steps:
        raise BusinessError("没有步骤的 SOP 不能发布", code="damage_sop_has_no_steps", status_code=409)
    map_ids = {step.point_map_id for step in template.steps if step.point_map_id}
    if map_ids:
        unpublished = list(db.scalars(select(PointMap).where(PointMap.id.in_(map_ids), PointMap.status != "published")))
        if unpublished:
            raise BusinessError("SOP 引用了尚未发布的点位图", code="point_map_not_published", status_code=409)
    template.status = "published"
    template.published_at = datetime.now(timezone.utc)
    _commit(db)
    db.refresh(template)
    return ok(template)


@router.get("/point-maps")
def list_point_maps(
    brand: str | None = None,
    model: str | None = None,
    status: str | None = None,
    q: str | None = None,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(PointMap).options(selectinload(PointMap.markers)).order_by(PointMap.updated_at.desc())
    if brand:
        stmt = stmt.where(or_(PointMap.brand == brand, PointMap.brand.in_(["通用", "*"])))
    if model:
        stmt = stmt.where(or_(PointMap.model_pattern == "*", PointMap.model_pattern == "通用", func.lower(model).contains(func.lower(PointMap.model_pattern))))
    if status:
        stmt = stmt.where(PointMap.status == status)
    elif current_user.role not in {"admin", "manager", "technical_support"}:
        stmt = stmt.where(PointMap.status == "published")
    if q and (query := q.strip()):
        pattern = f"%{query}%"
        marker_match = or_(
            PointMarker.marker_code.ilike(pattern),
            PointMarker.label.ilike(pattern),
            PointMarker.component_ref.ilike(pattern),
            PointMarker.function_description.ilike(pattern),
            PointMarker.voltage_spec.ilike(pattern),
            PointMarker.current_spec.ilike(pattern),
        )
        stmt = stmt.where(or_(
            PointMap.title.ilike(pattern),
            PointMap.brand.ilike(pattern),
            PointMap.product_category.ilike(pattern),
            PointMap.series.ilike(pattern),
            PointMap.model_pattern.ilike(pattern),
            PointMap.module_name.ilike(pattern),
            PointMap.board_code.ilike(pattern),
            PointMap.source_reference.ilike(pattern),
            PointMap.markers.any(marker_match),
        ))
    maps = list(db.scalars(stmt.limit(500)))
    return ok([{
        "map": item,
        "marker_count": len(item.markers),
        "image_url": f"/api/files/attachment/{item.image_attachment_id}" if item.image_attachment_id else None,
        "source_file_url": f"/api/files/attachment/{item.source_attachment_id}" if item.source_attachment_id else None,
    } for item in maps])


@router.get("/point-maps/import-reference-library/status")
def point_map_import_status(db: Session = Depends(get_db)) -> dict:
    task = db.scalar(
        select(TaskRecord)
        .where(TaskRecord.task_type == "point_map_bulk_import")
        .order_by(TaskRecord.created_at.desc())
    )
    return ok(task)


@router.post(
    "/point-maps/import-reference-library", status_code=202,
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def start_point_map_library_import(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    root = settings.point_map_reference_root.resolve()
    if not root.is_dir():
        raise BusinessError(
            f"点位图资料目录不存在：{root}",
            code="point_map_reference_root_missing",
            status_code=409,
        )
    running = db.scalar(
        select(TaskRecord)
        .where(
            TaskRecord.task_type == "point_map_bulk_import",
            TaskRecord.status.in_(["queued", "running"]),
        )
        .order_by(TaskRecord.created_at.desc())
    )
    if running:
        return ok({"task_id": running.id, "status": running.status, "reused": True})
    task = TaskRecord(
        task_no=make_no("PMAP"),
        task_type="point_map_bulk_import",
        related_type="point_map_library",
        status="queued",
        progress=0,
        message=f"等待扫描：{root}",
    )
    db.add(task)
    _commit(db)
    db.refresh(task)
    background_tasks.add_task(import_point_map_library_task, task.id, current_user.id)
    return ok({"task_id": task.id, "status": task.status, "reused": False})


@router.get("/point-maps/{point_map_id}")
def get_point_map(point_map_id: int, db: Session = Depends(get_db)) -> dict:
    point_map = db.scalar(
        select(PointMap).where(PointMap.id == point_map_id).options(selectinload(PointMap.markers))
    )
    if not point_map:
        raise BusinessError("点位图不存在", code="point_map_not_found", status_code=404)
    return ok({
        "map": point_map,
        "markers": point_map.markers,
        "image_url": f"/api/files/attachment/{point_map.image_attachment_id}" if point_map.image_attachment_id else None,
        "source_file_url": f"/api/files/attachment/{point_map.source_attachment_id}" if point_map.source_attachment_id else None,
    })


@router.post(
    "/point-maps", status_code=201,
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def create_point_map(
    payload: PointMapCreate,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    point_map = PointMap(**payload.model_dump(), created_by=current_user.id)
    db.add(point_map)
    _commit(db, "相同品牌、型号、模块、标题和版本的点位图已存在")
    db.refresh(point_map)
    return get_point_map(point_map.id, db)


@router.post(
    "/point-maps/{point_map_id}/image", status_code=201,
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
async def upload_point_map_image(
    point_map_id: int,
    file: UploadFile = File(...),
    page_number: int = Form(1),
    auto_crop: bool = Form(True),
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    point_map = db.get(PointMap, point_map_id)
    if not point_map:
        raise BusinessError("点位图不存在", code="point_map_not_found", status_code=404)
    if point_map.status != "draft":
        raise BusinessError("已发布点位图不可替换图片，请创建新版本", code="published_point_map_immutable", status_code=409)
    filename = file.filename or "point-map"
    content = await file.read(settings.max_upload_bytes + 1)
    is_pdf = (file.content_type or "").lower() == "application/pdf" or Path(filename).suffix.lower() == ".pdf"
    is_image = (file.content_type or "").startswith("image/")
    if not is_pdf and not is_image:
        raise BusinessError("点位图只允许上传 PDF、PNG、JPG 或 WebP", code="point_map_image_required", status_code=415)
    storage = LocalStorageService()
    if is_pdf:
        source_stored = storage.save_bytes(filename, content, folder="point_map_sources")
        source_attachment = Attachment(
            attachment_type="point_map_source_pdf",
            original_filename=source_stored.original_filename,
            storage_path=source_stored.storage_path,
            content_type="application/pdf",
            file_size=source_stored.file_size,
            sha256=source_stored.sha256,
            uploaded_by=current_user.id,
        )
        db.add(source_attachment)
        db.flush()
        point_map.source_attachment_id = source_attachment.id
        point_map.source_page = page_number
        content = render_pdf_page_png(content, page_number, auto_crop=auto_crop)
        filename = f"{Path(filename).stem}-page-{page_number}.png"
        content_type = "image/png"
    else:
        content_type = file.content_type
    stored = storage.save_bytes(filename, content, folder="point_maps")
    attachment = Attachment(
        attachment_type="point_map_image",
        original_filename=stored.original_filename,
        storage_path=stored.storage_path,
        content_type=content_type,
        file_size=stored.file_size,
        sha256=stored.sha256,
        uploaded_by=current_user.id,
    )
    db.add(attachment)
    db.flush()
    point_map.image_attachment_id = attachment.id
    _commit(db)
    return get_point_map(point_map.id, db)


@router.post(
    "/point-maps/{point_map_id}/markers", status_code=201,
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def add_point_marker(point_map_id: int, payload: PointMarkerCreate, db: Session = Depends(get_db)) -> dict:
    point_map = db.get(PointMap, point_map_id)
    if not point_map:
        raise BusinessError("点位图不存在", code="point_map_not_found", status_code=404)
    marker = PointMarker(point_map_id=point_map.id, **payload.model_dump())
    db.add(marker)
    _commit(db, "点位编号已存在")
    db.refresh(marker)
    return ok(marker)


@router.patch(
    "/point-maps/{point_map_id}/markers/{marker_id}",
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def update_point_marker(
    point_map_id: int,
    marker_id: int,
    payload: PointMarkerUpdate,
    db: Session = Depends(get_db),
) -> dict:
    marker = db.get(PointMarker, marker_id)
    if not marker or marker.point_map_id != point_map_id:
        raise BusinessError("检测点位不存在", code="point_marker_not_found", status_code=404)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(marker, key, value)
    _commit(db)
    db.refresh(marker)
    return ok(marker)


@router.post(
    "/point-maps/{point_map_id}/publish",
    dependencies=[Depends(require_roles("admin", "manager", "technical_support"))],
)
def publish_point_map(point_map_id: int, db: Session = Depends(get_db)) -> dict:
    point_map = db.scalar(
        select(PointMap).where(PointMap.id == point_map_id).options(selectinload(PointMap.markers))
    )
    if not point_map:
        raise BusinessError("点位图不存在", code="point_map_not_found", status_code=404)
    if not point_map.image_attachment_id:
        raise BusinessError("点位图至少需要一张底图才能发布", code="point_map_incomplete", status_code=409)
    point_map.status = "published"
    _commit(db)
    db.refresh(point_map)
    return ok(point_map)


@router.get("/damage-assessments")
def list_damage_assessments(
    repair_order_id: int | None = None,
    status: str | None = None,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    accessible_orders = scope_orders(select(RepairOrder.id), current_user)
    stmt = select(DamageAssessment).where(
        DamageAssessment.repair_order_id.in_(accessible_orders),
        DamageAssessment.deleted_at.is_(None),
    ).order_by(
        DamageAssessment.created_at.desc()
    )
    if repair_order_id:
        stmt = stmt.where(DamageAssessment.repair_order_id == repair_order_id)
    if status:
        stmt = stmt.where(DamageAssessment.status == status)
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/damage-assessments", status_code=201)
def start_damage_assessment(
    payload: DamageAssessmentCreate,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    order = db.get(RepairOrder, payload.repair_order_id)
    if not order:
        raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
    require_order_access(db, order, current_user)
    device = db.get(DroneDevice, order.device_id)
    template = db.get(DamageSopTemplate, payload.template_id)
    if not template:
        raise BusinessError("SOP 模板不存在", code="damage_sop_not_found", status_code=404)
    if payload.operator_id not in {None, current_user.id} and current_user.role not in {"admin", "manager"}:
        raise BusinessError("不能代替其他人员开始定损", code="damage_operator_denied", status_code=403)
    assessment = create_assessment(
        db,
        order=order,
        device=device,
        template=template,
        operator_id=payload.operator_id or current_user.id,
    )
    _commit(db)
    return ok(assessment_detail(db, assessment))


@router.get("/damage-assessments/{assessment_id}")
def get_damage_assessment(
    assessment_id: int,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    assessment = load_assessment(db, assessment_id)
    order = db.get(RepairOrder, assessment.repair_order_id)
    require_order_access(db, order, current_user)
    return ok(assessment_detail(db, assessment))


@router.patch("/damage-assessments/{assessment_id}/results/{result_id}")
def update_damage_assessment_result(
    assessment_id: int,
    result_id: int,
    payload: DamageAssessmentResultUpdate,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    assessment = load_assessment(db, assessment_id)
    order = db.get(RepairOrder, assessment.repair_order_id)
    require_order_access(db, order, current_user)
    result = update_result(
        db,
        assessment=assessment,
        result_id=result_id,
        payload=payload,
        user_id=current_user.id,
    )
    _commit(db)
    db.refresh(result)
    return ok(result)


@router.post("/damage-assessments/{assessment_id}/complete")
def finish_damage_assessment(
    assessment_id: int,
    payload: DamageAssessmentComplete,
    current_user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    assessment = load_assessment(db, assessment_id)
    order = db.get(RepairOrder, assessment.repair_order_id)
    require_order_access(db, order, current_user)
    assessment = complete_assessment(db, assessment=assessment, payload=payload)
    _commit(db)
    db.refresh(assessment)
    return ok(assessment_detail(db, load_assessment(db, assessment.id)))


@router.get("/diagnoses")
def list_diagnoses(
    repair_order_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(Diagnosis).order_by(Diagnosis.created_at.desc())
    if repair_order_id:
        _active_order(db, repair_order_id, current_user)
        stmt = stmt.where(Diagnosis.repair_order_id == repair_order_id)
    else:
        stmt = stmt.where(Diagnosis.repair_order_id.in_(scope_orders(select(RepairOrder.id), current_user)))
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/diagnoses", status_code=201)
def create_diagnosis(
    payload: DiagnosisCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    _active_order(db, payload.repair_order_id, current_user)
    if payload.flight_log_id:
        flight_log = db.get(FlightLog, payload.flight_log_id)
        if not flight_log or flight_log.repair_order_id != payload.repair_order_id:
            raise BusinessError("飞行日志不属于该工单", code="flight_log_order_mismatch", status_code=409)
    diagnosis = Diagnosis(**payload.model_dump(), requires_human_confirmation=True)
    db.add(diagnosis)
    _commit(db)
    return ok(diagnosis)


@router.get("/calibrations")
def list_calibrations(
    repair_order_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(CalibrationRecord).order_by(CalibrationRecord.started_at.desc())
    if repair_order_id:
        _active_order(db, repair_order_id, current_user)
        stmt = stmt.where(CalibrationRecord.repair_order_id == repair_order_id)
    else:
        stmt = stmt.where(CalibrationRecord.repair_order_id.in_(scope_orders(select(RepairOrder.id), current_user)))
    return ok(list(db.scalars(stmt.limit(500))))


@router.get("/calibration/capabilities")
def calibration_capabilities(device_id: int, db: Session = Depends(get_db)) -> dict:
    device = db.get(DroneDevice, device_id)
    if not device:
        raise BusinessError("设备不存在", code="device_not_found", status_code=404)
    capability = gimbal_calibration_capability(brand=device.brand, model=device.model)
    capability["device_id"] = device.id
    capability["serial_number"] = device.serial_number
    return ok(capability)


@router.get("/calibration/lab/profiles")
def calibration_lab_profiles() -> dict:
    return ok({
        "engine": "service_gimbal_calibration_lab",
        "stage": "simulation_only",
        "live_execution_available": False,
        "profiles": list_profiles(),
        "safety_notice": "所有机型均处于研究档案阶段；本版本不会向真实设备发送标定指令。",
    })


@router.get("/calibration/lab/ports")
def calibration_lab_ports() -> dict:
    result = list_serial_ports()
    result.update({
        "discovery_only": True,
        "ports_opened": False,
        "live_execution_available": False,
    })
    return ok(result)


@router.get("/calibration/lab/devices")
def calibration_lab_devices() -> dict:
    return ok(discover_connected_dji_devices())


@router.post("/calibration/lab/simulate")
def simulate_gimbal_calibration(payload: CalibrationLabSimulationRequest) -> dict:
    try:
        engine = GimbalCalibrationEngine.for_profile(payload.profile_id)
        result = engine.simulate(CalibrationKind(payload.calibration_kind))
    except ValueError as exc:
        raise BusinessError(
            "未知或不受支持的标定研究档案",
            code="calibration_profile_not_supported",
            status_code=422,
        ) from exc
    return ok(result)


@router.post("/calibrations", status_code=201)
def create_calibration(
    payload: CalibrationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    data = payload.model_dump()
    device = db.get(DroneDevice, payload.device_id)
    if not device:
        raise BusinessError("设备不存在", code="device_not_found", status_code=404)
    order = _active_order(db, payload.repair_order_id, current_user)
    if order.device_id != device.id:
        raise BusinessError("标定设备与工单设备不一致", code="calibration_device_mismatch", status_code=409)
    if payload.operator_id not in {None, current_user.id} and current_user.role not in {"admin", "manager"}:
        raise BusinessError("不能代替其他人员登记标定", code="calibration_operator_denied", status_code=403)
    data["operator_id"] = payload.operator_id or current_user.id
    if "gimbal" in payload.calibration_type.lower() or "云台" in payload.calibration_type:
        supplied = payload.result_json or {}
        data["result_json"] = DJIOfficialWorkflowProvider().record(
            brand=device.brand,
            model=device.model,
            tool_name=payload.tool_name,
            before=supplied.get("before", {}),
            after=supplied.get("after", supplied),
            operator_id=payload.operator_id,
        )
    data["started_at"] = data["started_at"] or datetime.now(timezone.utc)
    record = CalibrationRecord(**data)
    db.add(record)
    _commit(db)
    return ok(record)


@router.get("/shipments")
def list_shipments(
    repair_order_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(Shipment).order_by(Shipment.created_at.desc())
    if repair_order_id:
        _active_order(db, repair_order_id, current_user)
        stmt = stmt.where(Shipment.repair_order_id == repair_order_id)
    else:
        stmt = stmt.where(Shipment.repair_order_id.in_(scope_orders(select(RepairOrder.id), current_user)))
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/shipments", status_code=201)
def create_shipment(
    payload: ShipmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    order = _active_order(db, payload.repair_order_id, current_user)
    manual = bool(payload.tracking_no)
    status = "created" if manual else "pending_submit"
    shipment = Shipment(
        **payload.model_dump(),
        provider="manual" if manual else "sf_express",
        logistics_status=status,
        last_synced_at=datetime.now(timezone.utc) if manual else None,
    )
    db.add(shipment)
    db.flush()
    db.add(ShipmentEvent(
        shipment_id=shipment.id,
        logistics_status=status,
        description="已登记人工运单" if manual else "顺丰接口尚未配置，建单请求已进入待发送队列",
        source="manual" if manual else "offline_queue",
        recorded_by=current_user.id,
    ))
    if not manual:
        db.add(TaskRecord(
            task_no=make_no("TASK"),
            task_type="sf_shipment_create",
            related_type="shipment",
            related_id=shipment.id,
            status="waiting_configuration",
            progress=0,
            message="等待顺丰账号与网络配置；不会伪造外部运单号。",
        ))
    _order_timeline(db, order.id, "shipment_created", f"物流记录已建立：{status}", current_user.id, {"shipment_id": shipment.id})
    _commit(db)
    db.refresh(shipment)
    return ok(shipment)


@router.get("/shipments/{shipment_id}/events")
def list_shipment_events(
    shipment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise BusinessError("物流记录不存在", code="shipment_not_found", status_code=404)
    _active_order(db, shipment.repair_order_id, current_user)
    return ok(list(db.scalars(
        select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment_id)
        .order_by(ShipmentEvent.occurred_at.desc())
    )))


@router.patch("/shipments/{shipment_id}")
def update_shipment(
    shipment_id: int,
    payload: ShipmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise BusinessError("物流记录不存在", code="shipment_not_found", status_code=404)
    _active_order(db, shipment.repair_order_id, current_user)
    if payload.tracking_no:
        shipment.tracking_no = payload.tracking_no.strip()
    if payload.external_order_no:
        shipment.external_order_no = payload.external_order_no.strip()
    shipment.logistics_status = payload.logistics_status
    shipment.last_synced_at = datetime.now(timezone.utc)
    event = ShipmentEvent(
        shipment_id=shipment.id,
        logistics_status=payload.logistics_status,
        location=payload.location,
        description=payload.description,
        source="manual",
        recorded_by=current_user.id,
        occurred_at=payload.occurred_at or datetime.now(timezone.utc),
    )
    db.add(event)
    _order_timeline(
        db,
        shipment.repair_order_id,
        "shipment_updated",
        f"物流状态更新为 {payload.logistics_status}",
        current_user.id,
        {"shipment_id": shipment.id, "location": payload.location},
    )
    _commit(db)
    return ok(shipment)


@router.get("/follow-ups")
def list_followups(
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    stmt = select(FollowUpTask).where(
        FollowUpTask.repair_order_id.in_(scope_orders(select(RepairOrder.id), current_user)),
        FollowUpTask.deleted_at.is_(None),
    ).order_by(
        case((FollowUpTask.status == "pending", 0), else_=1),
        FollowUpTask.scheduled_at,
        FollowUpTask.id,
    )
    if status:
        stmt = stmt.where(FollowUpTask.status == status)
    return ok(list(db.scalars(stmt.limit(500))))


@router.post("/follow-ups", status_code=201)
def create_followup(
    payload: FollowUpCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    order = _active_order(db, payload.repair_order_id, current_user)
    if order.customer_id != payload.customer_id:
        raise BusinessError("回访客户与工单不匹配", code="followup_customer_mismatch")
    task = FollowUpTask(**payload.model_dump(), status="pending")
    db.add(task)
    _order_timeline(db, order.id, "followup_planned", "已安排售后回访", current_user.id, {"scheduled_at": str(task.scheduled_at)})
    _commit(db)
    db.refresh(task)
    return ok(task)


@router.patch("/follow-ups/{task_id}")
def update_followup(
    task_id: int,
    payload: FollowUpUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    task = db.get(FollowUpTask, task_id)
    if not task or task.deleted_at is not None:
        raise BusinessError("回访任务不存在", code="followup_not_found", status_code=404)
    _active_order(db, task.repair_order_id, current_user)
    if task.status == "completed":
        raise BusinessError("已完成的回访任务不能重复提交", code="followup_already_completed", status_code=409)
    if payload.status == "completed" and not (payload.result or "").strip():
        raise BusinessError("完成回访时必须填写结果", code="followup_result_required")
    task.status, task.result, task.next_follow_up_at = payload.status, payload.result, payload.next_follow_up_at
    if payload.status == "completed":
        task.completed_at = datetime.now(timezone.utc)
        if payload.next_follow_up_at:
            db.add(FollowUpTask(
                repair_order_id=task.repair_order_id,
                customer_id=task.customer_id,
                follow_up_type=task.follow_up_type,
                scheduled_at=payload.next_follow_up_at,
                status="pending",
                content=f"上次回访结果：{payload.result}",
            ))
    _order_timeline(
        db,
        task.repair_order_id,
        "followup_updated",
        f"回访任务更新为 {payload.status}",
        current_user.id,
        {"result": payload.result, "next_follow_up_at": str(payload.next_follow_up_at or "")},
    )
    _commit(db)
    return ok(task)


@router.delete("/follow-ups/{task_id}")
def delete_followup(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    record = delete_resource(db, "follow_up_task", task_id, user=current_user)
    _commit(db)
    return ok(_deletion_payload(record))


@router.get("/settings", dependencies=[Depends(admin_access)])
def list_settings(db: Session = Depends(get_db)) -> dict:
    return ok(list(db.scalars(select(SystemSetting).where(SystemSetting.is_secret.is_(False)).order_by(SystemSetting.key))))


@router.put("/settings", dependencies=[Depends(admin_access)])
def upsert_setting(payload: SettingInput, db: Session = Depends(get_db)) -> dict:
    if any(part in payload.key.lower() for part in ("password", "secret", "token", "key")):
        raise BusinessError("密钥类配置必须使用对应的专用设置页面或环境变量", code="secret_setting_rejected")
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == payload.key))
    if setting:
        setting.value, setting.description = payload.value, payload.description
    else:
        setting = SystemSetting(**payload.model_dump(), is_secret=False)
        db.add(setting)
    _commit(db)
    return ok(setting)


@router.post("/orders/{order_id}/reports/{report_type}")
def generate_order_report(
    order_id: int,
    report_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    order = db.scalar(select(RepairOrder).where(RepairOrder.id == order_id).options(
        selectinload(RepairOrder.customer),
        selectinload(RepairOrder.device),
        selectinload(RepairOrder.quotes).selectinload(Quote.items),
    ))
    if not order:
        raise BusinessError("工单不存在", code="order_not_found", status_code=404)
    require_order_access(db, order, current_user)
    if report_type not in {"inspection", "completion"}:
        raise BusinessError("报告类型必须是 inspection 或 completion", code="invalid_report_type")
    path = PdfReportService(brand_name=load_brand_name(db)).repair_report(
        order, completed=report_type == "completion"
    )
    _register_order_report(
        db,
        order,
        path,
        attachment_type=f"repair_{report_type}_pdf",
        uploaded_by=current_user.id,
    )
    _commit(db)
    return ok({"path": str(path), "download_url": f"/api/files/report/{path.name}"})
