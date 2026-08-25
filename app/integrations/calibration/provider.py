from __future__ import annotations

from typing import Protocol


class CalibrationProvider(Protocol):
    def record(self, *, tool_name: str, before: dict, after: dict, operator_id: int | None) -> dict: ...


class ManualCalibrationProvider:
    def record(self, *, tool_name: str, before: dict, after: dict, operator_id: int | None) -> dict:
        return {"mode": "manual_record", "tool_name": tool_name, "before": before, "after": after, "operator_id": operator_id, "automatic_dji_protocol_used": False}
