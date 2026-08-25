from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SNQueryResult:
    serial_number: str
    product_name: str = ""
    product_model: str = ""
    activation_date: str = ""
    warranty_status: str = ""
    warranty_end_date: str = ""
    repair_count: str = ""
    flyaway_count: str = ""
    care_status: str = ""
    care_replacement_remaining: str = ""
    status: str = "待查询"
    message: str = ""
    raw_response: dict[str, Any] | list[Any] | None = field(default=None, repr=False)

    def as_row(self) -> dict[str, str]:
        values = asdict(self)
        values.pop("raw_response", None)
        return {key: "" if value is None else str(value) for key, value in values.items()}
