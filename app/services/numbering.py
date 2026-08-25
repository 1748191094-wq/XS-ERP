from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.models.entities import RepairOrder, RepairOrderNumberReservation, utcnow


REPAIR_ORDER_SEQUENCE_WIDTH = 4
REPAIR_ORDER_PATTERN = re.compile(r"^RO-(\d{10})-([0-9A-Z]{4})$")
BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHINA_TIMEZONE = timezone(timedelta(hours=8))
REPAIR_ORDER_ALLOCATION_ATTEMPTS = 128


def make_no(prefix: str) -> str:
    now = datetime.now(timezone.utc).astimezone()
    return f"{prefix}-{now:%Y%m%d%H%M%S}-{secrets.token_hex(2).upper()}"


@lru_cache(maxsize=None)
def _mixed_completion_count(positions: int, has_digit: bool, has_letter: bool) -> int:
    if positions == 0:
        return int(has_digit and has_letter)
    return (
        10 * _mixed_completion_count(positions - 1, True, has_letter)
        + 26 * _mixed_completion_count(positions - 1, has_digit, True)
    )


REPAIR_ORDER_SEQUENCE_LIMIT = _mixed_completion_count(REPAIR_ORDER_SEQUENCE_WIDTH, False, False)


def mixed_base36(value: int, *, width: int = REPAIR_ORDER_SEQUENCE_WIDTH) -> str:
    """Map a 1-based sequence to a Base36 token containing both letters and digits."""

    limit = _mixed_completion_count(width, False, False)
    if not 1 <= value <= limit:
        raise ValueError("Mixed Base36 sequence is outside the supported range")
    remaining_rank = value - 1
    token: list[str] = []
    has_digit = has_letter = False
    for position in range(width):
        remaining_positions = width - position - 1
        for character in BASE36_ALPHABET:
            next_has_digit = has_digit or character.isdigit()
            next_has_letter = has_letter or character.isalpha()
            block_size = _mixed_completion_count(
                remaining_positions, next_has_digit, next_has_letter
            )
            if remaining_rank < block_size:
                token.append(character)
                has_digit, has_letter = next_has_digit, next_has_letter
                break
            remaining_rank -= block_size
    return "".join(token)


def mixed_base36_rank(token: str) -> int:
    """Return the 1-based rank for a valid mixed Base36 token."""

    normalized = token.strip().upper()
    if (
        len(normalized) != REPAIR_ORDER_SEQUENCE_WIDTH
        or any(character not in BASE36_ALPHABET for character in normalized)
        or not any(character.isdigit() for character in normalized)
        or not any(character.isalpha() for character in normalized)
    ):
        raise ValueError("Repair-order suffix must contain both letters and digits")
    rank = 0
    has_digit = has_letter = False
    for position, actual in enumerate(normalized):
        remaining_positions = REPAIR_ORDER_SEQUENCE_WIDTH - position - 1
        for character in BASE36_ALPHABET:
            if character == actual:
                break
            rank += _mixed_completion_count(
                remaining_positions,
                has_digit or character.isdigit(),
                has_letter or character.isalpha(),
            )
        has_digit = has_digit or actual.isdigit()
        has_letter = has_letter or actual.isalpha()
    return rank + 1


def _random_repair_order_suffix() -> str:
    """Choose uniformly from all four-character tokens containing letters and digits."""

    return mixed_base36(secrets.randbelow(REPAIR_ORDER_SEQUENCE_LIMIT) + 1)


def is_short_repair_order_no(value: str) -> bool:
    match = REPAIR_ORDER_PATTERN.fullmatch(value)
    if not match:
        return False
    suffix = match.group(2)
    if not any(character.isdigit() for character in suffix) or not any(
        character.isalpha() for character in suffix
    ):
        return False
    try:
        datetime.strptime(match.group(1), "%y%m%d%H%M")
    except ValueError:
        return False
    return True


def allocate_local_repair_order_no(db: Session, *, now: datetime | None = None) -> str:
    """Randomly allocate and atomically reserve a repair-order number."""

    source_now = now or datetime.now(timezone.utc)
    if source_now.tzinfo is None:
        source_now = source_now.replace(tzinfo=CHINA_TIMEZONE)
    minute_key = source_now.astimezone(CHINA_TIMEZONE).strftime("%y%m%d%H%M")

    for _attempt in range(REPAIR_ORDER_ALLOCATION_ATTEMPTS):
        order_no = f"RO-{minute_key}-{_random_repair_order_suffix()}"
        reservation_id = db.execute(
            sqlite_insert(RepairOrderNumberReservation)
            .values(order_no=order_no, reserved_at=utcnow())
            .on_conflict_do_nothing(index_elements=[RepairOrderNumberReservation.order_no])
            .returning(RepairOrderNumberReservation.id)
        ).scalar_one_or_none()
        if reservation_id is None:
            continue
        # Existing rows are backfilled by migration; this also protects later imports.
        if db.scalar(select(RepairOrder.id).where(RepairOrder.order_no == order_no)) is None:
            return order_no

    raise BusinessError(
        "维修工单号随机分配发生过多碰撞，请稍后重试",
        code="repair_order_number_exhausted",
        status_code=409,
    )


def _allocate_from_sync_host() -> str:
    if not settings.sync_host_url or len(settings.sync_shared_secret) < 24:
        raise BusinessError(
            "终端未配置可用的同步主机，无法安全分配维修工单号",
            code="repair_order_number_host_not_configured",
            status_code=503,
        )
    request = Request(
        f"{settings.sync_host_url}/api/sync/numbering/repair-orders/next",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-Sync-Secret": settings.sync_shared_secret},
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise BusinessError(
            "无法连接编号主机；终端离线时不能新建维修工单，请恢复连接后重试",
            code="repair_order_number_host_unavailable",
            status_code=503,
        ) from exc
    order_no = payload.get("data", {}).get("order_no") if payload.get("success") else None
    if not isinstance(order_no, str) or not is_short_repair_order_no(order_no):
        raise BusinessError(
            "编号主机返回了无效维修工单号",
            code="repair_order_number_host_invalid",
            status_code=503,
        )
    return order_no


def allocate_repair_order_no(db: Session, *, now: datetime | None = None) -> str:
    if settings.sync_role == "terminal":
        return _allocate_from_sync_host()
    return allocate_local_repair_order_no(db, now=now)
