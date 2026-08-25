from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import RepairOrderStatus
from app.core.exceptions import BusinessError
from app.models.entities import (
    FinanceTransaction,
    FollowUpTask,
    InventoryTransaction,
    RepairOrder,
    RepairOrderStatusHistory,
    utcnow,
)


class RepairOrderService:
    @staticmethod
    def change_status(db: Session, order: RepairOrder, new_status: str, *, changed_by: int | None = None, reason: str | None = None) -> RepairOrder:
        try:
            target = RepairOrderStatus(new_status).value
        except ValueError as exc:
            raise BusinessError("未知的工单状态", code="invalid_order_status") from exc
        previous = order.status
        if previous == target:
            return order
        if previous in {RepairOrderStatus.COMPLETED, RepairOrderStatus.CANCELLED}:
            raise BusinessError(
                "已完成或已取消的维修工单不能直接重新流转",
                code="order_terminal_state",
                status_code=409,
            )
        order.status = target
        if target == RepairOrderStatus.COMPLETED:
            order.completed_at = utcnow()
            existing = db.scalar(select(FollowUpTask).where(
                FollowUpTask.repair_order_id == order.id,
                FollowUpTask.deleted_at.is_(None),
            ))
            if not existing:
                db.add(
                    FollowUpTask(
                        repair_order_id=order.id,
                        customer_id=order.customer_id,
                        scheduled_at=utcnow() + timedelta(days=3),
                        content="维修完成后三日回访：确认设备状态与客户满意度。",
                    )
                )
        else:
            order.completed_at = None
        db.add(RepairOrderStatusHistory(repair_order_id=order.id, from_status=previous, to_status=target, changed_by=changed_by, reason=reason))
        from app.services.tickets import TicketService
        TicketService.sync_repair_order_status(db, order, actor_id=changed_by, reason=reason)
        db.flush()
        return order

    @staticmethod
    def recalculate_finance(db: Session, order: RepairOrder) -> RepairOrder:
        income = db.scalar(
            select(func.coalesce(func.sum(FinanceTransaction.amount), 0)).where(
                FinanceTransaction.repair_order_id == order.id,
                FinanceTransaction.transaction_type == "income",
                FinanceTransaction.deleted_at.is_(None),
            )
        ) or Decimal("0")
        refunds = db.scalar(
            select(func.coalesce(func.sum(FinanceTransaction.amount), 0)).where(
                FinanceTransaction.repair_order_id == order.id,
                FinanceTransaction.transaction_type == "refund",
                FinanceTransaction.deleted_at.is_(None),
            )
        ) or Decimal("0")
        expenses = db.scalar(
            select(func.coalesce(func.sum(FinanceTransaction.amount), 0)).where(
                FinanceTransaction.repair_order_id == order.id,
                FinanceTransaction.transaction_type == "expense",
                FinanceTransaction.deleted_at.is_(None),
            )
        ) or Decimal("0")
        material_cost = db.scalar(
            select(func.coalesce(func.sum(InventoryTransaction.quantity * InventoryTransaction.unit_cost), 0)).where(
                InventoryTransaction.repair_order_id == order.id,
                InventoryTransaction.transaction_type == "repair_issue",
            )
        ) or Decimal("0")
        returned_cost = db.scalar(
            select(func.coalesce(func.sum(InventoryTransaction.quantity * InventoryTransaction.unit_cost), 0)).where(
                InventoryTransaction.repair_order_id == order.id,
                InventoryTransaction.transaction_type == "repair_return",
            )
        ) or Decimal("0")
        order.total_received = Decimal(income) - Decimal(refunds)
        order.total_cost = Decimal(expenses) + Decimal(material_cost) - Decimal(returned_cost)
        order.gross_profit = order.total_received - order.total_cost
        db.flush()
        return order
