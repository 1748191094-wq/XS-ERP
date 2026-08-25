from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.database import SessionLocal, create_schema
from app.models.entities import Customer, DroneDevice, InventoryItem, RepairOrder, RepairOrderStatusHistory, User
from app.services.numbering import make_no


def seed() -> None:
    create_schema()
    with SessionLocal() as db:
        engineer = db.scalar(select(User).where(User.username == "demo_engineer"))
        if not engineer:
            engineer = User(username="demo_engineer", display_name="演示工程师", role="engineer", enabled=False)
            db.add(engineer); db.flush()
        customer = db.scalar(select(Customer).where(Customer.phone == "13800000000"))
        if not customer:
            customer = Customer(customer_no=make_no("CU"), name="演示客户", phone="13800000000", email="demo@example.com", city="上海", notes="初始化演示数据")
            db.add(customer); db.flush()
        device = db.scalar(select(DroneDevice).where(DroneDevice.serial_number == "DEMO-DJI-0001"))
        if not device:
            device = DroneDevice(customer_id=customer.id, brand="DJI", model="Mini 4 Pro", serial_number="DEMO-DJI-0001", warranty_status="unknown")
            db.add(device); db.flush()
        if not db.scalar(select(RepairOrder).where(RepairOrder.order_no == "DEMO-RO-0001")):
            order = RepairOrder(order_no="DEMO-RO-0001", customer_id=customer.id, device_id=device.id, engineer_id=engineer.id, fault_description="云台抖动，飞行后偶发水平偏移", intake_condition="外观轻微使用痕迹", intake_accessories="机身、电池 1 块", status="pending_inspection")
            db.add(order); db.flush(); db.add(RepairOrderStatusHistory(repair_order_id=order.id, from_status=None, to_status=order.status, reason="演示数据"))
        items = [
            ("DJI-M4P-GIMBAL-CABLE", "Mini 4 Pro 云台排线", "云台", Decimal("80"), Decimal("160"), Decimal("5"), Decimal("2")),
            ("GEN-USB-C", "USB-C 数据线", "通用", Decimal("12"), Decimal("29"), Decimal("20"), Decimal("5")),
        ]
        for sku, name, category, buy, sell, stock, safety in items:
            if not db.scalar(select(InventoryItem).where(InventoryItem.sku == sku)):
                db.add(InventoryItem(sku=sku, name=name, category=category, purchase_price=buy, sale_price=sell, stock_quantity=stock, safety_stock=safety, unit="件", enabled=True))
        db.commit()


if __name__ == "__main__":
    seed()
    print("演示数据初始化完成")
