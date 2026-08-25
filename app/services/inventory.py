from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import InventoryTransactionType
from app.core.exceptions import BusinessError
from app.core.inventory_quantity import inventory_quantity
from app.models.entities import InventoryItem, InventoryTransaction, RepairOrder
from app.models.client import ProductSKU
from app.schemas.domain import StockChange
from app.services.numbering import make_no
from app.services.orders import RepairOrderService


INCREASE_TYPES = {
    InventoryTransactionType.STOCK_IN,
    InventoryTransactionType.REPAIR_RETURN,
    InventoryTransactionType.PURCHASE_IN,
}
DECREASE_TYPES = {
    InventoryTransactionType.STOCK_OUT,
    InventoryTransactionType.REPAIR_ISSUE,
    InventoryTransactionType.DAMAGE,
    InventoryTransactionType.PURCHASE_RETURN,
}


class InventoryService:
    @staticmethod
    def change_stock(db: Session, payload: StockChange) -> InventoryTransaction:
        item = db.get(InventoryItem, payload.inventory_item_id)
        if not item or item.deleted_at is not None:
            raise BusinessError("库存项目不存在", code="inventory_not_found", status_code=404)
        if not item.enabled:
            raise BusinessError("库存项目已停用", code="inventory_disabled", status_code=409)
        try:
            tx_type = InventoryTransactionType(payload.transaction_type)
        except ValueError as exc:
            raise BusinessError("未知的库存流水类型", code="invalid_inventory_type") from exc
        try:
            before = inventory_quantity(item.stock_quantity)
            quantity = inventory_quantity(payload.quantity)
        except ValueError as exc:
            raise BusinessError("库存数量必须为整数", code="inventory_quantity_must_be_integer") from exc
        if tx_type != InventoryTransactionType.ADJUSTMENT and quantity < 0:
            raise BusinessError("除手工调整外，流水数量必须为正数", code="invalid_stock_quantity")
        order = None
        if tx_type in {InventoryTransactionType.REPAIR_ISSUE, InventoryTransactionType.REPAIR_RETURN} and not payload.repair_order_id:
            raise BusinessError(
                "维修领料或退料必须关联有效工单",
                code="repair_order_required",
                status_code=409,
            )
        if payload.repair_order_id:
            order = db.get(RepairOrder, payload.repair_order_id)
            if not order or order.deleted_at is not None:
                raise BusinessError("工单不存在", code="order_not_found", status_code=404)

        unit_cost = Decimal(payload.unit_cost) if payload.unit_cost is not None else Decimal(item.purchase_price)
        if tx_type == InventoryTransactionType.REPAIR_RETURN:
            issued_quantity, issued_cost = db.execute(
                select(
                    func.coalesce(func.sum(InventoryTransaction.quantity), 0),
                    func.coalesce(func.sum(InventoryTransaction.quantity * InventoryTransaction.unit_cost), 0),
                ).where(
                    InventoryTransaction.repair_order_id == order.id,
                    InventoryTransaction.inventory_item_id == item.id,
                    InventoryTransaction.transaction_type == InventoryTransactionType.REPAIR_ISSUE.value,
                )
            ).one()
            returned_quantity = db.scalar(
                select(func.coalesce(func.sum(InventoryTransaction.quantity), 0)).where(
                    InventoryTransaction.repair_order_id == order.id,
                    InventoryTransaction.inventory_item_id == item.id,
                    InventoryTransaction.transaction_type == InventoryTransactionType.REPAIR_RETURN.value,
                )
            ) or Decimal("0")
            issued_quantity = Decimal(issued_quantity)
            outstanding = issued_quantity - Decimal(returned_quantity)
            if issued_quantity <= 0 or quantity > outstanding:
                raise BusinessError(
                    f"退料数量超过该工单待退数量 {max(outstanding, Decimal('0'))} {item.unit}",
                    code="repair_return_exceeds_issue",
                    status_code=409,
                )
            unit_cost = (Decimal(issued_cost) / issued_quantity).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        if tx_type in INCREASE_TYPES:
            after = before + quantity
        elif tx_type in DECREASE_TYPES:
            after = before - quantity
        elif tx_type in {InventoryTransactionType.ADJUSTMENT, InventoryTransactionType.STOCKTAKE_ADJUSTMENT}:
            after = before + quantity
        else:
            raise BusinessError("未知的库存变动方式", code="invalid_inventory_type")
        if after < 0:
            raise BusinessError(f"库存不足，当前仅有 {before} {item.unit}", code="insufficient_stock", status_code=409)
        linked_sku = db.scalar(
            select(ProductSKU).where(
                ProductSKU.inventory_item_id == item.id,
                ProductSKU.deleted_at.is_(None),
            )
        )
        if linked_sku and after < linked_sku.reserved_quantity:
            raise BusinessError(
                f"商城订单已锁定 {linked_sku.reserved_quantity} 件，库存不能降到该数量以下",
                code="inventory_below_client_reservation",
                status_code=409,
            )
        item.stock_quantity = after
        if linked_sku:
            linked_sku.stock_quantity = int(after)
        tx = InventoryTransaction(
            transaction_no=make_no("ST"), inventory_item_id=item.id, transaction_type=tx_type.value,
            quantity=quantity, before_quantity=before, after_quantity=after,
            unit_cost=unit_cost,
            repair_order_id=payload.repair_order_id, operator_id=payload.operator_id, remarks=payload.remarks,
        )
        db.add(tx)
        db.flush()
        if order:
            RepairOrderService.recalculate_finance(db, order)
        return tx
