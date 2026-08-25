from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import FinanceTransactionType
from app.core.exceptions import BusinessError
from app.models.entities import (
    Customer,
    FinanceTransaction,
    PurchaseOrder,
    Quote,
    RepairOrder,
    ServiceTicket,
    utcnow,
)
from app.schemas.domain import FinanceCreate, FinanceUpdate
from app.services.numbering import make_no
from app.services.orders import RepairOrderService


class FinanceService:
    @staticmethod
    def _validated_type(value: str) -> str:
        try:
            return FinanceTransactionType(value).value
        except ValueError as exc:
            raise BusinessError("未知的财务流水类型", code="invalid_finance_type") from exc

    @staticmethod
    def _active_customer(db: Session, customer_id: int) -> Customer:
        customer = db.get(Customer, customer_id)
        if not customer or customer.deleted_at is not None:
            raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
        return customer

    @staticmethod
    def _active_order(db: Session, order_id: int) -> RepairOrder:
        order = db.get(RepairOrder, order_id)
        if not order or order.deleted_at is not None:
            raise BusinessError("工单不存在", code="order_not_found", status_code=404)
        FinanceService._active_customer(db, order.customer_id)
        return order

    @staticmethod
    def _resolve_target(
        db: Session,
        payload: FinanceCreate | FinanceUpdate,
        *,
        tx_type: str,
        current_tx_id: int | None = None,
    ) -> tuple[int | None, int | None]:
        if payload.purchase_order_id:
            if payload.repair_order_id or payload.quote_id or payload.customer_id:
                raise BusinessError(
                    "采购付款不能同时关联工单、报价或客户",
                    code="finance_target_conflict",
                    status_code=409,
                )
            purchase_order = db.get(PurchaseOrder, payload.purchase_order_id)
            if not purchase_order:
                raise BusinessError("采购单不存在", code="purchase_order_not_found", status_code=404)
            if tx_type != FinanceTransactionType.EXPENSE.value:
                raise BusinessError(
                    "采购单只能关联支出流水",
                    code="purchase_payment_type_invalid",
                    status_code=409,
                )
            paid_stmt = select(func.coalesce(func.sum(FinanceTransaction.amount), 0)).where(
                FinanceTransaction.purchase_order_id == purchase_order.id,
                FinanceTransaction.transaction_type == FinanceTransactionType.EXPENSE.value,
                FinanceTransaction.deleted_at.is_(None),
            )
            if current_tx_id is not None:
                paid_stmt = paid_stmt.where(FinanceTransaction.id != current_tx_id)
            paid = Decimal(db.scalar(paid_stmt) or 0)
            if paid + Decimal(payload.amount) > Decimal(purchase_order.total_amount):
                remaining = max(Decimal("0"), Decimal(purchase_order.total_amount) - paid)
                raise BusinessError(
                    f"付款金额超过剩余应付 ¥{remaining:.2f}",
                    code="purchase_overpayment",
                    status_code=409,
                )
            return None, None

        quote = db.get(Quote, payload.quote_id) if payload.quote_id else None
        if payload.quote_id and (not quote or quote.deleted_at is not None):
            raise BusinessError("报价不存在", code="quote_not_found", status_code=404)

        order: RepairOrder | None = None
        customer_id: int | None = None
        if quote:
            if payload.repair_order_id and payload.repair_order_id != quote.repair_order_id:
                raise BusinessError(
                    "报价与所选工单不一致",
                    code="finance_quote_order_mismatch",
                    status_code=409,
                )
            if quote.repair_order_id:
                order = FinanceService._active_order(db, quote.repair_order_id)
                customer_id = order.customer_id
            elif quote.service_ticket_id:
                ticket = db.get(ServiceTicket, quote.service_ticket_id)
                if not ticket or ticket.deleted_at is not None:
                    raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
                customer_id = ticket.customer_id
                if customer_id is None:
                    raise BusinessError("报价未关联客户", code="quote_customer_missing", status_code=409)
                FinanceService._active_customer(db, customer_id)
            else:
                raise BusinessError("报价未关联业务工单", code="quote_target_missing", status_code=409)
        elif payload.repair_order_id:
            order = FinanceService._active_order(db, payload.repair_order_id)
            customer_id = order.customer_id
        elif payload.customer_id:
            customer_id = FinanceService._active_customer(db, payload.customer_id).id

        if customer_id is not None and payload.customer_id is not None and payload.customer_id != customer_id:
            raise BusinessError(
                "财务客户与工单客户不一致",
                code="finance_customer_mismatch",
                status_code=409,
            )
        return order.id if order else None, customer_id

    @staticmethod
    def create(db: Session, payload: FinanceCreate) -> FinanceTransaction:
        tx_type = FinanceService._validated_type(payload.transaction_type)
        repair_order_id, customer_id = FinanceService._resolve_target(db, payload, tx_type=tx_type)
        tx = FinanceTransaction(
            transaction_no=make_no("FN"), transaction_type=tx_type,
            paid_at=payload.paid_at or utcnow(),
            **payload.model_dump(exclude={"transaction_type", "paid_at", "repair_order_id", "customer_id"}),
            repair_order_id=repair_order_id,
            customer_id=customer_id,
        )
        db.add(tx)
        db.flush()
        if repair_order_id:
            RepairOrderService.recalculate_finance(db, db.get(RepairOrder, repair_order_id))
        return tx

    @staticmethod
    def update(db: Session, tx: FinanceTransaction, payload: FinanceUpdate) -> FinanceTransaction:
        if tx.deleted_at is not None:
            raise BusinessError("财务流水不存在", code="finance_not_found", status_code=404)
        tx_type = FinanceService._validated_type(payload.transaction_type)
        repair_order_id, customer_id = FinanceService._resolve_target(
            db, payload, tx_type=tx_type, current_tx_id=tx.id
        )
        new_order = db.get(RepairOrder, repair_order_id) if repair_order_id else None

        old_order_id = tx.repair_order_id
        values = payload.model_dump(exclude={"transaction_type", "paid_at", "attachment_id", "repair_order_id", "customer_id"})
        for key, value in values.items():
            setattr(tx, key, value)
        tx.repair_order_id = repair_order_id
        tx.customer_id = customer_id
        tx.transaction_type = tx_type
        tx.paid_at = payload.paid_at or tx.paid_at
        db.flush()

        affected_order_ids = {order_id for order_id in (old_order_id, tx.repair_order_id) if order_id}
        for order_id in affected_order_ids:
            order = new_order if new_order and new_order.id == order_id else db.get(RepairOrder, order_id)
            if order and order.deleted_at is None:
                RepairOrderService.recalculate_finance(db, order)
        return tx
