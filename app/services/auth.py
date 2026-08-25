from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import new_session_token, token_digest
from app.models.entities import AuditLog, User, UserSession


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:80]
    return request.client.host[:80] if request.client else None


def create_session(db: Session, user: User, request: Request) -> tuple[str, UserSession]:
    raw_token = new_session_token()
    now = datetime.now(timezone.utc)
    session = UserSession(
        user_id=user.id,
        token_hash=token_digest(raw_token),
        csrf_token=secrets.token_urlsafe(36),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_hours),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:300] or None,
    )
    db.add(session)
    db.flush()
    return raw_token, session


def add_audit_log(
    db: Session,
    request: Request,
    *,
    action: str,
    success: bool,
    status_code: int,
    user: User | None = None,
    username: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    path = request.url.path
    parts = [part for part in path.split("/") if part]
    resource_type = parts[1] if len(parts) > 1 else None
    resource_id = parts[2] if len(parts) > 2 and parts[2].isdigit() else None
    row = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else username,
        action=action[:120],
        resource_type=resource_type,
        resource_id=resource_id,
        method=request.method,
        path=path[:500],
        status_code=status_code,
        success=success,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:300] or None,
        details_json=details,
    )
    db.add(row)
    return row
