from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import new_session_token, token_digest
from app.models.client import ClientAccount, ClientActionLog, ClientSession


IDENTIFIER_CHANGE_ACTION = "client.identifier.change"
IDENTIFIER_CHANGE_LIMIT = 2
CLIENT_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def identifier_change_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or datetime.now(timezone.utc)).astimezone(CLIENT_BUSINESS_TIMEZONE)
    start_local = datetime(current.year, 1, 1, tzinfo=CLIENT_BUSINESS_TIMEZONE)
    reset_local = datetime(current.year + 1, 1, 1, tzinfo=CLIENT_BUSINESS_TIMEZONE)
    return start_local.astimezone(timezone.utc), reset_local.astimezone(timezone.utc)


def identifier_change_status(
    db: Session, account_id: int, *, now: datetime | None = None
) -> dict:
    start_at, reset_at = identifier_change_window(now)
    used = db.scalar(
        select(func.count(ClientActionLog.id)).where(
            ClientActionLog.account_id == account_id,
            ClientActionLog.action == IDENTIFIER_CHANGE_ACTION,
            ClientActionLog.success.is_(True),
            ClientActionLog.created_at >= start_at,
            ClientActionLog.created_at < reset_at,
        )
    ) or 0
    return {
        "limit": IDENTIFIER_CHANGE_LIMIT,
        "used": used,
        "remaining": max(0, IDENTIFIER_CHANGE_LIMIT - used),
        "resets_at": reset_at,
    }


def request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    return forwarded[:80] or (request.client.host[:80] if request.client else None)


def create_client_session(
    db: Session, account: ClientAccount, request: Request
) -> tuple[str, ClientSession]:
    raw_token = new_session_token()
    now = datetime.now(timezone.utc)
    session = ClientSession(
        account_id=account.id,
        token_hash=token_digest(raw_token),
        csrf_token=secrets.token_urlsafe(36),
        expires_at=now + timedelta(days=settings.client_session_days),
        created_at=now,
        last_seen_at=now,
        ip_address=request_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:300] or None,
    )
    db.add(session)
    db.flush()
    return raw_token, session


def add_client_action_log(
    db: Session,
    request: Request,
    *,
    action: str,
    account: ClientAccount | None = None,
    success: bool = True,
    resource_type: str | None = None,
    resource_id: int | str | None = None,
    details: dict | None = None,
) -> ClientActionLog:
    log = ClientActionLog(
        account_id=account.id if account else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        success=success,
        ip_address=request_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:300] or None,
        details_json=details,
    )
    db.add(log)
    return log
