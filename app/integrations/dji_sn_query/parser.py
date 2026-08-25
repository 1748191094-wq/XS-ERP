from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import SNQueryResult


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "product_name": (
        "productName", "productTypeName", "deviceName", "modelName",
        "product_name", "product_type_name",
    ),
    "product_model": (
        "productModel", "productCode", "productNumber", "deviceModel",
        "model", "product_model", "product_code",
    ),
    "activation_date": (
        "activationDate", "activationTime", "activeDate", "activeTime",
        "activation_date", "activation_time",
    ),
    "warranty_status": (
        "warrantyStatus", "warrantyState", "isInWarranty", "inWarranty",
        "warranty_status", "warranty_state",
    ),
    "warranty_end_date": (
        "warrantyEndDate", "warrantyEndTime", "warrantyExpireDate",
        "warrantyExpiration", "warranty_end_date", "warranty_end_time",
    ),
    "repair_count": (
        "repairCount", "repairTimes", "serviceCount", "repair_count",
    ),
    "flyaway_count": (
        "flyawayCount", "flyAwayCount", "flyawayTimes", "flyaway_count",
    ),
    "care_status": (
        "careStatus", "djiCareStatus", "servicePlanStatus", "careState",
        "care_status", "dji_care_status",
    ),
    "care_replacement_remaining": (
        "remainingReplacementCount", "replaceRemainCount", "remainingTimes",
        "replacementRemaining", "careReplacementRemaining",
    ),
}


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _scalars(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _scalars(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalars(item, (*path, str(index)))
    elif path:
        yield path, path[-1], value


def _format_value(value: Any, *, date_hint: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if date_hint and isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            pass
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def _pick(flattened: list[tuple[tuple[str, ...], str, Any]], aliases: tuple[str, ...], *, date_hint: bool = False) -> str:
    wanted = {_normalized_key(alias) for alias in aliases}
    for _path, key, value in flattened:
        if _normalized_key(key) in wanted:
            rendered = _format_value(value, date_hint=date_hint)
            if rendered:
                return rendered
    return ""


def _message_from_payload(flattened: list[tuple[tuple[str, ...], str, Any]]) -> str:
    for aliases in (("message", "msg", "errorMessage", "errorMsg"), ("description", "reason")):
        value = _pick(flattened, aliases)
        if value:
            return value
    return ""


def normalize_device_response(serial_number: str, payload: Any) -> SNQueryResult:
    """Convert a DJI response with a changing nested schema into stable fields.

    Unknown fields remain available in ``raw_response``.  This function does
    not treat HTTP success as a successful lookup unless at least one device
    field can be identified.
    """

    flattened = list(_scalars(payload))
    values: dict[str, str] = {}
    for field_name, aliases in FIELD_ALIASES.items():
        values[field_name] = _pick(
            flattened,
            aliases,
            date_hint=field_name in {"activation_date", "warranty_end_date"},
        )

    meaningful = any(values.values())
    message = _message_from_payload(flattened)
    return SNQueryResult(
        serial_number=serial_number.strip().upper(),
        **values,
        status="查询成功" if meaningful else "未识别结果",
        message=message or ("已收到响应，但没有识别出设备字段" if not meaningful else ""),
        raw_response=payload if isinstance(payload, (dict, list)) else {"value": payload},
    )


def _line_after(lines: list[str], label: str, *, start: int = 0) -> str:
    for index in range(start, len(lines) - 1):
        if lines[index] == label:
            return lines[index + 1]
    return ""


def parse_device_page_text(serial_number: str, page_text: str) -> SNQueryResult:
    """Parse the stable labels rendered on DJI's device detail page."""

    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    serial_number = serial_number.strip().upper()
    serial_index = -1
    for index, line in enumerate(lines):
        if line.startswith("序列号：") and serial_number in line.upper():
            serial_index = index
            break
    if serial_index < 1:
        return SNQueryResult(
            serial_number=serial_number,
            status="未识别结果",
            message="详情页中没有找到对应序列号",
        )

    product_name = lines[serial_index - 1]
    activation_date = ""
    for line in lines[serial_index + 1:]:
        if line.startswith("激活时间："):
            activation_date = line.split("：", 1)[1].strip()
            break

    warranty_status = ""
    warranty_end_date = ""
    warranty_start = lines.index("保修期", serial_index) if "保修期" in lines[serial_index:] else -1
    if warranty_start >= 0:
        warranty_status = _line_after(lines, "状态", start=warranty_start)
        warranty_end_date = _line_after(lines, "预计截止日期", start=warranty_start)

    care_status = ""
    care_replacement_remaining = ""
    care_start = -1
    for index in range(serial_index, len(lines)):
        if lines[index].startswith("DJI Care"):
            care_start = index
            break
    if care_start >= 0:
        care_state = _line_after(lines, "状态", start=care_start)
        care_period = _line_after(lines, "服务有效期", start=care_start)
        care_status = care_state
        if care_period:
            care_status = f"{care_state}（{care_period}）" if care_state else care_period
        care_replacement_remaining = _line_after(lines, "剩余置换次数", start=care_start)

    repair_count = _line_after(lines, "维修或服务记录", start=serial_index)
    flyaway_count = _line_after(lines, "飞丢申报记录", start=serial_index)
    return SNQueryResult(
        serial_number=serial_number,
        product_name=product_name,
        activation_date=activation_date,
        warranty_status=warranty_status,
        warranty_end_date=warranty_end_date,
        repair_count=repair_count,
        flyaway_count=flyaway_count,
        care_status=care_status,
        care_replacement_remaining=care_replacement_remaining,
        status="查询成功",
        message="来自 DJI 设备详情页",
    )
