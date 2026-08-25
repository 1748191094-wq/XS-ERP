from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models.entities import (
    Customer,
    DamageAssessment,
    DeletedRecord,
    DroneDevice,
    FinanceTransaction,
    FollowUpTask,
    InventoryItem,
    Quote,
    RepairOrder,
    ServiceTicket,
    User,
    WorkOrderGroup,
    utcnow,
)
from app.services.quotes import SERVICE_TICKET_QUOTE_TYPES


RESOURCE_MODELS = {
    "customer": Customer,
    "drone_device": DroneDevice,
    "repair_order": RepairOrder,
    "service_ticket": ServiceTicket,
    "quote": Quote,
    "damage_assessment": DamageAssessment,
    "inventory_item": InventoryItem,
    "finance_transaction": FinanceTransaction,
    "work_order_group": WorkOrderGroup,
    "follow_up_task": FollowUpTask,
}

RESOURCE_LABELS = {
    "customer": "客户",
    "drone_device": "设备",
    "repair_order": "维修工单",
    "service_ticket": "服务工单",
    "quote": "报价历史",
    "damage_assessment": "定损结果",
    "inventory_item": "库存内容",
    "finance_transaction": "财务流水",
    "work_order_group": "工单组",
    "follow_up_task": "回访记录",
}


def _resource_label(resource_type: str, resource) -> str:
    if resource_type == "follow_up_task":
        return f"{RESOURCE_LABELS[resource_type]} · #{resource.id}"
    attribute = {
        "customer": "customer_no",
        "drone_device": "serial_number",
        "repair_order": "order_no",
        "service_ticket": "ticket_no",
        "quote": "quote_no",
        "damage_assessment": "assessment_no",
        "inventory_item": "sku",
        "finance_transaction": "transaction_no",
        "work_order_group": "name",
    }[resource_type]
    value = getattr(resource, attribute)
    return f"{RESOURCE_LABELS[resource_type]} · {value}"


def _active_related(db: Session, resource_type: str, resource) -> list:
    affected = [resource]
    if resource_type == "repair_order":
        tickets = list(db.scalars(
            select(ServiceTicket).where(
                ServiceTicket.repair_order_id == resource.id,
                ServiceTicket.deleted_at.is_(None),
            )
        ))
        affected.extend(tickets)
        ticket_ids = [ticket.id for ticket in tickets]
        quote_filter = Quote.repair_order_id == resource.id
        if ticket_ids:
            quote_filter = quote_filter | Quote.service_ticket_id.in_(ticket_ids)
        affected.extend(db.scalars(
            select(Quote).where(quote_filter, Quote.deleted_at.is_(None))
        ))
    elif resource_type == "service_ticket":
        affected.extend(db.scalars(
            select(Quote).where(
                Quote.service_ticket_id == resource.id,
                Quote.deleted_at.is_(None),
            )
        ))
    return list(dict.fromkeys(affected))


def delete_resource(
    db: Session, resource_type: str, resource_id: int, *, user: User
) -> DeletedRecord:
    model = RESOURCE_MODELS.get(resource_type)
    if model is None:
        raise BusinessError("不支持删除该类记录", code="unsupported_delete", status_code=400)
    resource = db.get(model, resource_id)
    if not resource or resource.deleted_at is not None:
        raise BusinessError("记录不存在或已在回收站", code="resource_not_found", status_code=404)

    batch_id = str(uuid4())
    deleted_at = utcnow()
    for item in _active_related(db, resource_type, resource):
        item.deleted_at = deleted_at
        item.deleted_by = user.id
        item.deletion_batch_id = batch_id

    record = DeletedRecord(
        batch_id=batch_id,
        resource_type=resource_type,
        resource_id=resource_id,
        label=_resource_label(resource_type, resource),
        deleted_by=user.id,
        deleted_at=deleted_at,
    )
    db.add(record)
    db.flush()
    return record


def restore_record(db: Session, deletion_id: int, *, user: User) -> DeletedRecord:
    record = db.get(DeletedRecord, deletion_id)
    if not record:
        raise BusinessError("回收站记录不存在", code="deletion_not_found", status_code=404)
    if record.restored_at is not None:
        return record

    if record.resource_type == "quote":
        quote = db.get(Quote, record.resource_id)
        if not quote:
            raise BusinessError(
                "报价记录不存在，无法恢复",
                code="quote_restore_target_invalid",
                status_code=409,
            )
        if quote.service_ticket_id:
            ticket = db.get(ServiceTicket, quote.service_ticket_id)
            if (
                not ticket
                or ticket.deleted_at is not None
                or ticket.ticket_type not in SERVICE_TICKET_QUOTE_TYPES
            ):
                raise BusinessError(
                    "关联服务工单不存在、已删除或当前类型不支持报价，无法恢复该报价",
                    code="quote_restore_target_invalid",
                    status_code=409,
                )

    for model in RESOURCE_MODELS.values():
        for item in db.scalars(select(model).where(model.deletion_batch_id == record.batch_id)):
            item.deleted_at = None
            item.deleted_by = None
            item.deletion_batch_id = None
    record.restored_at = utcnow()
    record.restored_by = user.id
    db.flush()
    return record


def list_deleted_records(db: Session) -> list[dict]:
    records = list(db.scalars(
        select(DeletedRecord)
        .where(DeletedRecord.restored_at.is_(None))
        .order_by(DeletedRecord.deleted_at.desc())
        .limit(500)
    ))
    result: list[dict] = []
    for record in records:
        affected_count = 0
        for model in RESOURCE_MODELS.values():
            affected_count += len(list(db.scalars(
                select(model.id).where(model.deletion_batch_id == record.batch_id)
            )))
        result.append({
            "id": record.id,
            "resource_type": record.resource_type,
            "resource_id": record.resource_id,
            "label": record.label,
            "deleted_by": record.deleted_by,
            "deleted_at": record.deleted_at,
            "affected_count": affected_count,
        })
    return result
