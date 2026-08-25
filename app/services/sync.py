from __future__ import annotations

import hashlib
import hmac
import json
import socket
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.core.inventory_quantity import inventory_quantity, inventory_quantity_text
from app.models.entities import (
    Customer,
    DroneDevice,
    FinanceTransaction,
    InventoryItem,
    InventoryTransaction,
    ProcessingGroup,
    Quote,
    QuoteItem,
    RepairOrder,
    RepairOrderStatusHistory,
    ServiceTicket,
    ServiceTicketCollaborator,
    ServiceTicketNote,
    ServiceTicketTimeline,
    SyncCanonicalRecord,
    SyncConflict,
    SyncEntityState,
    SyncNode,
    SyncOutboxEvent,
    SyncServerChange,
    Supplier,
    SystemSetting,
    User,
    utcnow,
)
from app.schemas.domain import (
    VALID_TICKET_TYPES,
    ReplacementTicketUpdate,
    StockChange,
    normalize_payment_url,
)
from app.services.finance import FinanceService
from app.services.inventory import InventoryService
from app.services.orders import RepairOrderService
from app.services.quotes import QuoteService, money as quote_money


SUPPORTED_ENTITY_TYPES = (
    "customer",
    "device",
    "repair_order",
    "service_ticket",
    "quote",
    "inventory_item",
    "inventory_transaction",
    "finance_transaction",
)
HOST_ONLY_ENTITY_TYPES: tuple[str, ...] = ()
INCOMING_EVENT_OPERATIONS = {"upsert", "history_import"}
ENTITY_RECORD_KEY_FIELDS = {
    "customer": "customer_no",
    "device": "sync_key",
    "repair_order": "order_no",
    "service_ticket": "ticket_no",
    "quote": "quote_no",
    "inventory_item": "sku",
    "inventory_transaction": "transaction_no",
    "finance_transaction": "transaction_no",
}
NODE_ID_KEY = "sync.node_id"
PULL_CURSOR_KEY = "sync.pull_cursor"


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(jsonable_encoder(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _iso(value: Any) -> Any:
    return jsonable_encoder(value)


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if not value or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_date(value: str | date | None) -> date | None:
    if not value or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _user_name(db: Session, user_id: int | None) -> str | None:
    user = db.get(User, user_id) if user_id else None
    return user.username if user else None


def _user_id(db: Session, username: str | None) -> int | None:
    return db.scalar(select(User.id).where(User.username == username)) if username else None


def _group_name(db: Session, group_id: int | None) -> str | None:
    group = db.get(ProcessingGroup, group_id) if group_id else None
    return group.name if group else None


def _group_id(db: Session, name: str | None) -> int | None:
    return db.scalar(select(ProcessingGroup.id).where(ProcessingGroup.name == name)) if name else None


def _device_key(db: Session, device: DroneDevice) -> str:
    if not device.sync_key:
        device.sync_key = str(uuid.uuid4())
        db.flush()
    return device.sync_key


def _customer_payload(row: Customer) -> dict[str, Any]:
    return {
        "customer_no": row.customer_no,
        "name": row.name,
        "phone": row.phone,
        "email": row.email,
        "wechat": row.wechat,
        "wecom_external_user_id": row.wecom_external_user_id,
        "wecom_group_id": row.wecom_group_id,
        "customer_type": row.customer_type,
        "company_name": row.company_name,
        "province": row.province,
        "city": row.city,
        "address": row.address,
        "notes": row.notes,
        "deleted_at": _iso(row.deleted_at),
        "deletion_batch_id": row.deletion_batch_id,
    }


def _device_payload(db: Session, row: DroneDevice) -> dict[str, Any]:
    customer = db.get(Customer, row.customer_id)
    return {
        "sync_key": _device_key(db, row),
        "customer_no": customer.customer_no if customer else None,
        "brand": row.brand,
        "model": row.model,
        "serial_number": row.serial_number,
        "activation_date": _iso(row.activation_date),
        "purchase_date": _iso(row.purchase_date),
        "warranty_status": row.warranty_status,
        "is_temporary": row.is_temporary,
        "remarks": row.remarks,
        "deleted_at": _iso(row.deleted_at),
        "deletion_batch_id": row.deletion_batch_id,
    }


def _order_payload(db: Session, row: RepairOrder) -> dict[str, Any]:
    customer = db.get(Customer, row.customer_id)
    device = db.get(DroneDevice, row.device_id)
    history = list(db.scalars(
        select(RepairOrderStatusHistory)
        .where(RepairOrderStatusHistory.repair_order_id == row.id)
        .order_by(RepairOrderStatusHistory.changed_at, RepairOrderStatusHistory.id)
    ))
    return {
        "order_no": row.order_no,
        "source_request_key": row.source_request_key,
        "customer_no": customer.customer_no if customer else None,
        "device_key": _device_key(db, device) if device else None,
        "fault_description": row.fault_description,
        "intake_condition": row.intake_condition,
        "intake_accessories": row.intake_accessories,
        "engineer_username": _user_name(db, row.engineer_id),
        "processing_group": _group_name(db, row.processing_group_id),
        "status": row.status,
        "priority": row.priority,
        "received_at": _iso(row.received_at),
        "expected_finish_at": _iso(row.expected_finish_at),
        "completed_at": _iso(row.completed_at),
        "internal_notes": row.internal_notes,
        "customer_notes": row.customer_notes,
        "deleted_at": _iso(row.deleted_at),
        "deletion_batch_id": row.deletion_batch_id,
        "status_history": [
            {
                "from_status": item.from_status,
                "to_status": item.to_status,
                "changed_by": _user_name(db, item.changed_by),
                "reason": item.reason,
                "changed_at": _iso(item.changed_at),
            }
            for item in history
        ],
    }


def _ticket_payload(db: Session, row: ServiceTicket) -> dict[str, Any]:
    customer = db.get(Customer, row.customer_id) if row.customer_id else None
    device = db.get(DroneDevice, row.device_id) if row.device_id else None
    order = db.get(RepairOrder, row.repair_order_id) if row.repair_order_id else None
    collaborators = list(db.scalars(
        select(ServiceTicketCollaborator)
        .where(ServiceTicketCollaborator.ticket_id == row.id)
        .order_by(ServiceTicketCollaborator.added_at, ServiceTicketCollaborator.id)
    ))
    notes = list(db.scalars(
        select(ServiceTicketNote)
        .where(ServiceTicketNote.ticket_id == row.id)
        .order_by(ServiceTicketNote.created_at, ServiceTicketNote.id)
    ))
    timeline = list(db.scalars(
        select(ServiceTicketTimeline)
        .where(ServiceTicketTimeline.ticket_id == row.id)
        .order_by(ServiceTicketTimeline.created_at, ServiceTicketTimeline.id)
    ))
    return {
        "ticket_no": row.ticket_no,
        "ticket_type": row.ticket_type,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "priority": row.priority,
        "customer_no": customer.customer_no if customer else None,
        "device_key": _device_key(db, device) if device else None,
        "order_no": order.order_no if order else None,
        "current_owner": _user_name(db, row.current_owner_id),
        "processing_group": _group_name(db, row.processing_group_id),
        "created_by": _user_name(db, row.created_by),
        "due_at": _iso(row.due_at),
        "first_response_at": _iso(row.first_response_at),
        "resolved_at": _iso(row.resolved_at),
        "closed_at": _iso(row.closed_at),
        "last_reminded_at": _iso(row.last_reminded_at),
        "reminder_count": row.reminder_count,
        "replacement_inspection_result": row.replacement_inspection_result,
        "trade_in_credit": str(row.trade_in_credit) if row.trade_in_credit is not None else None,
        "return_reference": row.return_reference,
        "outbound_to_customer_tracking_no": row.outbound_to_customer_tracking_no,
        "deleted_at": _iso(row.deleted_at),
        "deletion_batch_id": row.deletion_batch_id,
        "collaborators": [
            {
                "username": _user_name(db, item.user_id),
                "role": item.collaborator_role,
                "added_by": _user_name(db, item.added_by),
                "added_at": _iso(item.added_at),
            }
            for item in collaborators
        ],
        "notes": [
            {
                "visibility": item.visibility,
                "content": item.content,
                "author": _user_name(db, item.author_id),
                "created_at": _iso(item.created_at),
            }
            for item in notes
        ],
        "timeline": [
            {
                "event_type": item.event_type,
                "summary": item.summary,
                "from_status": item.from_status,
                "to_status": item.to_status,
                "details_json": item.details_json,
                "actor": _user_name(db, item.actor_id),
                "created_at": _iso(item.created_at),
            }
            for item in timeline
        ],
    }


def _quote_payload(db: Session, row: Quote) -> dict[str, Any]:
    order = db.get(RepairOrder, row.repair_order_id) if row.repair_order_id else None
    ticket = db.get(ServiceTicket, row.service_ticket_id) if row.service_ticket_id else None
    return {
        "quote_no": row.quote_no,
        "order_no": order.order_no if order else None,
        "ticket_no": ticket.ticket_no if ticket else None,
        "version": row.version,
        "status": row.status,
        "subtotal": str(row.subtotal),
        "discount": str(row.discount),
        "labor_fee": str(row.labor_fee),
        "shipping_fee": str(row.shipping_fee),
        "total_amount": str(row.total_amount),
        "assessment_result": row.assessment_result,
        "assessment_responsibility": row.assessment_responsibility,
        "repair_recommendation": row.repair_recommendation,
        "customer_notice": row.customer_notice,
        "payment_url": row.payment_url,
        "customer_confirmed_at": _iso(row.customer_confirmed_at),
        "deleted_at": _iso(row.deleted_at),
        "deletion_batch_id": row.deletion_batch_id,
        "items": [
            {
                "item_name": item.item_name,
                "specification": item.specification,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "cost_price": str(item.cost_price),
                "amount": str(item.amount),
                "item_type": item.item_type,
                "remarks": item.remarks,
                "sort_order": item.sort_order,
            }
            for item in sorted(row.items, key=lambda value: (value.sort_order, value.id))
        ],
    }


def _inventory_item_payload(db: Session, row: InventoryItem) -> dict[str, Any]:
    supplier = db.get(Supplier, row.supplier_id) if row.supplier_id else None
    return {
        "sku": row.sku,
        "name": row.name,
        "category": row.category,
        "compatible_models": row.compatible_models,
        "unit": row.unit,
        "purchase_price": str(row.purchase_price),
        "sale_price": str(row.sale_price),
        "stock_quantity": inventory_quantity_text(row.stock_quantity),
        "safety_stock": inventory_quantity_text(row.safety_stock),
        "supplier_name": supplier.name if supplier else None,
        "location": row.location,
        "enabled": row.enabled,
        "deleted_at": _iso(row.deleted_at),
        "deletion_batch_id": row.deletion_batch_id,
    }


def _inventory_transaction_payload(db: Session, row: InventoryTransaction) -> dict[str, Any]:
    item = db.get(InventoryItem, row.inventory_item_id)
    order = db.get(RepairOrder, row.repair_order_id) if row.repair_order_id else None
    return {
        "transaction_no": row.transaction_no,
        "idempotency_key": row.idempotency_key,
        "sku": item.sku if item else None,
        "transaction_type": row.transaction_type,
        "quantity": inventory_quantity_text(row.quantity),
        "before_quantity": inventory_quantity_text(row.before_quantity),
        "after_quantity": inventory_quantity_text(row.after_quantity),
        "unit_cost": str(row.unit_cost),
        "order_no": order.order_no if order else None,
        "operator_username": _user_name(db, row.operator_id),
        "remarks": row.remarks,
        "created_at": _iso(row.created_at),
    }


def _finance_transaction_payload(db: Session, row: FinanceTransaction) -> dict[str, Any]:
    order = db.get(RepairOrder, row.repair_order_id) if row.repair_order_id else None
    customer = db.get(Customer, row.customer_id) if row.customer_id else None
    quote = db.get(Quote, row.quote_id) if row.quote_id else None
    return {
        "transaction_no": row.transaction_no,
        "idempotency_key": row.idempotency_key,
        "order_no": order.order_no if order else None,
        "quote_no": quote.quote_no if quote else None,
        "customer_no": customer.customer_no if customer else None,
        "transaction_type": row.transaction_type,
        "category": row.category,
        "amount": str(row.amount),
        "payment_method": row.payment_method,
        "paid_at": _iso(row.paid_at),
        "description": row.description,
        "created_at": _iso(row.created_at),
        "deleted_at": _iso(row.deleted_at),
        "deletion_batch_id": row.deletion_batch_id,
    }


def scan_supported_records(db: Session) -> list[tuple[str, str, dict[str, Any]]]:
    result: list[tuple[str, str, dict[str, Any]]] = []
    for row in db.scalars(select(Customer).order_by(Customer.id)):
        result.append(("customer", row.customer_no, _customer_payload(row)))
    for row in db.scalars(select(DroneDevice).order_by(DroneDevice.id)):
        result.append(("device", _device_key(db, row), _device_payload(db, row)))
    for row in db.scalars(select(RepairOrder).order_by(RepairOrder.id)):
        result.append(("repair_order", row.order_no, _order_payload(db, row)))
    for row in db.scalars(select(ServiceTicket).order_by(ServiceTicket.id)):
        result.append(("service_ticket", row.ticket_no, _ticket_payload(db, row)))
    for row in db.scalars(select(Quote).order_by(Quote.id)):
        result.append(("quote", row.quote_no, _quote_payload(db, row)))
    for row in db.scalars(select(InventoryItem).order_by(InventoryItem.id)):
        result.append(("inventory_item", row.sku, _inventory_item_payload(db, row)))
    for row in db.scalars(select(InventoryTransaction).order_by(InventoryTransaction.id)):
        result.append(("inventory_transaction", row.transaction_no, _inventory_transaction_payload(db, row)))
    for row in db.scalars(select(FinanceTransaction).order_by(FinanceTransaction.id)):
        result.append(("finance_transaction", row.transaction_no, _finance_transaction_payload(db, row)))
    return result


def record_payload(db: Session, entity_type: str, record_key: str) -> dict[str, Any]:
    if entity_type == "customer":
        row = db.scalar(select(Customer).where(Customer.customer_no == record_key))
        return _customer_payload(row) if row else {}
    if entity_type == "device":
        row = db.scalar(select(DroneDevice).where(DroneDevice.sync_key == record_key))
        return _device_payload(db, row) if row else {}
    if entity_type == "repair_order":
        row = db.scalar(select(RepairOrder).where(RepairOrder.order_no == record_key))
        return _order_payload(db, row) if row else {}
    if entity_type == "service_ticket":
        row = db.scalar(select(ServiceTicket).where(ServiceTicket.ticket_no == record_key))
        return _ticket_payload(db, row) if row else {}
    if entity_type == "quote":
        row = db.scalar(select(Quote).where(Quote.quote_no == record_key))
        return _quote_payload(db, row) if row else {}
    if entity_type == "inventory_item":
        row = db.scalar(select(InventoryItem).where(InventoryItem.sku == record_key))
        return _inventory_item_payload(db, row) if row else {}
    if entity_type == "inventory_transaction":
        row = db.scalar(select(InventoryTransaction).where(InventoryTransaction.transaction_no == record_key))
        return _inventory_transaction_payload(db, row) if row else {}
    if entity_type == "finance_transaction":
        row = db.scalar(select(FinanceTransaction).where(FinanceTransaction.transaction_no == record_key))
        return _finance_transaction_payload(db, row) if row else {}
    raise ValueError(f"不支持的同步实体：{entity_type}")


def node_id(db: Session) -> str:
    configured = settings.sync_node_id.strip()
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == NODE_ID_KEY))
    if configured:
        value = configured
    elif row:
        value = row.value
    else:
        value = str(uuid.uuid4())
    if not row:
        db.add(SystemSetting(
            key=NODE_ID_KEY,
            value=value,
            description="离线同步节点唯一标识；部署后不得复制修改",
            is_secret=False,
        ))
        db.flush()
    elif configured and row.value != configured:
        row.value = configured
    return value


def _state(db: Session, entity_type: str, record_key: str) -> SyncEntityState | None:
    return db.scalar(select(SyncEntityState).where(
        SyncEntityState.entity_type == entity_type,
        SyncEntityState.record_key == record_key,
    ))


def _canonical(db: Session, entity_type: str, record_key: str) -> SyncCanonicalRecord | None:
    return db.scalar(select(SyncCanonicalRecord).where(
        SyncCanonicalRecord.entity_type == entity_type,
        SyncCanonicalRecord.record_key == record_key,
    ))


def _invalid_sync_event(message: str, *, code: str) -> BusinessError:
    return BusinessError(message, code=code, status_code=422)


def _validate_incoming_event_batch(db: Session, events: list[dict[str, Any]]) -> None:
    """Reject a malformed push batch before collection, node registration, or writes."""

    with db.no_autoflush:
        for event in events:
            if not isinstance(event, dict):
                raise _invalid_sync_event(
                    "同步事件格式无效",
                    code="invalid_sync_event",
                )
            entity_type = event.get("entity_type")
            if entity_type not in SUPPORTED_ENTITY_TYPES:
                raise _invalid_sync_event(
                    "同步事件包含不支持的实体类型",
                    code="invalid_sync_entity_type",
                )

            operation = event.get("operation", "upsert")
            if operation not in INCOMING_EVENT_OPERATIONS:
                raise _invalid_sync_event(
                    "同步事件包含不支持的操作类型",
                    code="invalid_sync_operation",
                )
            if operation == "history_import" and entity_type != "inventory_transaction":
                raise _invalid_sync_event(
                    "只有库存流水可以执行历史导入",
                    code="invalid_sync_history_import",
                )

            payload = event.get("payload_json")
            if not isinstance(payload, dict):
                raise _invalid_sync_event(
                    "同步事件载荷格式无效",
                    code="invalid_sync_payload",
                )
            declared_hash = event.get("payload_hash")
            calculated_hash = payload_hash(payload)
            if not isinstance(declared_hash, str) or not hmac.compare_digest(
                declared_hash.encode("utf-8"),
                calculated_hash.encode("ascii"),
            ):
                raise _invalid_sync_event(
                    "同步事件载荷哈希校验失败",
                    code="sync_payload_hash_mismatch",
                )

            key_field = ENTITY_RECORD_KEY_FIELDS[entity_type]
            payload_record_key = payload.get(key_field)
            record_key = event.get("record_key")
            if not isinstance(payload_record_key, str) or not payload_record_key:
                raise _invalid_sync_event(
                    "同步事件载荷缺少业务主键",
                    code="sync_record_key_missing",
                )
            if not isinstance(record_key, str) or record_key != payload_record_key:
                raise _invalid_sync_event(
                    "同步事件记录键与载荷业务主键不一致",
                    code="sync_record_key_mismatch",
                )
            quantity_fields: tuple[str, ...] = ()
            if entity_type == "inventory_item":
                quantity_fields = ("stock_quantity", "safety_stock")
            elif entity_type == "inventory_transaction":
                quantity_fields = ("quantity", "before_quantity", "after_quantity")
            try:
                for field in quantity_fields:
                    if field in payload:
                        inventory_quantity(payload[field])
            except ValueError as exc:
                raise _invalid_sync_event(
                    "同步库存数量必须为整数",
                    code="inventory_quantity_must_be_integer",
                ) from exc

            base_revision = event.get("base_revision")
            if (
                not isinstance(base_revision, int)
                or isinstance(base_revision, bool)
                or base_revision < 0
            ):
                raise _invalid_sync_event(
                    "同步事件基础版本无效",
                    code="invalid_sync_base_revision",
                )
            current = _canonical(db, entity_type, record_key)
            if current is None and base_revision != 0:
                raise _invalid_sync_event(
                    "主机不存在该记录，基础版本必须为 0",
                    code="sync_base_revision_without_record",
                )


def _publish_host_change(
    db: Session,
    *,
    origin_node_id: str,
    entity_type: str,
    record_key: str,
    payload: dict[str, Any],
    event_id: str | None = None,
    operation: str = "upsert",
    force_emit: bool = False,
) -> SyncCanonicalRecord:
    digest = payload_hash(payload)
    record = _canonical(db, entity_type, record_key)
    if record and record.payload_hash == digest and not force_emit:
        return record
    if record and record.payload_hash != digest:
        record.revision += 1
        record.payload_json = payload
        record.payload_hash = digest
        record.origin_node_id = origin_node_id
    elif not record:
        record = SyncCanonicalRecord(
            entity_type=entity_type,
            record_key=record_key,
            revision=1,
            payload_json=payload,
            payload_hash=digest,
            origin_node_id=origin_node_id,
        )
        db.add(record)
        db.flush()
    db.add(SyncServerChange(
        event_id=event_id or str(uuid.uuid4()),
        origin_node_id=origin_node_id,
        entity_type=entity_type,
        record_key=record_key,
        operation=operation,
        revision=record.revision,
        payload_hash=digest,
        payload_json=payload,
    ))
    state = _state(db, entity_type, record_key)
    if state:
        state.payload_hash = digest
        state.payload_json = payload
        state.server_revision = record.revision
    else:
        db.add(SyncEntityState(
            entity_type=entity_type,
            record_key=record_key,
            payload_hash=digest,
            payload_json=payload,
            server_revision=record.revision,
        ))
    return record


def collect_local_changes(db: Session) -> dict[str, int]:
    current_node = node_id(db)
    host = settings.sync_role == "host"
    created = updated = 0
    for entity_type, record_key, payload in scan_supported_records(db):
        digest = payload_hash(payload)
        state = _state(db, entity_type, record_key)
        if state and state.payload_hash == digest:
            continue
        if host:
            _publish_host_change(
                db,
                origin_node_id=current_node,
                entity_type=entity_type,
                record_key=record_key,
                payload=payload,
            )
            created += 1
            continue
        pending = db.scalar(select(SyncOutboxEvent).where(
            SyncOutboxEvent.entity_type == entity_type,
            SyncOutboxEvent.record_key == record_key,
            SyncOutboxEvent.status == "pending",
        ))
        if pending:
            pending.payload_json = payload
            pending.payload_hash = digest
            updated += 1
        else:
            operation = "upsert"
            if entity_type == "inventory_transaction":
                item_state = _state(db, "inventory_item", payload.get("sku") or "")
                if not item_state:
                    operation = "history_import"
            db.add(SyncOutboxEvent(
                event_id=str(uuid.uuid4()),
                origin_node_id=current_node,
                entity_type=entity_type,
                record_key=record_key,
                operation=operation,
                base_revision=state.server_revision if state else 0,
                base_payload_json=state.payload_json if state else None,
                payload_json=payload,
                payload_hash=digest,
            ))
            created += 1
    db.commit()
    return {"created": created, "coalesced": updated}


def pending_events(db: Session, *, limit: int = 500) -> list[SyncOutboxEvent]:
    return list(db.scalars(
        select(SyncOutboxEvent)
        .where(SyncOutboxEvent.status == "pending")
        .order_by(SyncOutboxEvent.id)
        .limit(limit)
    ))


def _conflicting_fields(base: dict[str, Any] | None, incoming: dict[str, Any], current: dict[str, Any]) -> list[str]:
    base = base or {}
    result: list[str] = []
    for key in sorted(set(base) | set(incoming) | set(current)):
        if incoming.get(key) != base.get(key) and current.get(key) != base.get(key) and incoming.get(key) != current.get(key):
            result.append(key)
    return result


def _set_values(row: Any, payload: dict[str, Any], fields: tuple[str, ...], *, datetimes=(), dates=(), decimals=()) -> None:
    for field in fields:
        value = payload.get(field)
        if field in datetimes:
            value = _parse_datetime(value)
        elif field in dates:
            value = _parse_date(value)
        elif field in decimals and value is not None:
            value = Decimal(value)
        setattr(row, field, value)


def apply_payload(
    db: Session,
    entity_type: str,
    payload: dict[str, Any],
    *,
    host_merge: bool = False,
) -> None:
    if entity_type == "customer":
        row = db.scalar(select(Customer).where(Customer.customer_no == payload["customer_no"]))
        if not row:
            row = Customer(customer_no=payload["customer_no"], name=payload["name"])
            db.add(row)
        _set_values(row, payload, (
            "name", "phone", "email", "wechat", "wecom_external_user_id", "wecom_group_id",
            "customer_type", "company_name", "province", "city", "address", "notes",
            "deleted_at", "deletion_batch_id",
        ), datetimes=("deleted_at",))
    elif entity_type == "device":
        customer_id = db.scalar(select(Customer.id).where(Customer.customer_no == payload["customer_no"]))
        if not customer_id:
            raise ValueError("缺少设备关联客户")
        row = db.scalar(select(DroneDevice).where(DroneDevice.sync_key == payload["sync_key"]))
        if not row:
            row = DroneDevice(
                sync_key=payload["sync_key"],
                customer_id=customer_id,
                model=payload["model"],
                serial_number=payload["serial_number"],
            )
            db.add(row)
        row.customer_id = customer_id
        _set_values(row, payload, (
            "brand", "model", "serial_number", "activation_date", "purchase_date",
            "warranty_status", "is_temporary", "remarks", "deleted_at", "deletion_batch_id",
        ), dates=("activation_date", "purchase_date"), datetimes=("deleted_at",))
    elif entity_type == "repair_order":
        customer_id = db.scalar(select(Customer.id).where(Customer.customer_no == payload["customer_no"]))
        device_id = db.scalar(select(DroneDevice.id).where(DroneDevice.sync_key == payload["device_key"]))
        if not customer_id or not device_id:
            raise ValueError("缺少维修工单关联客户或设备")
        device = db.get(DroneDevice, device_id)
        if device.customer_id != customer_id:
            raise ValueError("维修工单设备与客户归属不一致")
        row = db.scalar(select(RepairOrder).where(RepairOrder.order_no == payload["order_no"]))
        if not row:
            row = RepairOrder(
                order_no=payload["order_no"],
                customer_id=customer_id,
                device_id=device_id,
                fault_description=payload["fault_description"],
            )
            db.add(row)
            db.flush()
        row.customer_id = customer_id
        row.device_id = device_id
        row.engineer_id = _user_id(db, payload.get("engineer_username"))
        row.processing_group_id = _group_id(db, payload.get("processing_group"))
        _set_values(row, payload, (
            "source_request_key", "fault_description", "intake_condition", "intake_accessories",
            "status", "priority", "received_at", "expected_finish_at", "completed_at",
            "internal_notes", "customer_notes", "deleted_at", "deletion_batch_id",
        ), datetimes=("received_at", "expected_finish_at", "completed_at", "deleted_at"))
        existing = {
            (item.to_status, _iso(item.changed_at), item.reason)
            for item in db.scalars(select(RepairOrderStatusHistory).where(RepairOrderStatusHistory.repair_order_id == row.id))
        }
        for item in payload.get("status_history", []):
            key = (item["to_status"], item["changed_at"], item.get("reason"))
            if key not in existing:
                db.add(RepairOrderStatusHistory(
                    repair_order_id=row.id,
                    from_status=item.get("from_status"),
                    to_status=item["to_status"],
                    changed_by=_user_id(db, item.get("changed_by")),
                    reason=item.get("reason"),
                    changed_at=_parse_datetime(item["changed_at"]),
                ))
    elif entity_type == "service_ticket":
        row = db.scalar(select(ServiceTicket).where(ServiceTicket.ticket_no == payload["ticket_no"]))
        if payload.get("ticket_type") not in VALID_TICKET_TYPES:
            raise ValueError("未知的服务工单类型")
        if (
            row
            and row.ticket_type != payload.get("ticket_type")
            and db.scalar(select(Quote.id).where(
                Quote.service_ticket_id == row.id,
                Quote.deleted_at.is_(None),
            ).limit(1)) is not None
        ):
            raise ValueError("已有有效报价的服务工单不能通过同步更改类型")
        customer_id = db.scalar(select(Customer.id).where(Customer.customer_no == payload.get("customer_no"))) if payload.get("customer_no") else None
        device_id = db.scalar(select(DroneDevice.id).where(DroneDevice.sync_key == payload.get("device_key"))) if payload.get("device_key") else None
        order_id = db.scalar(select(RepairOrder.id).where(RepairOrder.order_no == payload.get("order_no"))) if payload.get("order_no") else None
        if payload.get("customer_no") and not customer_id:
            raise ValueError("缺少服务工单关联客户")
        if payload.get("device_key") and not device_id:
            raise ValueError("缺少服务工单关联设备")
        if payload.get("order_no") and not order_id:
            raise ValueError("缺少服务工单关联维修工单")
        if order_id:
            order = db.get(RepairOrder, order_id)
            if payload.get("ticket_type") != "repair":
                raise ValueError("关联维修工单的服务工单类型必须为 repair")
            if customer_id is not None and customer_id != order.customer_id:
                raise ValueError("服务工单客户与维修工单不一致")
            if device_id is not None and device_id != order.device_id:
                raise ValueError("服务工单设备与维修工单不一致")
            customer_id, device_id = order.customer_id, order.device_id
        elif payload.get("ticket_type") == "repair":
            raise ValueError("repair 服务工单必须关联维修工单")
        elif device_id and customer_id and db.get(DroneDevice, device_id).customer_id != customer_id:
            raise ValueError("服务工单设备与客户归属不一致")
        if row and row.repair_order_id and row.repair_order_id != order_id:
            raise ValueError("维修关联服务工单不能通过同步解除或改绑")
        replacement_fields = (
            "replacement_inspection_result",
            "trade_in_credit",
            "return_reference",
            "outbound_to_customer_tracking_no",
        )
        existing_replacement_data = row and any((
            row.replacement_inspection_result,
            row.trade_in_credit is not None,
            row.return_reference,
            row.outbound_to_customer_tracking_no,
        ))
        if payload.get("ticket_type") != "replacement" and (
            existing_replacement_data
            or any(payload.get(field) is not None for field in replacement_fields)
        ):
            raise ValueError("置换业务字段仅适用于置换工单")
        replacement_input = {
            field: payload[field] for field in replacement_fields if field in payload
        }
        try:
            replacement_values = ReplacementTicketUpdate.model_validate(
                replacement_input
            ).model_dump(exclude_unset=True)
        except ValidationError as exc:
            raise ValueError("置换业务字段格式无效") from exc
        if not row:
            row = ServiceTicket(
                ticket_no=payload["ticket_no"],
                ticket_type=payload["ticket_type"],
                title=payload["title"],
                description=payload["description"],
            )
            db.add(row)
            db.flush()
        row.customer_id, row.device_id, row.repair_order_id = customer_id, device_id, order_id
        row.current_owner_id = _user_id(db, payload.get("current_owner"))
        row.processing_group_id = _group_id(db, payload.get("processing_group"))
        row.created_by = _user_id(db, payload.get("created_by"))
        _set_values(row, payload, (
            "ticket_type", "title", "description", "status", "priority", "due_at",
            "first_response_at", "resolved_at", "closed_at", "last_reminded_at",
            "reminder_count", "deleted_at", "deletion_batch_id",
        ), datetimes=("due_at", "first_response_at", "resolved_at", "closed_at", "last_reminded_at", "deleted_at"))
        for field in replacement_fields:
            if field not in payload:
                continue
            setattr(row, field, replacement_values[field])
        existing_notes = {
            (item.visibility, item.content, _iso(item.created_at))
            for item in db.scalars(select(ServiceTicketNote).where(ServiceTicketNote.ticket_id == row.id))
        }
        for item in payload.get("notes", []):
            key = (item["visibility"], item["content"], item["created_at"])
            if key not in existing_notes:
                db.add(ServiceTicketNote(
                    ticket_id=row.id,
                    visibility=item["visibility"],
                    content=item["content"],
                    author_id=_user_id(db, item.get("author")),
                    created_at=_parse_datetime(item["created_at"]),
                ))
        existing_timeline = {
            (item.event_type, item.summary, _iso(item.created_at))
            for item in db.scalars(select(ServiceTicketTimeline).where(ServiceTicketTimeline.ticket_id == row.id))
        }
        for item in payload.get("timeline", []):
            key = (item["event_type"], item["summary"], item["created_at"])
            if key not in existing_timeline:
                db.add(ServiceTicketTimeline(
                    ticket_id=row.id,
                    event_type=item["event_type"],
                    summary=item["summary"],
                    from_status=item.get("from_status"),
                    to_status=item.get("to_status"),
                    details_json=item.get("details_json"),
                    actor_id=_user_id(db, item.get("actor")),
                    created_at=_parse_datetime(item["created_at"]),
                ))
    elif entity_type == "quote":
        row = db.scalar(select(Quote).where(Quote.quote_no == payload["quote_no"]))
        order_no = payload.get("order_no")
        ticket_no = payload.get("ticket_no")
        target_order = db.scalar(
            select(RepairOrder).where(RepairOrder.order_no == order_no)
        ) if order_no else None
        target_ticket = db.scalar(
            select(ServiceTicket).where(ServiceTicket.ticket_no == ticket_no)
        ) if ticket_no else None
        if order_no and not target_order:
            raise ValueError("缺少报价关联维修工单")
        if ticket_no and not target_ticket:
            raise ValueError("缺少报价关联服务工单")
        if bool(target_order) == bool(target_ticket):
            raise ValueError("报价必须关联且只关联一个工单")
        incoming_quote_deleted = payload.get("deleted_at") is not None
        if not incoming_quote_deleted:
            if target_order and target_order.deleted_at is not None:
                raise ValueError("活跃报价不能关联已删除的维修工单")
            if target_ticket and target_ticket.deleted_at is not None:
                raise ValueError("活跃报价不能关联已删除的服务工单")
        order_id = target_order.id if target_order else None
        ticket_id = target_ticket.id if target_ticket else None
        if target_ticket and target_ticket.ticket_type not in {"retail", "replacement"}:
            raise ValueError("只有零售或置换服务工单可以关联报价")
        if target_ticket and not target_ticket.customer_id:
            raise ValueError("服务工单报价必须关联客户")
        if row and (
            row.repair_order_id != order_id
            or row.service_ticket_id != ticket_id
        ):
            raise ValueError("报价不能通过同步解除或改绑业务工单")
        payment_url = normalize_payment_url(payload.get("payment_url")) if "payment_url" in payload else None
        discount = Decimal(payload.get("discount") or "0")
        labor_fee = Decimal(payload.get("labor_fee") or "0")
        shipping_fee = Decimal(payload.get("shipping_fee") or "0")
        if min(discount, labor_fee, shipping_fee) < 0:
            raise ValueError("报价优惠、工时费和运费不能为负数")
        prepared_items: list[dict[str, Any]] = []
        subtotal = Decimal("0")
        for item in payload.get("items", []):
            quantity = Decimal(item["quantity"])
            unit_price = Decimal(item["unit_price"])
            cost_price = Decimal(item.get("cost_price") or "0")
            if quantity <= 0 or unit_price < 0 or cost_price < 0:
                raise ValueError("报价项数量和价格无效")
            amount = quote_money(quantity * unit_price)
            subtotal += amount
            prepared_items.append({
                "item_name": item["item_name"],
                "specification": item.get("specification"),
                "quantity": quantity,
                "unit_price": unit_price,
                "cost_price": cost_price,
                "amount": amount,
                "item_type": item["item_type"],
                "remarks": item.get("remarks"),
                "sort_order": item.get("sort_order", 0),
            })
        if not row:
            row = Quote(quote_no=payload["quote_no"], repair_order_id=order_id, service_ticket_id=ticket_id)
            db.add(row)
            db.flush()
        row.repair_order_id, row.service_ticket_id = order_id, ticket_id
        _set_values(row, payload, (
            "version", "status", "assessment_result", "assessment_responsibility",
            "repair_recommendation", "customer_notice", "customer_confirmed_at",
            "deleted_at", "deletion_batch_id",
        ), datetimes=("customer_confirmed_at", "deleted_at"))
        if "payment_url" in payload:
            row.payment_url = payment_url
        row.discount = quote_money(discount)
        row.labor_fee = quote_money(labor_fee)
        row.shipping_fee = quote_money(shipping_fee)
        row.items.clear()
        for item in prepared_items:
            row.items.append(QuoteItem(**item))
        row.subtotal = quote_money(subtotal)
        row.total_amount = quote_money(max(
            Decimal("0"),
            subtotal + row.labor_fee + row.shipping_fee - row.discount,
        ))
        if target_order and target_order.deleted_at is None:
            QuoteService.recalculate_order_total(db, target_order)
            RepairOrderService.recalculate_finance(db, target_order)
    elif entity_type == "inventory_item":
        row = db.scalar(select(InventoryItem).where(InventoryItem.sku == payload["sku"]))
        is_new = row is None
        if is_new:
            row = InventoryItem(
                sku=payload["sku"],
                name=payload["name"],
                stock_quantity=inventory_quantity(payload.get("stock_quantity") or "0"),
            )
            db.add(row)
        supplier_name = (payload.get("supplier_name") or "").strip()
        supplier = db.scalar(select(Supplier).where(Supplier.name == supplier_name)) if supplier_name else None
        if supplier_name and not supplier:
            supplier = Supplier(name=supplier_name)
            db.add(supplier)
            db.flush()
        row.supplier_id = supplier.id if supplier else None
        _set_values(row, payload, (
            "name", "category", "compatible_models", "unit", "purchase_price",
            "sale_price", "location", "enabled", "deleted_at", "deletion_batch_id",
        ), datetimes=("deleted_at",), decimals=("purchase_price", "sale_price"))
        if "safety_stock" in payload:
            row.safety_stock = inventory_quantity(payload.get("safety_stock") or "0")
        # 主机库存余额只能由库存流水计算；终端下发的物料快照不得覆盖现有余额。
        if not host_merge or is_new:
            row.stock_quantity = inventory_quantity(payload.get("stock_quantity") or "0")
    elif entity_type == "inventory_transaction":
        item = db.scalar(select(InventoryItem).where(InventoryItem.sku == payload.get("sku")))
        if not item:
            raise ValueError("缺少库存流水关联物料")
        order_id = db.scalar(select(RepairOrder.id).where(
            RepairOrder.order_no == payload.get("order_no")
        )) if payload.get("order_no") else None
        operator_id = _user_id(db, payload.get("operator_username"))
        row = db.scalar(select(InventoryTransaction).where(
            InventoryTransaction.transaction_no == payload["transaction_no"]
        ))
        history_import = bool(payload.get("_sync_history_import"))
        if host_merge and not history_import:
            if not row:
                row = InventoryService.change_stock(db, StockChange(
                    inventory_item_id=item.id,
                    transaction_type=payload["transaction_type"],
                    quantity=inventory_quantity(payload["quantity"]),
                    repair_order_id=order_id,
                    operator_id=operator_id,
                    unit_cost=Decimal(payload["unit_cost"]) if payload.get("unit_cost") is not None else None,
                    remarks=payload.get("remarks"),
                ))
                row.transaction_no = payload["transaction_no"]
                row.created_at = _parse_datetime(payload.get("created_at")) or row.created_at
        else:
            old_order_id = row.repair_order_id if row else None
            if not row:
                row = InventoryTransaction(
                    transaction_no=payload["transaction_no"],
                    inventory_item_id=item.id,
                    transaction_type=payload["transaction_type"],
                    quantity=inventory_quantity(payload["quantity"]),
                    before_quantity=inventory_quantity(payload["before_quantity"]),
                    after_quantity=inventory_quantity(payload["after_quantity"]),
                    unit_cost=Decimal(payload.get("unit_cost") or "0"),
                    created_at=_parse_datetime(payload.get("created_at")) or utcnow(),
                )
                db.add(row)
            row.inventory_item_id = item.id
            row.transaction_type = payload["transaction_type"]
            row.quantity = inventory_quantity(payload["quantity"])
            row.before_quantity = inventory_quantity(payload["before_quantity"])
            row.after_quantity = inventory_quantity(payload["after_quantity"])
            row.unit_cost = Decimal(payload.get("unit_cost") or "0")
            row.repair_order_id = order_id
            row.operator_id = operator_id
            row.remarks = payload.get("remarks")
            if not history_import:
                item.stock_quantity = row.after_quantity
            for affected_id in {value for value in (old_order_id, order_id) if value}:
                RepairOrderService.recalculate_finance(db, db.get(RepairOrder, affected_id))
        incoming_key = payload.get("idempotency_key")
        key_owner = db.scalar(select(InventoryTransaction).where(
            InventoryTransaction.idempotency_key == incoming_key
        )) if incoming_key else None
        if key_owner and key_owner.id != row.id:
            raise ValueError("库存流水幂等键已被其他记录占用")
        row.idempotency_key = incoming_key
    elif entity_type == "finance_transaction":
        order = db.scalar(select(RepairOrder).where(
            RepairOrder.order_no == payload.get("order_no")
        )) if payload.get("order_no") else None
        customer = db.scalar(select(Customer).where(
            Customer.customer_no == payload.get("customer_no")
        )) if payload.get("customer_no") else None
        quote = db.scalar(select(Quote).where(
            Quote.quote_no == payload.get("quote_no")
        )) if payload.get("quote_no") else None
        if payload.get("order_no") and not order:
            raise ValueError("缺少财务流水关联工单")
        if payload.get("customer_no") and not customer:
            raise ValueError("缺少财务流水关联客户")
        if payload.get("quote_no") and not quote:
            raise ValueError("缺少财务流水关联报价")
        if quote:
            if quote.repair_order_id:
                if order and order.id != quote.repair_order_id:
                    raise ValueError("财务流水报价与维修工单不一致")
                order = db.get(RepairOrder, quote.repair_order_id)
                if customer and customer.id != order.customer_id:
                    raise ValueError("财务流水客户与报价客户不一致")
                customer = db.get(Customer, order.customer_id)
            elif quote.service_ticket_id:
                if order:
                    raise ValueError("零售报价财务流水不能同时关联维修工单")
                ticket = db.get(ServiceTicket, quote.service_ticket_id)
                if not ticket or not ticket.customer_id:
                    raise ValueError("零售报价缺少关联客户")
                if customer and customer.id != ticket.customer_id:
                    raise ValueError("财务流水客户与零售报价客户不一致")
                customer = db.get(Customer, ticket.customer_id)
            else:
                raise ValueError("报价未关联业务工单")
        if order:
            if customer and customer.id != order.customer_id:
                raise ValueError("财务流水客户与维修工单不一致")
            customer = db.get(Customer, order.customer_id)
        tx_type = FinanceService._validated_type(payload["transaction_type"])
        amount = Decimal(payload["amount"])
        if amount <= 0:
            raise ValueError("财务流水金额必须大于 0")
        row = db.scalar(select(FinanceTransaction).where(
            FinanceTransaction.transaction_no == payload["transaction_no"]
        ))
        old_order_id = row.repair_order_id if row else None
        if not row:
            row = FinanceTransaction(
                transaction_no=payload["transaction_no"],
                transaction_type=tx_type,
                category=payload["category"],
                amount=amount,
                paid_at=_parse_datetime(payload.get("paid_at")) or utcnow(),
                created_at=_parse_datetime(payload.get("created_at")) or utcnow(),
            )
            db.add(row)
        row.repair_order_id = order.id if order else None
        row.customer_id = customer.id if customer else None
        row.quote_id = quote.id if quote else None
        row.transaction_type = tx_type
        row.category = payload["category"]
        row.amount = amount
        row.payment_method = payload.get("payment_method")
        row.paid_at = _parse_datetime(payload.get("paid_at")) or row.paid_at
        row.description = payload.get("description")
        row.deleted_at = _parse_datetime(payload.get("deleted_at"))
        row.deletion_batch_id = payload.get("deletion_batch_id")
        incoming_key = payload.get("idempotency_key")
        key_owner = db.scalar(select(FinanceTransaction).where(
            FinanceTransaction.idempotency_key == incoming_key
        )) if incoming_key else None
        if key_owner and key_owner.id != row.id:
            raise ValueError("财务流水幂等键已被其他记录占用")
        row.idempotency_key = incoming_key
        db.flush()
        for affected_id in {value for value in (old_order_id, row.repair_order_id) if value}:
            RepairOrderService.recalculate_finance(db, db.get(RepairOrder, affected_id))
    else:
        raise ValueError(f"不支持的同步实体：{entity_type}")
    db.flush()


def receive_events(db: Session, origin_node_id: str, events: list[dict[str, Any]], *, ip_address: str | None = None) -> dict[str, Any]:
    if settings.sync_role != "host":
        raise BusinessError("当前节点不是同步主机", code="sync_host_required", status_code=409)
    _validate_incoming_event_batch(db, events)
    collect_local_changes(db)
    node = db.scalar(select(SyncNode).where(SyncNode.node_id == origin_node_id))
    if not node:
        node = SyncNode(
            node_id=origin_node_id,
            name=f"终端-{origin_node_id[:8]}",
            role="terminal",
            enabled=True,
        )
        db.add(node)
    if not node.enabled:
        raise BusinessError("该同步终端已停用", code="sync_node_disabled", status_code=403)
    node.last_seen_at = utcnow()
    node.last_ip = ip_address
    acknowledgements: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for event in events:
        duplicate = db.scalar(select(SyncServerChange).where(SyncServerChange.event_id == event["event_id"]))
        if duplicate:
            acknowledgements.append({"event_id": event["event_id"], "revision": duplicate.revision, "duplicate": True})
            continue
        existing_conflict = db.scalar(select(SyncConflict).where(
            SyncConflict.event_id == event["event_id"],
            SyncConflict.status == "open",
        ))
        if existing_conflict:
            conflicts.append({
                "event_id": event["event_id"],
                "conflict_id": existing_conflict.conflict_id,
                "record_key": existing_conflict.record_key,
            })
            continue
        current = _canonical(db, event["entity_type"], event["record_key"])
        if current and current.payload_hash == event["payload_hash"]:
            acknowledgements.append({"event_id": event["event_id"], "revision": current.revision, "duplicate": True})
            continue
        if current and event["base_revision"] != current.revision:
            conflict = SyncConflict(
                conflict_id=str(uuid.uuid4()),
                event_id=event["event_id"],
                origin_node_id=origin_node_id,
                entity_type=event["entity_type"],
                record_key=event["record_key"],
                base_revision=event["base_revision"],
                current_revision=current.revision,
                base_payload_json=event.get("base_payload_json"),
                incoming_payload_json=event["payload_json"],
                current_payload_json=current.payload_json,
                conflicting_fields_json=_conflicting_fields(
                    event.get("base_payload_json"), event["payload_json"], current.payload_json
                ),
            )
            db.add(conflict)
            conflicts.append({"event_id": event["event_id"], "conflict_id": conflict.conflict_id, "record_key": event["record_key"]})
            continue
        try:
            with db.begin_nested():
                apply_input = dict(event["payload_json"])
                if event.get("operation") == "history_import":
                    item_record = _canonical(db, "inventory_item", apply_input.get("sku") or "")
                    if not item_record or item_record.origin_node_id != origin_node_id:
                        raise ValueError("库存历史流水只能随同一终端首次创建的物料导入")
                    apply_input["_sync_history_import"] = True
                apply_payload(db, event["entity_type"], apply_input, host_merge=True)
        except (BusinessError, IntegrityError, ValueError) as exc:
            current_payload = current.payload_json if current else {}
            conflict = SyncConflict(
                conflict_id=str(uuid.uuid4()),
                event_id=event["event_id"],
                origin_node_id=origin_node_id,
                entity_type=event["entity_type"],
                record_key=event["record_key"],
                base_revision=event["base_revision"],
                current_revision=current.revision if current else 0,
                base_payload_json=event.get("base_payload_json"),
                incoming_payload_json=event["payload_json"],
                current_payload_json=current_payload,
                conflicting_fields_json=["apply_error"],
            )
            db.add(conflict)
            conflicts.append({"event_id": event["event_id"], "conflict_id": conflict.conflict_id, "error": str(exc)})
            continue
        canonical_payload = record_payload(db, event["entity_type"], event["record_key"])
        record = _publish_host_change(
            db,
            origin_node_id=origin_node_id,
            entity_type=event["entity_type"],
            record_key=event["record_key"],
            payload=canonical_payload,
            event_id=event["event_id"],
        )
        if event["entity_type"] == "inventory_transaction":
            item = db.scalar(select(InventoryItem).where(
                InventoryItem.sku == canonical_payload.get("sku")
            ))
            if item:
                _publish_host_change(
                    db,
                    origin_node_id=origin_node_id,
                    entity_type="inventory_item",
                    record_key=item.sku,
                    payload=_inventory_item_payload(db, item),
                )
        acknowledgements.append({"event_id": event["event_id"], "revision": record.revision, "duplicate": False})
    db.commit()
    return {"acknowledgements": acknowledgements, "conflicts": conflicts}


def changes_after(db: Session, cursor: int, *, limit: int = 1000) -> dict[str, Any]:
    if settings.sync_role != "host":
        raise BusinessError("当前节点不是同步主机", code="sync_host_required", status_code=409)
    collect_local_changes(db)
    changes = list(db.scalars(
        select(SyncServerChange)
        .where(SyncServerChange.id > cursor)
        .order_by(SyncServerChange.id)
        .limit(limit)
    ))
    return {
        "cursor": changes[-1].id if changes else cursor,
        "has_more": len(changes) == limit,
        "changes": [
            {
                "server_seq": item.id,
                "event_id": item.event_id,
                "origin_node_id": item.origin_node_id,
                "entity_type": item.entity_type,
                "record_key": item.record_key,
                "operation": item.operation,
                "revision": item.revision,
                "payload_hash": item.payload_hash,
                "payload_json": item.payload_json,
            }
            for item in changes
        ],
    }


def apply_changes(db: Session, changes: list[dict[str, Any]]) -> dict[str, Any]:
    applied = conflicts = 0
    for change in changes:
        state = _state(db, change["entity_type"], change["record_key"])
        local_payload = record_payload(db, change["entity_type"], change["record_key"]) or None
        local_hash = payload_hash(local_payload) if local_payload else None
        force_host = change.get("operation") == "force_host"
        locally_diverged = bool(
            local_payload
            and local_hash != change["payload_hash"]
            and (not state or local_hash != state.payload_hash)
        )
        if locally_diverged and not force_host:
            existing_conflict = db.scalar(select(SyncConflict).where(
                SyncConflict.event_id == change["event_id"],
                SyncConflict.status == "open",
            ))
            if not existing_conflict:
                db.add(SyncConflict(
                    conflict_id=str(uuid.uuid4()),
                    event_id=change["event_id"],
                    origin_node_id=change["origin_node_id"],
                    entity_type=change["entity_type"],
                    record_key=change["record_key"],
                    base_revision=state.server_revision if state else 0,
                    current_revision=change["revision"],
                    base_payload_json=state.payload_json if state else None,
                    incoming_payload_json=local_payload,
                    current_payload_json=change["payload_json"],
                    conflicting_fields_json=_conflicting_fields(
                        state.payload_json if state else None,
                        local_payload,
                        change["payload_json"],
                    ),
                ))
            conflicts += 1
            continue
        apply_payload(db, change["entity_type"], change["payload_json"])
        if state:
            state.payload_hash = change["payload_hash"]
            state.payload_json = change["payload_json"]
            state.server_revision = change["revision"]
        else:
            db.add(SyncEntityState(
                entity_type=change["entity_type"],
                record_key=change["record_key"],
                payload_hash=change["payload_hash"],
                payload_json=change["payload_json"],
                server_revision=change["revision"],
            ))
        outbox = db.scalar(select(SyncOutboxEvent).where(
            SyncOutboxEvent.event_id == change["event_id"]
        ))
        if outbox:
            outbox.status = "acknowledged"
            outbox.acknowledged_at = utcnow()
            outbox.error_message = None
        if force_host:
            for conflict in db.scalars(select(SyncConflict).where(
                SyncConflict.event_id == change["event_id"],
                SyncConflict.status == "open",
            )):
                conflict.status = "resolved"
                conflict.resolution = "keep_host"
                conflict.resolved_at = utcnow()
        applied += 1
    db.commit()
    return {"applied": applied, "conflicts": conflicts}


def resolve_conflict(db: Session, conflict_id: str, resolution: str) -> SyncConflict:
    if settings.sync_role != "host":
        raise BusinessError("只能在同步主机处理冲突", code="sync_host_required", status_code=409)
    if resolution not in {"keep_host", "accept_terminal"}:
        raise BusinessError("未知的冲突处理方式", code="invalid_sync_resolution")
    conflict = db.scalar(select(SyncConflict).where(SyncConflict.conflict_id == conflict_id))
    if not conflict:
        raise BusinessError("同步冲突不存在", code="sync_conflict_not_found", status_code=404)
    if conflict.status != "open":
        raise BusinessError("该同步冲突已经处理", code="sync_conflict_resolved", status_code=409)
    current = _canonical(db, conflict.entity_type, conflict.record_key)
    if resolution == "keep_host":
        if not current:
            raise BusinessError("主机版本不存在，不能保留主机版本", code="sync_canonical_missing", status_code=409)
        if not db.scalar(select(SyncServerChange).where(SyncServerChange.event_id == conflict.event_id)):
            _publish_host_change(
                db,
                origin_node_id=node_id(db),
                entity_type=conflict.entity_type,
                record_key=conflict.record_key,
                payload=current.payload_json,
                event_id=conflict.event_id,
                operation="force_host",
                force_emit=True,
            )
    else:
        try:
            with db.begin_nested():
                apply_payload(
                    db,
                    conflict.entity_type,
                    conflict.incoming_payload_json,
                    host_merge=True,
                )
        except (BusinessError, IntegrityError, ValueError) as exc:
            raise BusinessError(
                f"终端版本无法应用：{exc}",
                code="sync_conflict_apply_failed",
                status_code=409,
            ) from exc
        canonical_payload = record_payload(db, conflict.entity_type, conflict.record_key)
        _publish_host_change(
            db,
            origin_node_id=conflict.origin_node_id,
            entity_type=conflict.entity_type,
            record_key=conflict.record_key,
            payload=canonical_payload,
            event_id=conflict.event_id,
            force_emit=True,
        )
        if conflict.entity_type == "inventory_transaction":
            item = db.scalar(select(InventoryItem).where(
                InventoryItem.sku == canonical_payload.get("sku")
            ))
            if item:
                _publish_host_change(
                    db,
                    origin_node_id=conflict.origin_node_id,
                    entity_type="inventory_item",
                    record_key=item.sku,
                    payload=_inventory_item_payload(db, item),
                )
    conflict.status = "resolved"
    conflict.resolution = resolution
    conflict.resolved_at = utcnow()
    db.commit()
    db.refresh(conflict)
    return conflict


def status(db: Session) -> dict[str, Any]:
    current_node = node_id(db)
    return {
        "role": settings.sync_role,
        "node_id": current_node,
        "node_name": settings.sync_node_name or socket.gethostname(),
        "host_url": settings.sync_host_url,
        "interval_seconds": settings.sync_interval_seconds,
        "supported_entities": list(SUPPORTED_ENTITY_TYPES),
        "host_only_entities": list(HOST_ONLY_ENTITY_TYPES),
        "pending": len(pending_events(db, limit=10000)),
        "open_conflicts": len(list(db.scalars(select(SyncConflict.id).where(SyncConflict.status == "open")))),
    }
