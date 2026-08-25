from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import entities  # noqa: F401
from app.models.entities import (
    Customer,
    DroneDevice,
    InventoryItem,
    PurchaseOrder,
    RepairOrder,
    Supplier,
    utcnow,
)
from app.schemas.domain import FinanceCreate, FinanceUpdate, QuoteCreate, QuoteItemInput, StockChange
from app.services.finance import FinanceService
from app.services.inventory import InventoryService
from app.services.quotes import QuoteService


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    path = tmp_path_factory.mktemp("logic-audit") / "logic-audit.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _order_context(db: Session, suffix: str = "A") -> tuple[Customer, DroneDevice, RepairOrder]:
    customer = Customer(customer_no=f"CU-{suffix}", name=f"Customer {suffix}")
    db.add(customer)
    db.flush()
    device = DroneDevice(
        customer_id=customer.id,
        brand="DJI",
        model="Audit",
        serial_number=f"SN-{suffix}",
    )
    db.add(device)
    db.flush()
    order = RepairOrder(
        order_no=f"R-260811-00{suffix}1",
        customer_id=customer.id,
        device_id=device.id,
        fault_description="logic audit",
    )
    db.add(order)
    db.flush()
    return customer, device, order


def _finance_payload(**overrides) -> FinanceCreate:
    values = {
        "transaction_type": "income",
        "category": "audit",
        "amount": Decimal("10"),
    }
    values.update(overrides)
    return FinanceCreate(**values)


def test_finance_canonicalizes_order_customer_and_rejects_mismatch_or_deleted_targets(db: Session):
    customer, _device, order = _order_context(db, "A")
    other = Customer(customer_no="CU-B", name="Other")
    db.add(other)
    db.flush()

    transaction = FinanceService.create(db, _finance_payload(repair_order_id=order.id))
    assert transaction.repair_order_id == order.id
    assert transaction.customer_id == customer.id

    with pytest.raises(BusinessError) as caught:
        FinanceService.create(
            db,
            _finance_payload(repair_order_id=order.id, customer_id=other.id),
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "finance_customer_mismatch"

    order.deleted_at = utcnow()
    db.flush()
    with pytest.raises(BusinessError) as caught:
        FinanceService.create(db, _finance_payload(repair_order_id=order.id))
    assert caught.value.code == "order_not_found"

    order.deleted_at = None
    customer.deleted_at = utcnow()
    db.flush()
    with pytest.raises(BusinessError) as caught:
        FinanceService.create(db, _finance_payload(repair_order_id=order.id))
    assert caught.value.code == "customer_not_found"


def test_purchase_finance_is_exclusive_expense_only_and_cannot_overpay_on_create_or_update(db: Session):
    customer, _device, order = _order_context(db, "P")
    supplier = Supplier(name="Audit Supplier")
    db.add(supplier)
    db.flush()
    purchase = PurchaseOrder(
        purchase_no="PO-AUDIT",
        supplier_id=supplier.id,
        total_amount=Decimal("100.00"),
    )
    db.add(purchase)
    db.flush()

    with pytest.raises(BusinessError) as caught:
        FinanceService.create(
            db,
            _finance_payload(
                transaction_type="expense",
                purchase_order_id=purchase.id,
                repair_order_id=order.id,
            ),
        )
    assert caught.value.code == "finance_target_conflict"

    with pytest.raises(BusinessError) as caught:
        FinanceService.create(db, _finance_payload(purchase_order_id=purchase.id))
    assert caught.value.code == "purchase_payment_type_invalid"

    payment = FinanceService.create(
        db,
        _finance_payload(
            transaction_type="expense",
            purchase_order_id=purchase.id,
            amount=Decimal("60.00"),
        ),
    )
    assert payment.purchase_order_id == purchase.id
    assert payment.repair_order_id is None
    assert payment.customer_id is None

    with pytest.raises(BusinessError) as caught:
        FinanceService.create(
            db,
            _finance_payload(
                transaction_type="expense",
                purchase_order_id=purchase.id,
                amount=Decimal("40.01"),
            ),
        )
    assert caught.value.code == "purchase_overpayment"

    with pytest.raises(BusinessError) as caught:
        FinanceService.update(
            db,
            payment,
            FinanceUpdate(
                purchase_order_id=purchase.id,
                transaction_type="expense",
                category="audit update",
                amount=Decimal("100.01"),
            ),
        )
    assert caught.value.code == "purchase_overpayment"
    assert payment.amount == Decimal("60.00")

    payment.deleted_at = utcnow()
    with pytest.raises(BusinessError) as caught:
        FinanceService.update(
            db,
            payment,
            FinanceUpdate(
                purchase_order_id=purchase.id,
                transaction_type="expense",
                category="deleted",
                amount=Decimal("10.00"),
            ),
        )
    assert caught.value.code == "finance_not_found"


def test_inventory_repair_return_is_bounded_and_uses_issued_weighted_cost(db: Session):
    _customer, _device, order = _order_context(db, "I")
    item = InventoryItem(
        sku="PART-AUDIT",
        name="Audit Part",
        stock_quantity=Decimal("10"),
        purchase_price=Decimal("99"),
        enabled=True,
    )
    db.add(item)
    db.flush()

    with pytest.raises(BusinessError) as caught:
        InventoryService.change_stock(
            db,
            StockChange(
                inventory_item_id=item.id,
                transaction_type="repair_issue",
                quantity=Decimal("1"),
                unit_cost=Decimal("10"),
            ),
        )
    assert caught.value.code == "repair_order_required"

    for cost in (Decimal("10"), Decimal("20")):
        InventoryService.change_stock(
            db,
            StockChange(
                inventory_item_id=item.id,
                transaction_type="repair_issue",
                quantity=Decimal("2"),
                repair_order_id=order.id,
                unit_cost=cost,
            ),
        )

    returned = InventoryService.change_stock(
        db,
        StockChange(
            inventory_item_id=item.id,
            transaction_type="repair_return",
            quantity=Decimal("3"),
            repair_order_id=order.id,
            unit_cost=Decimal("999"),
        ),
    )
    assert returned.unit_cost == Decimal("15.00")
    assert item.stock_quantity == Decimal("9")

    with pytest.raises(BusinessError) as caught:
        InventoryService.change_stock(
            db,
            StockChange(
                inventory_item_id=item.id,
                transaction_type="repair_return",
                quantity=Decimal("2"),
                repair_order_id=order.id,
            ),
        )
    assert caught.value.code == "repair_return_exceeds_issue"
    assert item.stock_quantity == Decimal("9")

    order.deleted_at = utcnow()
    db.flush()
    with pytest.raises(BusinessError) as caught:
        InventoryService.change_stock(
            db,
            StockChange(
                inventory_item_id=item.id,
                transaction_type="repair_issue",
                quantity=Decimal("1"),
                repair_order_id=order.id,
            ),
        )
    assert caught.value.code == "order_not_found"


def test_inventory_rejects_disabled_and_soft_deleted_items(db: Session):
    item = InventoryItem(
        sku="PART-DISABLED",
        name="Disabled Part",
        stock_quantity=Decimal("2"),
        enabled=False,
    )
    db.add(item)
    db.flush()
    payload = StockChange(
        inventory_item_id=item.id,
        transaction_type="stock_out",
        quantity=Decimal("1"),
    )
    with pytest.raises(BusinessError) as caught:
        InventoryService.change_stock(db, payload)
    assert caught.value.code == "inventory_disabled"

    item.enabled = True
    item.deleted_at = utcnow()
    db.flush()
    with pytest.raises(BusinessError) as caught:
        InventoryService.change_stock(db, payload)
    assert caught.value.code == "inventory_not_found"


def test_quote_recalculates_latest_active_total_and_rejects_terminal_or_invalid_items(db: Session):
    _customer, _device, order = _order_context(db, "Q")
    inventory = InventoryItem(
        sku="QUOTE-PART",
        name="Quote Part",
        stock_quantity=Decimal("1"),
        enabled=True,
    )
    db.add(inventory)
    db.flush()

    first = QuoteService.create_version(
        db,
        QuoteCreate(
            repair_order_id=order.id,
            items=[QuoteItemInput(item_name="First", quantity=Decimal("2"), unit_price=Decimal("10"))],
        ),
    )
    assert first.total_amount == Decimal("20.00")
    assert order.total_quote_amount == Decimal("20.00")

    second = QuoteService.create_version(
        db,
        QuoteCreate(
            repair_order_id=order.id,
            items=[QuoteItemInput(item_name="Second", quantity=Decimal("1"), unit_price=Decimal("30"))],
        ),
    )
    assert first.status == "superseded"
    assert order.total_quote_amount == Decimal("30.00")

    second.deleted_at = utcnow()
    QuoteService.recalculate_order_total(db, order)
    assert order.total_quote_amount == Decimal("20.00")
    with pytest.raises(BusinessError) as caught:
        QuoteService.confirm(db, second)
    assert caught.value.code == "quote_not_found"

    order.status = "completed"
    with pytest.raises(BusinessError) as caught:
        QuoteService.create_version(
            db,
            QuoteCreate(repair_order_id=order.id, items=[]),
        )
    assert caught.value.code == "terminal_order_quote_forbidden"

    order.status = "pending_inspection"
    negative_price = QuoteCreate(
        repair_order_id=order.id,
        items=[QuoteItemInput(item_name="Bad price", quantity=Decimal("1"), unit_price=Decimal("1"))],
    )
    negative_price.items[0].unit_price = Decimal("-0.01")
    with pytest.raises(BusinessError) as caught:
        QuoteService.create_version(db, negative_price)
    assert caught.value.code == "invalid_quote_unit_price"

    invalid_quantity = QuoteCreate(
        repair_order_id=order.id,
        items=[QuoteItemInput(item_name="Bad quantity", quantity=Decimal("1"), unit_price=Decimal("1"))],
    )
    invalid_quantity.items[0].quantity = Decimal("0")
    with pytest.raises(BusinessError) as caught:
        QuoteService.create_version(db, invalid_quantity)
    assert caught.value.code == "invalid_quote_quantity"

    inventory.deleted_at = utcnow()
    with pytest.raises(BusinessError) as caught:
        QuoteService.create_version(
            db,
            QuoteCreate(
                repair_order_id=order.id,
                items=[QuoteItemInput(
                    inventory_item_id=inventory.id,
                    item_name="Deleted inventory",
                    quantity=Decimal("1"),
                    unit_price=Decimal("1"),
                )],
            ),
        )
    assert caught.value.code == "inventory_not_found"
