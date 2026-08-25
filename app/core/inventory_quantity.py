from __future__ import annotations

from decimal import Decimal, InvalidOperation


def inventory_quantity(value: object) -> Decimal:
    """Return a whole inventory quantity or reject fractional/invalid input."""

    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("库存数量必须为整数") from exc
    if not quantity.is_finite() or quantity != quantity.to_integral_value():
        raise ValueError("库存数量必须为整数")
    return quantity.quantize(Decimal("1"))


def inventory_quantity_int(value: object) -> int:
    return int(inventory_quantity(value))


def inventory_quantity_text(value: object) -> str:
    return str(inventory_quantity_int(value))
