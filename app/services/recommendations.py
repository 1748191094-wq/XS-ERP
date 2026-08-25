from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models.entities import Diagnosis, InventoryItem, RepairOrder
from app.schemas.domain import QuoteCreate, QuoteItemInput


CATEGORY_MAP = {
    "battery": ("电池", "battery"),
    "motor": ("电机", "motor"),
    "esc": ("电调", "esc"),
    "imu": ("IMU", "传感器"),
    "gps": ("GPS", "定位"),
    "compass": ("指南针", "传感器"),
    "flight_event": ("检测",),
}
LABOR_BY_SEVERITY = {"low": Decimal("80"), "medium": Decimal("120"), "high": Decimal("260"), "critical": Decimal("450")}


class QuoteRecommendationService:
    @staticmethod
    def build(db: Session, order_id: int) -> QuoteCreate:
        order = db.get(RepairOrder, order_id)
        if not order:
            raise BusinessError("工单不存在", code="order_not_found", status_code=404)
        diagnoses = list(db.scalars(select(Diagnosis).where(Diagnosis.repair_order_id == order_id)))
        categories = {alias.lower() for diagnosis in diagnoses for alias in CATEGORY_MAP.get(diagnosis.diagnosis_type, ())}
        model_name = order.device.model.lower()
        candidates = list(db.scalars(select(InventoryItem).where(
            InventoryItem.enabled.is_(True),
            InventoryItem.deleted_at.is_(None),
            InventoryItem.stock_quantity > 0,
        )))
        items: list[QuoteItemInput] = []
        for candidate in candidates:
            compatible = (candidate.compatible_models or "").lower()
            category = (candidate.category or "").lower()
            name = candidate.name.lower()
            model_match = not compatible or model_name in compatible or compatible in model_name
            category_match = bool(categories and any(alias in category or alias in name for alias in categories))
            if model_match and category_match:
                items.append(QuoteItemInput(
                    inventory_item_id=candidate.id, item_name=candidate.name, quantity=Decimal("1"),
                    unit_price=candidate.sale_price, cost_price=candidate.purchase_price, item_type="part",
                    remarks="根据已记录诊断和兼容型号自动推荐，须人工复核", sort_order=len(items),
                ))
        labor = max((LABOR_BY_SEVERITY.get(d.severity, Decimal("80")) for d in diagnoses), default=Decimal("80"))
        return QuoteCreate(repair_order_id=order_id, labor_fee=labor, items=items)
