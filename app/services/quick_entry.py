from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import BusinessError
from app.models.entities import Customer, DroneDevice, Quote, RepairOrder, RepairOrderStatusHistory
from app.schemas.domain import QuickEntryCreate, QuoteCreate, QuoteItemInput
from app.services.numbering import allocate_repair_order_no, make_no
from app.services.quotes import QuoteService
from app.services.tickets import TicketService


class QuickEntryService:
    @staticmethod
    def _same_request(
        payload: QuickEntryCreate,
        customer: Customer,
        device: DroneDevice,
        order: RepairOrder,
        quote: Quote | None,
    ) -> bool:
        if quote is None:
            return False
        expected = {
            "customer_name": payload.customer_name,
            "phone": payload.phone,
            "email": payload.email,
            "brand": payload.brand,
            "model": payload.model,
            "serial_number": payload.serial_number,
            "warranty_status": payload.warranty_status,
            "fault_description": payload.fault_description,
            "intake_condition": payload.intake_condition,
            "intake_accessories": payload.intake_accessories,
            "priority": payload.priority,
            "customer_notes": payload.customer_notes,
        }
        actual = {
            "customer_name": customer.name,
            "phone": customer.phone,
            "email": customer.email,
            "brand": device.brand,
            "model": device.model,
            "serial_number": None if device.is_temporary else device.serial_number,
            "warranty_status": device.warranty_status,
            "fault_description": order.fault_description,
            "intake_condition": order.intake_condition,
            "intake_accessories": order.intake_accessories,
            "priority": order.priority,
            "customer_notes": order.customer_notes,
        }
        if actual != expected:
            return False
        if (
            quote.labor_fee != payload.labor_fee
            or quote.shipping_fee != payload.shipping_fee
            or quote.discount != payload.discount
            or quote.payment_url != payload.payment_url
        ):
            return False
        actual_items = [
            (
                item.inventory_item_id,
                item.item_name,
                item.quantity,
                item.unit_price,
                item.cost_price,
                item.item_type,
                item.remarks,
            )
            for item in sorted(quote.items, key=lambda row: (row.sort_order, row.id))
        ]
        expected_items = [
            (
                item.inventory_item_id,
                item.item_name,
                item.quantity,
                item.unit_price,
                item.cost_price,
                item.item_type,
                item.remarks,
            )
            for item in payload.items
        ]
        return actual_items == expected_items

    @staticmethod
    def create(
        db: Session, payload: QuickEntryCreate, *, request_key: str | None = None,
        created_by: int | None = None,
    ) -> tuple[Customer, DroneDevice, RepairOrder, Quote, bool]:
        if request_key:
            existing_order = db.scalar(select(RepairOrder).where(RepairOrder.source_request_key == request_key))
            if existing_order:
                quote = db.scalar(select(Quote).where(Quote.repair_order_id == existing_order.id).order_by(Quote.version.desc()).options(selectinload(Quote.items)))
                if existing_order.deleted_at is not None:
                    raise BusinessError("幂等请求对应的工单已删除", code="order_not_found", status_code=404)
                if not QuickEntryService._same_request(
                    payload, existing_order.customer, existing_order.device, existing_order, quote
                ):
                    raise BusinessError(
                        "相同 Idempotency-Key 对应的快捷录入内容不同",
                        code="idempotency_payload_mismatch",
                        status_code=409,
                    )
                return existing_order.customer, existing_order.device, existing_order, quote, True
        customer = None
        if payload.phone:
            customer = db.scalar(select(Customer).where(
                Customer.phone == payload.phone,
                Customer.deleted_at.is_(None),
            ))
        if not customer and payload.email:
            customer = db.scalar(select(Customer).where(
                Customer.email == payload.email,
                Customer.deleted_at.is_(None),
            ).order_by(Customer.id))
        if not customer:
            customer = Customer(
                customer_no=make_no("CU"), name=payload.customer_name, phone=payload.phone, email=payload.email,
                wechat=payload.wechat, customer_type=payload.customer_type, company_name=payload.company_name,
                address=payload.address, notes="由快捷录入创建",
            )
            db.add(customer)
            db.flush()
        else:
            customer.name = payload.customer_name
            customer.email = customer.email or payload.email
            customer.wechat = customer.wechat or payload.wechat
            customer.company_name = customer.company_name or payload.company_name
            customer.address = customer.address or payload.address
        serial = payload.serial_number or make_no("TEMP-DEV")
        # 每次收机都建立独立归属记录。序列号不是所有权唯一键，二手流转时
        # 同一序列号可以对应不同客户，同时旧工单仍保留原客户关系。
        device = DroneDevice(
            customer_id=customer.id, brand=payload.brand, model=payload.model, serial_number=serial,
            warranty_status=payload.warranty_status, is_temporary=payload.serial_number is None,
            remarks="由快捷录入创建；独立设备归属记录",
        )
        db.add(device)
        db.flush()
        order = RepairOrder(
            order_no=allocate_repair_order_no(db), source_request_key=request_key,
            customer_id=customer.id, device_id=device.id,
            fault_description=payload.fault_description, intake_condition=payload.intake_condition,
            intake_accessories=payload.intake_accessories, priority=payload.priority,
            customer_notes=payload.customer_notes,
        )
        db.add(order)
        db.flush()
        db.add(RepairOrderStatusHistory(repair_order_id=order.id, from_status=None, to_status=order.status, reason="快捷录入创建工单"))
        TicketService.ensure_for_repair_order(db, order, created_by=created_by)
        quote_payload = QuoteCreate(
            repair_order_id=order.id, labor_fee=payload.labor_fee, shipping_fee=payload.shipping_fee,
            discount=payload.discount, payment_url=payload.payment_url,
            items=[QuoteItemInput(**item.model_dump(), sort_order=index) for index, item in enumerate(payload.items)],
        )
        quote = QuoteService.create_version(db, quote_payload)
        db.flush()
        return customer, device, order, quote, False
