from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models.entities import (
    Customer,
    InventoryItem,
    Quote,
    QuoteItem,
    RepairOrder,
    ServiceTicket,
    ServiceTicketTimeline,
    utcnow,
)
from app.schemas.domain import QuoteCreate
from app.services.numbering import make_no
from app.services.orders import RepairOrderService


CENT = Decimal("0.01")
TERMINAL_ORDER_STATUSES = {"completed", "cancelled"}
SERVICE_TICKET_QUOTE_TYPES = {
    "retail": "零售",
    "replacement": "置换",
}
TERMINAL_SERVICE_TICKET_STATUSES = {"closed", "cancelled"}


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class QuoteService:
    @staticmethod
    def recalculate_order_total(db: Session, order: RepairOrder) -> RepairOrder:
        if order.deleted_at is not None:
            raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
        db.flush()
        latest = db.scalar(
            select(Quote)
            .where(
                Quote.repair_order_id == order.id,
                Quote.deleted_at.is_(None),
            )
            .order_by(Quote.version.desc(), Quote.id.desc())
            .limit(1)
        )
        order.total_quote_amount = money(latest.total_amount if latest else Decimal("0"))
        db.flush()
        return order

    @staticmethod
    def create_version(db: Session, payload: QuoteCreate) -> Quote:
        if bool(payload.repair_order_id) == bool(payload.service_ticket_id):
            raise BusinessError(
                "维修工单与服务工单必须且只能选择一个",
                code="quote_target_invalid",
                status_code=409,
            )
        order = db.get(RepairOrder, payload.repair_order_id) if payload.repair_order_id else None
        ticket = db.get(ServiceTicket, payload.service_ticket_id) if payload.service_ticket_id else None
        if payload.repair_order_id and (not order or order.deleted_at is not None):
            raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
        if payload.service_ticket_id and (not ticket or ticket.deleted_at is not None):
            raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
        if order:
            customer = db.get(Customer, order.customer_id)
            if not customer or customer.deleted_at is not None:
                raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
        if order and order.status in TERMINAL_ORDER_STATUSES:
            raise BusinessError(
                "已完成或已取消的工单不能新建报价",
                code="terminal_order_quote_forbidden",
                status_code=409,
            )
        if ticket and ticket.ticket_type not in SERVICE_TICKET_QUOTE_TYPES:
            raise BusinessError(
                "只有零售或置换工单可直接创建服务工单报价",
                code="service_ticket_quote_type_invalid",
                status_code=409,
            )
        if ticket and ticket.status in TERMINAL_SERVICE_TICKET_STATUSES:
            raise BusinessError(
                "已关闭或已取消的服务工单不能新建报价",
                code="terminal_ticket_quote_forbidden",
                status_code=409,
            )
        if ticket and not ticket.customer_id:
            raise BusinessError("服务工单必须先关联客户", code="customer_required")
        if ticket:
            customer = db.get(Customer, ticket.customer_id)
            if not customer or customer.deleted_at is not None:
                raise BusinessError("客户不存在", code="customer_not_found", status_code=404)

        for item_input in payload.items:
            if Decimal(item_input.quantity) <= 0:
                raise BusinessError("报价项数量必须大于 0", code="invalid_quote_quantity", status_code=409)
            if Decimal(item_input.unit_price) < 0:
                raise BusinessError("报价项单价不能为负数", code="invalid_quote_unit_price", status_code=409)
            if item_input.inventory_item_id:
                inventory = db.get(InventoryItem, item_input.inventory_item_id)
                if not inventory or inventory.deleted_at is not None:
                    raise BusinessError("库存项目不存在", code="inventory_not_found", status_code=404)
                if not inventory.enabled:
                    raise BusinessError("库存项目已停用", code="inventory_disabled", status_code=409)

        target_filter = (
            Quote.repair_order_id == order.id
            if order else Quote.service_ticket_id == ticket.id
        )
        current_version = db.scalar(select(func.max(Quote.version)).where(target_filter)) or 0
        for old in db.scalars(select(Quote).where(
            target_filter,
            Quote.status == "draft",
            Quote.deleted_at.is_(None),
        )):
            old.status = "superseded"
        quote = Quote(
            quote_no=make_no("QT"),
            repair_order_id=order.id if order else None,
            service_ticket_id=ticket.id if ticket else None,
            version=current_version + 1,
            status="draft", discount=money(payload.discount), labor_fee=money(payload.labor_fee), shipping_fee=money(payload.shipping_fee),
            assessment_result=payload.assessment_result if order else None,
            assessment_responsibility=payload.assessment_responsibility if order else None,
            repair_recommendation=payload.repair_recommendation if order else None,
            customer_notice=payload.customer_notice if order else None,
            payment_url=payload.payment_url,
        )
        db.add(quote)
        db.flush()
        subtotal = Decimal("0")
        for item_input in payload.items:
            amount = money(item_input.quantity * item_input.unit_price)
            subtotal += amount
            db.add(QuoteItem(quote_id=quote.id, amount=amount, **item_input.model_dump()))
        quote.subtotal = money(subtotal)
        quote.total_amount = money(max(Decimal("0"), subtotal + quote.labor_fee + quote.shipping_fee - quote.discount))
        if order:
            db.flush()
            QuoteService.recalculate_order_total(db, order)
            RepairOrderService.change_status(db, order, "pending_quote", reason=f"创建报价草稿 {quote.quote_no}")
        else:
            business_label = SERVICE_TICKET_QUOTE_TYPES[ticket.ticket_type]
            db.add(ServiceTicketTimeline(
                ticket_id=ticket.id,
                event_type="quote_created",
                summary=f"创建{business_label}报价草稿 {quote.quote_no}",
                details_json={
                    "quote_id": quote.id,
                    "version": quote.version,
                    "total_amount": str(quote.total_amount),
                    "ticket_type": ticket.ticket_type,
                },
            ))
        db.flush()
        db.refresh(quote)
        return quote

    @staticmethod
    def confirm(db: Session, quote: Quote) -> Quote:
        if quote.deleted_at is not None:
            raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
        if quote.status not in {"draft", "sent"}:
            raise BusinessError("只有草稿或已发送报价可确认", code="invalid_quote_state")
        order = db.get(RepairOrder, quote.repair_order_id) if quote.repair_order_id else None
        ticket = db.get(ServiceTicket, quote.service_ticket_id) if quote.service_ticket_id else None
        if quote.repair_order_id and (not order or order.deleted_at is not None):
            raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
        if quote.service_ticket_id and (not ticket or ticket.deleted_at is not None):
            raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
        if ticket and ticket.ticket_type not in SERVICE_TICKET_QUOTE_TYPES:
            raise BusinessError(
                "该服务工单类型不支持确认报价",
                code="service_ticket_quote_type_invalid",
                status_code=409,
            )
        customer_id = order.customer_id if order else ticket.customer_id if ticket else None
        if customer_id:
            customer = db.get(Customer, customer_id)
            if not customer or customer.deleted_at is not None:
                raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
        if order and order.status in TERMINAL_ORDER_STATUSES:
            raise BusinessError(
                "已完成或已取消的工单不能确认报价",
                code="terminal_order_quote_forbidden",
                status_code=409,
            )
        if ticket and ticket.status in TERMINAL_SERVICE_TICKET_STATUSES:
            raise BusinessError(
                "已关闭或已取消的服务工单不能确认报价",
                code="terminal_ticket_quote_forbidden",
                status_code=409,
            )
        quote.status = "confirmed"
        quote.customer_confirmed_at = utcnow()
        if order:
            RepairOrderService.change_status(db, order, "customer_confirmed", reason=f"客户确认报价 {quote.quote_no}")
        elif ticket:
            business_label = SERVICE_TICKET_QUOTE_TYPES.get(ticket.ticket_type, "服务")
            db.add(ServiceTicketTimeline(
                ticket_id=ticket.id,
                event_type="quote_confirmed",
                summary=f"客户确认{business_label}报价 {quote.quote_no}",
                details_json={
                    "quote_id": quote.id,
                    "version": quote.version,
                    "total_amount": str(quote.total_amount),
                    "ticket_type": ticket.ticket_type,
                },
            ))
        db.flush()
        return quote
