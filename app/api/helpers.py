from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder


def ok(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": jsonable_encoder(data), "error": None}
