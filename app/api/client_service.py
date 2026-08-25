from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.helpers import ok
from app.core.client_auth import ClientContext, get_client_context
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.models.client import (
    ClientAddress,
    ClientAttachment,
    ClientNotification,
    ClientRepairIntake,
    RecycleCatalogItem,
    RecycleRequest,
    RetailOrder,
)
from app.models.entities import (
    Attachment,
    Customer,
    DroneDevice,
    Quote,
    RepairOrder,
    RepairOrderStatusHistory,
    ServiceTicket,
    ServiceTicketTimeline,
)
from app.schemas.client import (
    ClientRepairCreate,
    ClientReplacementCreate,
    QuoteDecision,
    RecycleDecision,
    RecycleEstimateInput,
    RecycleSave,
)
from app.services.client_auth import add_client_action_log
from app.services.client_uploads import save_client_image, save_client_video
from app.services.numbering import allocate_repair_order_no, make_no
from app.services.tickets import TicketService
from app.storage.local import LocalStorageService
from app.reports.pdf import PdfReportService
from app.services.branding import load_brand_name


router = APIRouter(prefix="/api/client", tags=["client-service"])
def _money(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None

REPAIR_STATUS_LABELS = {
    "pending_inspection": "申请已提交",
    "awaiting_device": "设备待寄出",
    "received": "设备已收到",
    "inspecting": "检测中",
    "pending_quote": "等待报价",
    "quoted": "报价待确认",
    "customer_confirmed": "报价已确认",
    "repairing": "维修中",
    "pending_test": "质检中",
    "pending_shipping": "待发货",
    "shipping": "运输中",
    "completed": "已完成",
    "cancelled": "已取消",
}

RECYCLE_STATUS_LABELS = {
    "draft": "草稿",
    "submitted": "已提交",
    "pending_review": "待审核",
    "quoted": "已报价",
    "pending_customer_confirmation": "待用户确认",
    "accepted": "已确认",
    "rejected": "已拒绝",
    "pending_receipt": "待收货",
    "inspecting": "检测中",
    "price_confirmation": "价格确认",
    "completed": "已完成",
    "cancelled": "已取消",
}

RETAIL_STATUS_LABELS = {
    "pending_payment": "等待门店确认/收款",
    "paid": "已收款",
    "processing": "备货中",
    "shipped": "已发货",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunding": "退款处理中",
    "refunded": "已退款",
}

TICKET_STATUS_LABELS = {
    "open": "已提交",
    "assigned": "已分配顾问",
    "in_progress": "处理中",
    "waiting_customer": "等待客户回复",
    "waiting_internal": "内部处理中",
    "resolved": "已完成",
    "closed": "已关闭",
    "cancelled": "已取消",
}


def _address_snapshot(db: Session, account_id: int, address_id: int | None) -> dict | None:
    if address_id is None:
        return None
    row = db.scalar(
        select(ClientAddress).where(
            ClientAddress.id == address_id, ClientAddress.account_id == account_id
        )
    )
    if not row:
        raise BusinessError("地址不存在", code="address_not_found", status_code=404)
    return {
        "id": row.id,
        "recipient_name": row.recipient_name,
        "phone": row.phone,
        "province": row.province,
        "city": row.city,
        "district": row.district,
        "detail": row.detail,
        "postal_code": row.postal_code,
    }


def _quote_data(row: Quote | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row.id,
        "quote_no": row.quote_no,
        "version": row.version,
        "status": row.status,
        "subtotal": _money(row.subtotal),
        "discount": _money(row.discount),
        "labor_fee": _money(row.labor_fee),
        "shipping_fee": _money(row.shipping_fee),
        "total_amount": _money(row.total_amount),
        "assessment_result": row.assessment_result,
        "repair_recommendation": row.repair_recommendation,
        "customer_notice": row.customer_notice,
        "customer_confirmed_at": row.customer_confirmed_at,
        "items": [
            {
                "id": item.id,
                "name": item.item_name,
                "specification": item.specification,
                "quantity": item.quantity,
                "unit_price": _money(item.unit_price),
                "amount": _money(item.amount),
                "type": item.item_type,
                "remarks": item.remarks,
            }
            for item in sorted(row.items, key=lambda item: (item.sort_order, item.id))
        ],
        "pdf_url": f"/api/client/repair/quotes/{row.id}/pdf",
    }


def _repair_data(db: Session, row: RepairOrder, *, detail: bool = False) -> dict:
    intake = db.scalar(
        select(ClientRepairIntake).where(ClientRepairIntake.repair_order_id == row.id)
    )
    latest_quote = db.scalar(
        select(Quote)
        .where(Quote.repair_order_id == row.id, Quote.deleted_at.is_(None))
        .options(selectinload(Quote.items))
        .order_by(Quote.version.desc())
        .limit(1)
    )
    data = {
        "id": row.id,
        "order_no": row.order_no,
        "status": row.status,
        "status_label": REPAIR_STATUS_LABELS.get(row.status, "处理中"),
        "fault_description": row.fault_description,
        "device": {
            "id": row.device.id,
            "brand": row.device.brand,
            "model": row.device.model,
            "serial_number": row.device.serial_number,
        },
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "current_quote": _quote_data(latest_quote),
    }
    if detail:
        history = db.scalars(
            select(RepairOrderStatusHistory)
            .where(RepairOrderStatusHistory.repair_order_id == row.id)
            .order_by(RepairOrderStatusHistory.changed_at)
        )
        data.update(
            {
                "service_mode": intake.service_mode if intake else None,
                "fault_type": intake.fault_type if intake else None,
                "contact_name": intake.contact_name if intake else None,
                "contact_phone": intake.contact_phone if intake else None,
                "address": intake.address_snapshot_json if intake else None,
                "customer_notes": row.customer_notes,
                "timeline": [
                    {
                        "status": item.to_status,
                        "label": REPAIR_STATUS_LABELS.get(item.to_status, "处理中"),
                        "time": item.changed_at,
                    }
                    for item in history
                ],
            }
        )
    return data


def _estimate(db: Session, payload: RecycleEstimateInput) -> dict:
    item = db.scalar(
        select(RecycleCatalogItem).where(
            RecycleCatalogItem.id == payload.catalog_item_id,
            RecycleCatalogItem.enabled.is_(True),
            RecycleCatalogItem.deleted_at.is_(None),
        )
    )
    if not item:
        raise BusinessError("回收型号不存在", code="recycle_catalog_not_found", status_code=404)
    if payload.condition_codes:
        raise BusinessError(
            "仅旧报价规则已停用；当前仍按机型最高回收价报价，请重新打开回收页面",
            code="recycle_rules_disabled",
            status_code=410,
        )
    maximum_price = _money(item.reference_price)
    return {
        "catalog_item": {
            "id": item.id,
            "brand": item.brand,
            "model": item.model,
            "variant": item.variant,
        },
        "maximum_price": maximum_price,
        "reference_min": maximum_price,
        "reference_max": maximum_price,
        "breakdown": [],
        "notice": "这是该机型的最高回收参考价，最终价格以设备实物检测结果和门店正式报价为准。",
    }


def _recycle_data(row: RecycleRequest) -> dict:
    questionnaire = row.questionnaire_json if isinstance(row.questionnaire_json, dict) else {}
    contact = questionnaire.get("contact")
    contact = contact if isinstance(contact, dict) else {}
    return {
        "id": row.id,
        "request_no": row.request_no,
        "catalog_item_id": row.catalog_item_id,
        "questionnaire": questionnaire,
        "contact_name": contact.get("name"),
        "contact_phone": contact.get("phone"),
        "contact_wechat": contact.get("wechat"),
        "device_condition": questionnaire.get("device_condition"),
        "notes": questionnaire.get("notes"),
        "maximum_price": _money(row.reference_max),
        "reference_min": _money(row.reference_min),
        "reference_max": _money(row.reference_max),
        "staff_quote": _money(row.staff_quote),
        "status": row.status,
        "status_label": RECYCLE_STATUS_LABELS.get(row.status, "处理中"),
        "user_decision": row.user_decision,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "notice": "页面展示的是最高回收参考价，最终价格以设备实物检测结果和门店正式报价为准。",
    }


def _owned_repair(db: Session, order_id: int, customer_id: int) -> RepairOrder:
    row = db.scalar(
        select(RepairOrder)
        .where(
            RepairOrder.id == order_id,
            RepairOrder.customer_id == customer_id,
            RepairOrder.deleted_at.is_(None),
        )
        .options(joinedload(RepairOrder.device))
    )
    if not row:
        raise BusinessError("维修工单不存在", code="repair_not_found", status_code=404)
    return row


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _replacement_event(db: Session, ticket_id: int) -> ServiceTicketTimeline | None:
    return db.scalar(
        select(ServiceTicketTimeline)
        .where(
            ServiceTicketTimeline.ticket_id == ticket_id,
            ServiceTicketTimeline.event_type == "client_replacement_submitted",
        )
        .order_by(ServiceTicketTimeline.id.desc())
    )


def _replacement_data(db: Session, row: ServiceTicket) -> dict:
    event = _replacement_event(db, row.id)
    details = event.details_json if event and isinstance(event.details_json, dict) else {}
    return {
        "id": row.id,
        "ticket_no": row.ticket_no,
        "status": row.status,
        "status_label": TICKET_STATUS_LABELS.get(row.status, "处理中"),
        "old_model": details.get("old_model"),
        "desired_model": details.get("desired_model"),
        "contact_name": details.get("contact_name"),
        "contact_phone": details.get("contact_phone"),
        "address": details.get("address"),
        "notes": details.get("notes"),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "notice": "服务顾问会在一个工作日内联系您，请保持电话畅通",
    }


@router.get("/work-items")
def work_items(
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    """Merge shop orders, repair, recycle and client replacement requests."""
    repairs = list(
        db.scalars(
            select(RepairOrder)
            .where(
                RepairOrder.customer_id == context.account.customer_id,
                RepairOrder.deleted_at.is_(None),
            )
            .options(joinedload(RepairOrder.device))
        ).unique()
    )
    retail_orders = list(
        db.scalars(
            select(RetailOrder)
            .where(RetailOrder.account_id == context.account.id)
            .options(selectinload(RetailOrder.items))
        )
    )
    recycle_requests = list(
        db.scalars(
            select(RecycleRequest).where(
                RecycleRequest.account_id == context.account.id
            )
        )
    )
    replacement_tickets = list(
        db.scalars(
            select(ServiceTicket)
            .join(
                ServiceTicketTimeline,
                ServiceTicketTimeline.ticket_id == ServiceTicket.id,
            )
            .where(
                ServiceTicket.customer_id == context.account.customer_id,
                ServiceTicket.ticket_type == "replacement",
                ServiceTicket.deleted_at.is_(None),
                ServiceTicketTimeline.event_type == "client_replacement_submitted",
            )
        ).unique()
    )
    catalog_ids = {row.catalog_item_id for row in recycle_requests}
    catalog = {
        row.id: row
        for row in db.scalars(
            select(RecycleCatalogItem).where(RecycleCatalogItem.id.in_(catalog_ids))
        )
    } if catalog_ids else {}

    items: list[dict] = []
    for row in repairs:
        items.append(
            {
                "key": f"repair:{row.id}",
                "type": "repair",
                "type_label": "维修工单",
                "id": row.id,
                "number": row.order_no,
                "title": f"{row.device.brand} {row.device.model}",
                "summary": row.fault_description,
                "status": row.status,
                "status_label": REPAIR_STATUS_LABELS.get(row.status, "处理中"),
                "amount": None,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    for row in retail_orders:
        first = row.items[0].product_name if row.items else "商城商品"
        suffix = f" 等 {len(row.items)} 件" if len(row.items) > 1 else ""
        items.append(
            {
                "key": f"retail:{row.id}",
                "type": "retail",
                "type_label": "商城订单",
                "id": row.id,
                "number": row.order_no,
                "title": f"{first}{suffix}",
                "summary": "门店配送" if row.delivery_method == "shipping" else "到店自取",
                "status": row.status,
                "status_label": RETAIL_STATUS_LABELS.get(row.status, row.status),
                "amount": _money(row.total_amount),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    for row in recycle_requests:
        model = catalog.get(row.catalog_item_id)
        title = (
            f"{model.brand} {model.model}{' ' + model.variant if model.variant else ''}"
            if model
            else "设备回收"
        )
        items.append(
            {
                "key": f"recycle:{row.id}",
                "type": "recycle",
                "type_label": "回收申请",
                "id": row.id,
                "number": row.request_no,
                "title": title,
                "summary": "最终价格以设备实物检测结果为准",
                "status": row.status,
                "status_label": RECYCLE_STATUS_LABELS.get(row.status, "处理中"),
                "amount": _money(row.staff_quote or row.reference_max),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    for row in replacement_tickets:
        details = _replacement_event(db, row.id)
        payload = details.details_json if details and isinstance(details.details_json, dict) else {}
        old_model = payload.get("old_model") or "旧设备"
        desired_model = payload.get("desired_model") or "需求设备"
        items.append(
            {
                "key": f"replacement:{row.id}",
                "type": "replacement",
                "type_label": "置换工单",
                "id": row.id,
                "number": row.ticket_no,
                "title": f"{old_model} → {desired_model}",
                "summary": "服务顾问将在一个工作日内联系",
                "status": row.status,
                "status_label": TICKET_STATUS_LABELS.get(row.status, "处理中"),
                "amount": None,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    items.sort(key=lambda item: item["updated_at"] or item["created_at"], reverse=True)
    return ok(items)


@router.post("/replacement", status_code=201)
def create_replacement(
    payload: ClientReplacementCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    existing_event = db.scalar(
        select(ServiceTicketTimeline).where(
            ServiceTicketTimeline.event_type == "client_replacement_submitted",
            ServiceTicketTimeline.details_json["idempotency_key"].as_string() == idempotency_key,
        )
    )
    if existing_event:
        existing_ticket = db.get(ServiceTicket, existing_event.ticket_id)
        if not existing_ticket or existing_ticket.customer_id != context.account.customer_id:
            raise BusinessError("重复请求标识冲突", code="idempotency_conflict", status_code=409)
        return ok(_replacement_data(db, existing_ticket))

    address = _address_snapshot(db, context.account.id, payload.address_id)
    assert address is not None
    old_model = _single_line(payload.old_model)
    desired_model = _single_line(payload.desired_model)
    contact_name = _single_line(payload.contact_name)
    contact_phone = _single_line(payload.contact_phone)
    notes = _single_line(payload.notes) if payload.notes else None
    address_text = "".join(
        str(address.get(key) or "")
        for key in ("province", "city", "district", "detail")
    )
    description_lines = [
        f"旧机型：{old_model}",
        f"需求机型：{desired_model}",
        f"联系人：{contact_name}",
        f"联系电话：{contact_phone}",
        f"联系地址：{address_text}",
    ]
    if notes:
        description_lines.append(f"补充说明：{notes}")
    ticket = ServiceTicket(
        ticket_no=make_no("TKT"),
        ticket_type="replacement",
        title=f"客户置换申请：{old_model} → {desired_model}"[:240],
        description="\n".join(description_lines),
        status="open",
        priority="normal",
        customer_id=context.account.customer_id,
        created_by=None,
    )
    db.add(ticket)
    db.flush()
    db.add(
        ServiceTicketTimeline(
            ticket_id=ticket.id,
            event_type="created",
            summary="由客户置换申请建立统一服务工单",
            from_status=None,
            to_status="open",
            details_json={"description": ticket.description, "version": 1, "is_original": True},
            actor_id=None,
        )
    )
    details = {
        "idempotency_key": idempotency_key,
        "account_id": context.account.id,
        "old_model": old_model,
        "desired_model": desired_model,
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "address": address,
        "notes": notes,
    }
    db.add(
        ServiceTicketTimeline(
            ticket_id=ticket.id,
            event_type="client_replacement_submitted",
            summary="客户从客户端提交置换工单",
            from_status=None,
            to_status="open",
            details_json=details,
            actor_id=None,
        )
    )
    db.add(
        ClientNotification(
            account_id=context.account.id,
            notification_type="replacement_submitted",
            title="置换工单已提交",
            content=f"工单 {ticket.ticket_no} 已进入服务顾问处理队列。",
            resource_type="service_ticket",
            resource_id=ticket.id,
        )
    )
    add_client_action_log(
        db,
        request,
        action="client.replacement.submit",
        account=context.account,
        resource_type="service_ticket",
        resource_id=ticket.id,
    )
    db.commit()
    db.refresh(ticket)
    return ok(_replacement_data(db, ticket))


@router.get("/replacement/{ticket_id}")
def replacement_detail(
    ticket_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(ServiceTicket)
        .join(ServiceTicketTimeline, ServiceTicketTimeline.ticket_id == ServiceTicket.id)
        .where(
            ServiceTicket.id == ticket_id,
            ServiceTicket.customer_id == context.account.customer_id,
            ServiceTicket.ticket_type == "replacement",
            ServiceTicket.deleted_at.is_(None),
            ServiceTicketTimeline.event_type == "client_replacement_submitted",
        )
    )
    if not row:
        raise BusinessError("置换工单不存在", code="replacement_not_found", status_code=404)
    return ok(_replacement_data(db, row))


@router.get("/devices")
def my_devices(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    rows = db.scalars(
        select(DroneDevice)
        .where(
            DroneDevice.customer_id == context.account.customer_id,
            DroneDevice.deleted_at.is_(None),
        )
        .order_by(DroneDevice.created_at.desc())
    )
    return ok(
        [
            {
                "id": row.id,
                "brand": row.brand,
                "model": row.model,
                "serial_number": row.serial_number,
                "warranty_status": row.warranty_status,
            }
            for row in rows
        ]
    )


@router.post("/repair", status_code=201)
def create_repair(
    payload: ClientRepairCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    existing = db.scalar(
        select(ClientRepairIntake).where(
            ClientRepairIntake.idempotency_key == idempotency_key
        )
    )
    if existing:
        if existing.account_id != context.account.id:
            raise BusinessError("重复请求标识冲突", code="idempotency_conflict", status_code=409)
        return ok(_repair_data(db, _owned_repair(db, existing.repair_order_id, context.account.customer_id), detail=True))
    if payload.device_id:
        device = db.scalar(
            select(DroneDevice).where(
                DroneDevice.id == payload.device_id,
                DroneDevice.customer_id == context.account.customer_id,
                DroneDevice.deleted_at.is_(None),
            )
        )
        if not device:
            raise BusinessError("设备不存在", code="device_not_found", status_code=404)
    else:
        device = DroneDevice(
            customer_id=context.account.customer_id,
            brand=payload.brand,
            model=payload.model,
            serial_number=payload.serial_number,
            is_temporary=False,
        )
        db.add(device)
        db.flush()
    address = _address_snapshot(db, context.account.id, payload.address_id)
    intake_condition = {
        "fault_type": payload.fault_type,
        "has_water_damage": payload.has_water_damage,
        "has_crash_damage": payload.has_crash_damage,
        "was_disassembled": payload.was_disassembled,
        "current_state": payload.current_state,
        "service_mode": payload.service_mode,
    }
    order = RepairOrder(
        order_no=allocate_repair_order_no(db),
        source_request_key=f"client:{idempotency_key}",
        customer_id=context.account.customer_id,
        device_id=device.id,
        fault_description=payload.fault_description,
        intake_condition=json.dumps(intake_condition, ensure_ascii=False),
        intake_accessories=json.dumps(payload.accessories, ensure_ascii=False),
        status="pending_inspection",
        priority="normal",
        customer_notes=payload.notes,
    )
    db.add(order)
    db.flush()
    db.add(
        RepairOrderStatusHistory(
            repair_order_id=order.id,
            from_status=None,
            to_status=order.status,
            reason="客户提交维修申请",
        )
    )
    db.add(
        ClientRepairIntake(
            account_id=context.account.id,
            repair_order_id=order.id,
            service_mode=payload.service_mode,
            fault_type=payload.fault_type,
            has_water_damage=payload.has_water_damage,
            has_crash_damage=payload.has_crash_damage,
            was_disassembled=payload.was_disassembled,
            current_state=payload.current_state,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            address_snapshot_json=address,
            extra_json={"accessories": payload.accessories, "notes": payload.notes},
            idempotency_key=idempotency_key,
        )
    )
    TicketService.ensure_for_repair_order(db, order, created_by=None)
    db.add(
        ClientNotification(
            account_id=context.account.id,
            notification_type="repair_submitted",
            title="维修申请已提交",
            content=f"工单 {order.order_no} 已进入门店工作台。",
            resource_type="repair_order",
            resource_id=order.id,
        )
    )
    add_client_action_log(
        db,
        request,
        action="client.repair.submit",
        account=context.account,
        resource_type="repair_order",
        resource_id=order.id,
    )
    db.commit()
    db.refresh(order)
    order.device = device
    return ok(_repair_data(db, order, detail=True))


@router.get("/repair")
def repairs(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    rows = db.scalars(
        select(RepairOrder)
        .where(
            RepairOrder.customer_id == context.account.customer_id,
            RepairOrder.deleted_at.is_(None),
        )
        .options(joinedload(RepairOrder.device))
        .order_by(RepairOrder.created_at.desc())
    )
    return ok([_repair_data(db, row) for row in rows])


@router.get("/repair/{order_id}")
def repair_detail(
    order_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    return ok(_repair_data(db, _owned_repair(db, order_id, context.account.customer_id), detail=True))


@router.post("/repair/{order_id}/quote-decision")
def repair_quote_decision(
    order_id: int,
    payload: QuoteDecision,
    request: Request,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    order = _owned_repair(db, order_id, context.account.customer_id)
    quote = db.scalar(
        select(Quote)
        .where(Quote.repair_order_id == order.id, Quote.deleted_at.is_(None))
        .options(selectinload(Quote.items))
        .order_by(Quote.version.desc())
        .limit(1)
    )
    if not quote:
        raise BusinessError("当前没有可确认的报价", code="quote_not_found", status_code=404)
    if quote.status in {"confirmed", "rejected"}:
        raise BusinessError("该报价已经处理", code="quote_already_decided", status_code=409)
    previous = order.status
    now = datetime.now(timezone.utc)
    if payload.decision == "accepted":
        quote.status = "confirmed"
        quote.customer_confirmed_at = now
        order.status = "customer_confirmed"
    else:
        quote.status = "rejected"
        order.status = "pending_quote"
    if previous != order.status:
        db.add(
            RepairOrderStatusHistory(
                repair_order_id=order.id,
                from_status=previous,
                to_status=order.status,
                reason="客户接受报价" if payload.decision == "accepted" else "客户拒绝报价",
            )
        )
    TicketService.sync_repair_order_status(
        db, order, actor_id=None, reason="客户报价确认结果已同步"
    )
    add_client_action_log(
        db,
        request,
        action=f"client.repair.quote.{payload.decision}",
        account=context.account,
        resource_type="quote",
        resource_id=quote.id,
        details={"quote_version": quote.version, "repair_order_id": order.id},
    )
    db.commit()
    return ok(_repair_data(db, order, detail=True))


@router.get("/repair/quotes/{quote_id}/pdf")
def repair_quote_pdf(
    quote_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> FileResponse:
    quote = db.scalar(
        select(Quote)
        .join(RepairOrder, RepairOrder.id == Quote.repair_order_id)
        .where(
            Quote.id == quote_id,
            Quote.deleted_at.is_(None),
            RepairOrder.customer_id == context.account.customer_id,
            RepairOrder.deleted_at.is_(None),
        )
        .options(
            selectinload(Quote.items),
            joinedload(Quote.repair_order).joinedload(RepairOrder.customer),
            joinedload(Quote.repair_order).joinedload(RepairOrder.device),
        )
    )
    if not quote:
        raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
    path = PdfReportService(brand_name=load_brand_name(db)).quote(quote)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.post("/repair/{order_id}/attachments", status_code=201)
async def upload_repair_attachment(
    order_id: int,
    file: UploadFile = File(...),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    order = _owned_repair(db, order_id, context.account.customer_id)
    count = db.scalar(
        select(func.count(ClientAttachment.id)).where(
            ClientAttachment.account_id == context.account.id,
            ClientAttachment.resource_type == "repair",
            ClientAttachment.resource_id == order.id,
        )
    ) or 0
    if count >= settings.client_max_uploads_per_resource:
        raise BusinessError("附件数量已达上限", code="attachment_limit_reached", status_code=409)
    limit = settings.client_max_video_bytes if (file.content_type or "").startswith("video/") else settings.client_max_image_bytes
    content = await file.read(limit + 1)
    if (file.content_type or "").startswith("video/"):
        stored = save_client_video(
            filename=file.filename or "video.mp4",
            content_type=file.content_type,
            content=content,
            folder=f"repairs/{order.id}",
        )
        kind = "video"
    else:
        stored = save_client_image(
            filename=file.filename or "image.jpg",
            content_type=file.content_type,
            content=content,
            folder=f"repairs/{order.id}",
        )
        kind = "image"
    client_attachment = ClientAttachment(
        account_id=context.account.id,
        resource_type="repair",
        resource_id=order.id,
        attachment_type=kind,
        original_filename=file.filename or stored.original_filename,
        storage_path=stored.storage_path,
        content_type=stored.content_type,
        file_size=stored.file_size,
        sha256=stored.sha256,
    )
    db.add(client_attachment)
    db.add(
        Attachment(
            customer_id=order.customer_id,
            repair_order_id=order.id,
            attachment_type=f"client_{kind}",
            original_filename=file.filename or stored.original_filename,
            storage_path=stored.storage_path,
            content_type=stored.content_type,
            file_size=stored.file_size,
            sha256=stored.sha256,
            uploaded_by=None,
        )
    )
    db.commit()
    db.refresh(client_attachment)
    return ok(
        {
            "id": client_attachment.id,
            "type": kind,
            "filename": client_attachment.original_filename,
            "url": f"/api/client/attachments/{client_attachment.id}",
        }
    )


@router.get("/recycle/catalog")
def recycle_catalog(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(RecycleCatalogItem)
        .where(
            RecycleCatalogItem.enabled.is_(True),
            RecycleCatalogItem.deleted_at.is_(None),
        )
        .order_by(RecycleCatalogItem.sort_order, RecycleCatalogItem.brand, RecycleCatalogItem.model)
    )
    return ok(
        [
            {
                "id": row.id,
                "brand": row.brand,
                "model": row.model,
                "variant": row.variant,
                "maximum_price": _money(row.reference_price),
            }
            for row in rows
        ]
    )


@router.get("/recycle/rules")
def recycle_rules() -> dict:
    raise BusinessError(
        "仅旧报价规则已停用；旧机最高价报价功能正常可用",
        code="recycle_rules_disabled",
        status_code=410,
    )


@router.post("/recycle/estimate")
def recycle_estimate(payload: RecycleEstimateInput, db: Session = Depends(get_db)) -> dict:
    return ok(_estimate(db, payload))


@router.post("/recycle", status_code=201)
def save_recycle(
    payload: RecycleSave,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=100),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    if payload.submit and (not idempotency_key or len(idempotency_key) < 8):
        raise BusinessError(
            "提交回收申请需要有效的重复请求标识",
            code="idempotency_key_required",
            status_code=400,
        )
    if idempotency_key:
        existing = db.scalar(
            select(RecycleRequest).where(RecycleRequest.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.account_id != context.account.id:
                raise BusinessError("重复请求标识冲突", code="idempotency_conflict", status_code=409)
            return ok(_recycle_data(existing))
    estimate = _estimate(
        db,
        RecycleEstimateInput(
            catalog_item_id=payload.catalog_item_id,
            condition_codes=payload.condition_codes,
            details=payload.details,
        ),
    )
    if payload.request_id:
        row = db.scalar(
            select(RecycleRequest).where(
                RecycleRequest.id == payload.request_id,
                RecycleRequest.account_id == context.account.id,
                RecycleRequest.status == "draft",
            )
        )
        if not row:
            raise BusinessError("回收草稿不存在", code="recycle_draft_not_found", status_code=404)
    else:
        row = RecycleRequest(
            request_no=make_no("RC"),
            account_id=context.account.id,
            customer_id=context.account.customer_id,
            catalog_item_id=payload.catalog_item_id,
            questionnaire_json={},
            reference_min=estimate["reference_min"],
            reference_max=estimate["reference_max"],
            status="draft",
        )
        db.add(row)
    row.catalog_item_id = payload.catalog_item_id
    row.questionnaire_json = {
        "contact": {
            "name": payload.contact_name,
            "phone": payload.contact_phone,
            "wechat": payload.contact_wechat,
        },
        "device_condition": payload.device_condition,
        "notes": payload.notes,
        "details": payload.details,
    }
    row.reference_min = estimate["reference_min"]
    row.reference_max = estimate["reference_max"]
    if payload.submit:
        row.status = "submitted"
        row.idempotency_key = idempotency_key
        db.flush()
        ticket = ServiceTicket(
            ticket_no=make_no("TKT"),
            ticket_type="recycle",
            title=f"客户回收申请：{row.request_no}",
            description=(
                f"最高回收参考价 {row.reference_max}，联系人 {payload.contact_name}，"
                f"电话 {payload.contact_phone}，待人工审核"
            ),
            status="open",
            priority="normal",
            customer_id=context.account.customer_id,
            created_by=None,
        )
        db.add(ticket)
        db.flush()
        row.service_ticket_id = ticket.id
        db.add(
            ClientNotification(
                account_id=context.account.id,
                notification_type="recycle_submitted",
                title="回收申请已提交",
                content=f"申请 {row.request_no} 已进入门店审核。",
                resource_type="recycle_request",
                resource_id=row.id,
            )
        )
        add_client_action_log(
            db,
            request,
            action="client.recycle.submit",
            account=context.account,
            resource_type="recycle_request",
            resource_id=row.id,
        )
    db.commit()
    db.refresh(row)
    return ok(_recycle_data(row))


@router.get("/recycle")
def recycle_requests(
    context: ClientContext = Depends(get_client_context), db: Session = Depends(get_db)
) -> dict:
    rows = db.scalars(
        select(RecycleRequest)
        .where(RecycleRequest.account_id == context.account.id)
        .order_by(RecycleRequest.created_at.desc())
    )
    return ok([_recycle_data(row) for row in rows])


@router.get("/recycle/{request_id}")
def recycle_request_detail(
    request_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(RecycleRequest).where(
            RecycleRequest.id == request_id,
            RecycleRequest.account_id == context.account.id,
        )
    )
    if not row:
        raise BusinessError("回收申请不存在", code="recycle_request_not_found", status_code=404)
    return ok(_recycle_data(row))


@router.post("/recycle/{request_id}/decision")
def recycle_decision(
    request_id: int,
    payload: RecycleDecision,
    request: Request,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(RecycleRequest).where(
            RecycleRequest.id == request_id,
            RecycleRequest.account_id == context.account.id,
        )
    )
    if not row:
        raise BusinessError("回收申请不存在", code="recycle_request_not_found", status_code=404)
    if row.staff_quote is None or row.status not in {"quoted", "pending_customer_confirmation"}:
        raise BusinessError("当前没有可确认的正式报价", code="recycle_quote_not_ready", status_code=409)
    row.user_decision = payload.decision
    row.decision_at = datetime.now(timezone.utc)
    row.status = "accepted" if payload.decision == "accepted" else "rejected"
    ticket = db.get(ServiceTicket, row.service_ticket_id) if row.service_ticket_id else None
    if ticket:
        ticket.status = "in_progress" if payload.decision == "accepted" else "cancelled"
    add_client_action_log(
        db,
        request,
        action=f"client.recycle.quote.{payload.decision}",
        account=context.account,
        resource_type="recycle_request",
        resource_id=row.id,
        details={"staff_quote": str(row.staff_quote)},
    )
    db.commit()
    return ok(_recycle_data(row))


@router.post("/recycle/{request_id}/attachments", status_code=201)
async def upload_recycle_attachment(
    request_id: int,
    file: UploadFile = File(...),
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(RecycleRequest).where(
            RecycleRequest.id == request_id,
            RecycleRequest.account_id == context.account.id,
        )
    )
    if not row:
        raise BusinessError("回收申请不存在", code="recycle_request_not_found", status_code=404)
    count = db.scalar(
        select(func.count(ClientAttachment.id)).where(
            ClientAttachment.account_id == context.account.id,
            ClientAttachment.resource_type == "recycle",
            ClientAttachment.resource_id == row.id,
        )
    ) or 0
    if count >= settings.client_max_uploads_per_resource:
        raise BusinessError("附件数量已达上限", code="attachment_limit_reached", status_code=409)
    content = await file.read(settings.client_max_image_bytes + 1)
    stored = save_client_image(
        filename=file.filename or "image.jpg",
        content_type=file.content_type,
        content=content,
        folder=f"recycle/{row.id}",
    )
    attachment = ClientAttachment(
        account_id=context.account.id,
        resource_type="recycle",
        resource_id=row.id,
        attachment_type="image",
        original_filename=file.filename or stored.original_filename,
        storage_path=stored.storage_path,
        content_type=stored.content_type,
        file_size=stored.file_size,
        sha256=stored.sha256,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return ok({"id": attachment.id, "url": f"/api/client/attachments/{attachment.id}"})


@router.get("/attachments/{attachment_id}")
def client_attachment(
    attachment_id: int,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> FileResponse:
    row = db.scalar(
        select(ClientAttachment).where(
            ClientAttachment.id == attachment_id,
            ClientAttachment.account_id == context.account.id,
        )
    )
    if not row:
        raise BusinessError("附件不存在", code="client_attachment_not_found", status_code=404)
    path = LocalStorageService().absolute_path(row.storage_path)
    if not path.is_file():
        raise BusinessError("附件文件已丢失", code="attachment_file_missing", status_code=404)
    return FileResponse(path, media_type=row.content_type, filename=row.original_filename)
