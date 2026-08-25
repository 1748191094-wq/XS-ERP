from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import entities  # noqa: F401
from app.models.entities import RepairOrderNumberReservation
from app.services import numbering
from app.services.numbering import (
    REPAIR_ORDER_ALLOCATION_ATTEMPTS,
    REPAIR_ORDER_PATTERN,
    allocate_local_repair_order_no,
    allocate_repair_order_no,
    is_short_repair_order_no,
)


CHINA_TIMEZONE = timezone(timedelta(hours=8))
FIXED_MINUTE = datetime(2026, 8, 11, 22, 12, tzinfo=CHINA_TIMEZONE)


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def pragmas(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def _reservation_count(engine) -> int:
    with Session(engine) as db:
        return db.scalar(
            select(func.count()).select_from(RepairOrderNumberReservation)
        ) or 0


def _assert_new_format(order_no: str) -> None:
    assert REPAIR_ORDER_PATTERN.fullmatch(order_no)
    assert len(order_no) == 18
    assert order_no.startswith("RO-")
    assert any(character.isdigit() for character in order_no[-4:])
    assert any(character.isalpha() for character in order_no[-4:])


@pytest.mark.parametrize(
    ("order_no", "expected"),
    (
        ("RO-2608112212-B2CF", True),
        ("R-260811-B2CF", False),
        ("RO-2608112212-1234", False),
        ("RO-2608112212-ABCD", False),
        ("RO-2608112212-b2cf", False),
        ("RO-2602302212-B2CF", False),
    ),
)
def test_repair_order_number_validation(order_no: str, expected: bool):
    assert is_short_repair_order_no(order_no) is expected


def test_random_number_uses_fixed_china_minute_and_persists_reservation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    engine = _engine(tmp_path / "fixed-numbering.db")
    monkeypatch.setattr(numbering, "_random_repair_order_suffix", lambda: "B2CF")

    with Session(engine) as db:
        order_no = allocate_local_repair_order_no(db, now=FIXED_MINUTE)
        db.commit()

    assert order_no == "RO-2608112212-B2CF"
    _assert_new_format(order_no)
    with Session(engine) as db:
        reservation = db.scalar(
            select(RepairOrderNumberReservation).where(
                RepairOrderNumberReservation.order_no == order_no
            )
        )
        assert reservation is not None


def test_reserved_random_suffix_collision_retries_atomically(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    engine = _engine(tmp_path / "collision-numbering.db")
    monkeypatch.setattr(numbering, "_random_repair_order_suffix", lambda: "B2CF")
    with Session(engine) as db:
        first = allocate_local_repair_order_no(db, now=FIXED_MINUTE)
        db.commit()

    candidates = iter(("B2CF", "K7M2"))
    monkeypatch.setattr(
        numbering, "_random_repair_order_suffix", lambda: next(candidates)
    )
    with Session(engine) as db:
        second = allocate_local_repair_order_no(db, now=FIXED_MINUTE)
        db.commit()

    assert first == "RO-2608112212-B2CF"
    assert second == "RO-2608112212-K7M2"
    assert _reservation_count(engine) == 2


def test_random_suffix_collision_retry_exhaustion_returns_409(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    engine = _engine(tmp_path / "exhausted-numbering.db")
    monkeypatch.setattr(numbering, "_random_repair_order_suffix", lambda: "B2CF")
    with Session(engine) as db:
        allocate_local_repair_order_no(db, now=FIXED_MINUTE)
        db.commit()

    attempts = 0

    def repeated_collision() -> str:
        nonlocal attempts
        attempts += 1
        return "B2CF"

    monkeypatch.setattr(numbering, "_random_repair_order_suffix", repeated_collision)
    with Session(engine) as db, pytest.raises(BusinessError) as caught:
        allocate_local_repair_order_no(db, now=FIXED_MINUTE)

    assert caught.value.status_code == 409
    assert attempts == REPAIR_ORDER_ALLOCATION_ATTEMPTS
    assert _reservation_count(engine) == 1


def test_40_concurrent_allocations_are_unique_and_reserved(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    engine = _engine(tmp_path / "concurrent-numbering.db")
    candidates = iter(f"{index:03d}A" for index in range(40))
    candidate_lock = Lock()

    def next_candidate() -> str:
        with candidate_lock:
            return next(candidates)

    monkeypatch.setattr(numbering, "_random_repair_order_suffix", next_candidate)

    def allocate_one(_index: int) -> str:
        with Session(engine) as db:
            value = allocate_local_repair_order_no(db, now=FIXED_MINUTE)
            db.commit()
            return value

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(allocate_one, range(40)))

    assert len(values) == len(set(values)) == 40
    assert all(value.startswith("RO-2608112212-") for value in values)
    assert all(REPAIR_ORDER_PATTERN.fullmatch(value) for value in values)
    assert all(any(character.isdigit() for character in value[-4:]) for value in values)
    assert all(any(character.isalpha() for character in value[-4:]) for value in values)
    assert _reservation_count(engine) == 40


def test_terminal_without_numbering_host_fails_closed(tmp_path):
    engine = _engine(tmp_path / "terminal-numbering.db")
    original_role = settings.sync_role
    original_host = settings.sync_host_url
    original_secret = settings.sync_shared_secret
    try:
        object.__setattr__(settings, "sync_role", "terminal")
        object.__setattr__(settings, "sync_host_url", "")
        object.__setattr__(settings, "sync_shared_secret", "")
        with Session(engine) as db, pytest.raises(BusinessError) as caught:
            allocate_repair_order_no(db, now=FIXED_MINUTE)
        assert caught.value.code == "repair_order_number_host_not_configured"
    finally:
        object.__setattr__(settings, "sync_role", original_role)
        object.__setattr__(settings, "sync_host_url", original_host)
        object.__setattr__(settings, "sync_shared_secret", original_secret)
