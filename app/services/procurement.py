from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import BusinessError
from app.core.inventory_quantity import inventory_quantity, inventory_quantity_int
from app.models.entities import (
    FinanceTransaction,
    InventoryItem,
    InventoryLot,
    InventoryTransaction,
    PurchaseOrder,
    PurchaseOrderItem,
    Stocktake,
    StocktakeItem,
    Supplier,
)
from app.services.numbering import make_no


MONEY = Decimal("0.01")
def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _quantity(value) -> Decimal:
    try:
        return inventory_quantity(value)
    except ValueError as exc:
        raise BusinessError("库存数量必须为整数", code="inventory_quantity_must_be_integer") from exc


def serialize_supplier(supplier: Supplier) -> dict:
    return {
        "id": supplier.id,
        "name": supplier.name,
        "contact": supplier.contact,
        "phone": supplier.phone,
        "email": supplier.email,
        "address": supplier.address,
        "notes": supplier.notes,
        "enabled": supplier.enabled,
        "created_at": supplier.created_at,
        "updated_at": supplier.updated_at,
    }


def load_purchase_order(db: Session, purchase_order_id: int) -> PurchaseOrder:
    order = db.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == purchase_order_id)
        .options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.supplier))
    )
    if not order:
        raise BusinessError("采购单不存在", code="purchase_order_not_found", status_code=404)
    return order


def serialize_purchase_order(db: Session, order: PurchaseOrder) -> dict:
    paid = db.scalar(
        select(func.coalesce(func.sum(FinanceTransaction.amount), 0)).where(
            FinanceTransaction.purchase_order_id == order.id,
            FinanceTransaction.transaction_type == "expense",
            FinanceTransaction.deleted_at.is_(None),
        )
    ) or Decimal("0")
    return {
        "id": order.id,
        "purchase_no": order.purchase_no,
        "supplier_id": order.supplier_id,
        "supplier_name": order.supplier.name if order.supplier else "",
        "status": order.status,
        "ordered_at": order.ordered_at,
        "expected_at": order.expected_at,
        "completed_at": order.completed_at,
        "total_amount": order.total_amount,
        "paid_amount": paid,
        "payable_amount": max(Decimal("0"), Decimal(order.total_amount) - Decimal(paid)),
        "notes": order.notes,
        "created_by": order.created_by,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "items": [
            {
                "id": item.id,
                "inventory_item_id": item.inventory_item_id,
                "sku": item.sku_snapshot,
                "name": item.item_name_snapshot,
                "quantity": inventory_quantity_int(item.quantity),
                "received_quantity": inventory_quantity_int(item.received_quantity),
                "returned_quantity": inventory_quantity_int(item.returned_quantity),
                "net_received_quantity": inventory_quantity_int(
                    Decimal(item.received_quantity) - Decimal(item.returned_quantity)
                ),
                "unit_cost": item.unit_cost,
                "amount": item.amount,
                "remarks": item.remarks,
            }
            for item in order.items
        ],
    }


def create_purchase_order(
    db: Session,
    *,
    supplier_id: int,
    items: list[dict],
    expected_at: datetime | None,
    notes: str | None,
    created_by: int,
) -> PurchaseOrder:
    supplier = db.get(Supplier, supplier_id)
    if not supplier or not supplier.enabled:
        raise BusinessError("供应商不存在或已停用", code="supplier_not_found", status_code=404)
    if not items:
        raise BusinessError("采购单至少需要一个物料", code="purchase_items_required")
    order = PurchaseOrder(
        purchase_no=make_no("PO"),
        supplier_id=supplier.id,
        status="ordered",
        ordered_at=datetime.now(timezone.utc),
        expected_at=expected_at,
        notes=(notes or "").strip() or None,
        created_by=created_by,
    )
    db.add(order)
    db.flush()
    total = Decimal("0")
    seen: set[int] = set()
    for row in items:
        inventory_item_id = int(row["inventory_item_id"])
        if inventory_item_id in seen:
            raise BusinessError("同一物料不能在采购单中重复", code="duplicate_purchase_item")
        seen.add(inventory_item_id)
        inventory = db.get(InventoryItem, inventory_item_id)
        if not inventory or not inventory.enabled:
            raise BusinessError("采购物料不存在或已停用", code="inventory_not_found", status_code=404)
        quantity = _quantity(row["quantity"])
        unit_cost = _money(row["unit_cost"])
        if quantity <= 0 or unit_cost < 0:
            raise BusinessError("采购数量必须大于 0，单价不能小于 0", code="invalid_purchase_item")
        amount = _money(quantity * unit_cost)
        total += amount
        db.add(PurchaseOrderItem(
            purchase_order_id=order.id,
            inventory_item_id=inventory.id,
            sku_snapshot=inventory.sku,
            item_name_snapshot=inventory.name,
            quantity=quantity,
            unit_cost=unit_cost,
            amount=amount,
            remarks=(row.get("remarks") or "").strip() or None,
        ))
    order.total_amount = _money(total)
    db.flush()
    return load_purchase_order(db, order.id)


def receive_purchase_order(db: Session, order: PurchaseOrder, lines: list[dict], *, user_id: int) -> list[InventoryTransaction]:
    if order.status in {"cancelled", "closed"}:
        raise BusinessError("当前采购单不能继续入库", code="purchase_receive_not_allowed", status_code=409)
    if not lines:
        raise BusinessError("请填写本次到货数量", code="receipt_lines_required")
    order_items = {item.id: item for item in order.items}
    transactions: list[InventoryTransaction] = []
    for row in lines:
        purchase_item = order_items.get(int(row["purchase_order_item_id"]))
        if not purchase_item:
            raise BusinessError("采购明细不属于当前采购单", code="purchase_item_mismatch")
        quantity = _quantity(row["quantity"])
        net_received = Decimal(purchase_item.received_quantity) - Decimal(purchase_item.returned_quantity)
        outstanding = Decimal(purchase_item.quantity) - net_received
        if quantity <= 0 or quantity > outstanding:
            raise BusinessError(f"{purchase_item.item_name_snapshot} 本次最多可入库 {outstanding}", code="receipt_quantity_invalid")
        inventory = db.get(InventoryItem, purchase_item.inventory_item_id)
        before = Decimal(inventory.stock_quantity)
        old_value = before * Decimal(inventory.purchase_price)
        after = before + quantity
        inventory.stock_quantity = after
        if after > 0:
            inventory.purchase_price = _money((old_value + quantity * Decimal(purchase_item.unit_cost)) / after)
        purchase_item.received_quantity = Decimal(purchase_item.received_quantity) + quantity
        serials = [str(value).strip() for value in row.get("serial_numbers", []) if str(value).strip()]
        lot = InventoryLot(
            lot_no=(row.get("lot_no") or "").strip() or make_no("LOT"),
            inventory_item_id=inventory.id,
            purchase_order_item_id=purchase_item.id,
            quantity_received=quantity,
            quantity_remaining=quantity,
            unit_cost=purchase_item.unit_cost,
            serial_numbers_json=serials or None,
            created_by=user_id,
        )
        db.add(lot)
        db.flush()
        transaction = InventoryTransaction(
            transaction_no=make_no("ST"),
            inventory_item_id=inventory.id,
            transaction_type="purchase_in",
            quantity=quantity,
            before_quantity=before,
            after_quantity=after,
            unit_cost=purchase_item.unit_cost,
            purchase_order_id=order.id,
            purchase_order_item_id=purchase_item.id,
            inventory_lot_id=lot.id,
            operator_id=user_id,
            remarks=f"采购入库 {order.purchase_no}",
        )
        db.add(transaction)
        transactions.append(transaction)
    db.flush()
    complete = all(
        Decimal(item.received_quantity) - Decimal(item.returned_quantity) >= Decimal(item.quantity)
        for item in order.items
    )
    order.status = "received" if complete else "partially_received"
    order.completed_at = datetime.now(timezone.utc) if complete else None
    return transactions


def return_purchase_item(db: Session, order: PurchaseOrder, purchase_order_item_id: int, quantity, *, user_id: int, remarks: str | None) -> InventoryTransaction:
    purchase_item = next((item for item in order.items if item.id == purchase_order_item_id), None)
    if not purchase_item:
        raise BusinessError("采购明细不属于当前采购单", code="purchase_item_mismatch")
    quantity = _quantity(quantity)
    returnable = Decimal(purchase_item.received_quantity) - Decimal(purchase_item.returned_quantity)
    inventory = db.get(InventoryItem, purchase_item.inventory_item_id)
    before = Decimal(inventory.stock_quantity)
    if quantity <= 0 or quantity > returnable:
        raise BusinessError(f"最多可退 {returnable}", code="purchase_return_quantity_invalid")
    if quantity > before:
        raise BusinessError("当前库存不足，不能完成采购退货", code="insufficient_stock", status_code=409)
    after = before - quantity
    inventory.stock_quantity = after
    purchase_item.returned_quantity = Decimal(purchase_item.returned_quantity) + quantity
    remaining = quantity
    lots = list(db.scalars(
        select(InventoryLot).where(
            InventoryLot.purchase_order_item_id == purchase_item.id,
            InventoryLot.quantity_remaining > 0,
        ).order_by(InventoryLot.received_at.desc())
    ))
    chosen_lot_id = None
    for lot in lots:
        if chosen_lot_id is None:
            chosen_lot_id = lot.id
        take = min(remaining, Decimal(lot.quantity_remaining))
        lot.quantity_remaining = Decimal(lot.quantity_remaining) - take
        remaining -= take
        if remaining <= 0:
            break
    transaction = InventoryTransaction(
        transaction_no=make_no("ST"),
        inventory_item_id=inventory.id,
        transaction_type="purchase_return",
        quantity=quantity,
        before_quantity=before,
        after_quantity=after,
        unit_cost=purchase_item.unit_cost,
        purchase_order_id=order.id,
        purchase_order_item_id=purchase_item.id,
        inventory_lot_id=chosen_lot_id,
        operator_id=user_id,
        remarks=(remarks or "").strip() or f"采购退货 {order.purchase_no}",
    )
    db.add(transaction)
    order.status = "partially_received"
    order.completed_at = None
    return transaction


def create_stocktake(db: Session, lines: list[dict], *, user_id: int, notes: str | None) -> Stocktake:
    if not lines:
        raise BusinessError("盘点至少需要一个物料", code="stocktake_items_required")
    stocktake = Stocktake(stocktake_no=make_no("SC"), status="draft", notes=(notes or "").strip() or None, created_by=user_id)
    db.add(stocktake)
    db.flush()
    seen: set[int] = set()
    for row in lines:
        inventory_id = int(row["inventory_item_id"])
        if inventory_id in seen:
            raise BusinessError("同一物料不能重复盘点", code="duplicate_stocktake_item")
        seen.add(inventory_id)
        inventory = db.get(InventoryItem, inventory_id)
        if not inventory:
            raise BusinessError("盘点物料不存在", code="inventory_not_found", status_code=404)
        counted = _quantity(row["counted_quantity"])
        if counted < 0:
            raise BusinessError("盘点数量不能小于 0", code="invalid_stocktake_quantity")
        system = Decimal(inventory.stock_quantity)
        db.add(StocktakeItem(
            stocktake_id=stocktake.id,
            inventory_item_id=inventory.id,
            system_quantity=system,
            counted_quantity=counted,
            difference_quantity=counted - system,
            unit_cost=inventory.purchase_price,
            remarks=(row.get("remarks") or "").strip() or None,
        ))
    db.flush()
    return db.scalar(select(Stocktake).where(Stocktake.id == stocktake.id).options(selectinload(Stocktake.items)))


def commit_stocktake(db: Session, stocktake: Stocktake, *, user_id: int) -> list[InventoryTransaction]:
    if stocktake.status != "draft":
        raise BusinessError("盘点单已经提交，不能重复调整库存", code="stocktake_already_committed", status_code=409)
    transactions: list[InventoryTransaction] = []
    for line in stocktake.items:
        inventory = db.get(InventoryItem, line.inventory_item_id)
        before = Decimal(inventory.stock_quantity)
        if before != Decimal(line.system_quantity):
            raise BusinessError(
                f"{inventory.name} 在盘点期间发生了库存变化，请重新建立盘点单",
                code="stocktake_inventory_changed",
                status_code=409,
            )
        after = Decimal(line.counted_quantity)
        difference = after - before
        line.difference_quantity = difference
        if difference == 0:
            continue
        inventory.stock_quantity = after
        transaction = InventoryTransaction(
            transaction_no=make_no("ST"),
            inventory_item_id=inventory.id,
            transaction_type="stocktake_adjustment",
            quantity=difference,
            before_quantity=before,
            after_quantity=after,
            unit_cost=line.unit_cost,
            stocktake_id=stocktake.id,
            operator_id=user_id,
            remarks=f"盘点调整 {stocktake.stocktake_no}",
        )
        db.add(transaction)
        transactions.append(transaction)
    stocktake.status = "committed"
    stocktake.committed_at = datetime.now(timezone.utc)
    return transactions
