from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.core.security import hash_password, validate_emergency_password
from app.models.entities import AuditLog, User, UserSession


def revoke_user_sessions(db: Session, user_id: int) -> int:
    result = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    return int(result.rowcount or 0)


def recent_admin_login_failures(
    db: Session, username: str, *, now: datetime | None = None
) -> tuple[int, datetime | None]:
    now = now or datetime.now(timezone.utc)
    normalized = username.strip().lower()
    cutoff = now - timedelta(minutes=settings.admin_login_lock_minutes)
    latest_success = db.scalar(
        select(func.max(AuditLog.created_at)).where(
            AuditLog.action == "auth.login",
            AuditLog.success.is_(True),
            func.lower(AuditLog.username) == normalized,
        )
    )
    lower_bound = max(cutoff, latest_success) if latest_success else cutoff
    conditions = (
        AuditLog.action == "auth.login",
        AuditLog.success.is_(False),
        func.lower(AuditLog.username) == normalized,
        AuditLog.created_at > lower_bound,
    )
    count = db.scalar(select(func.count(AuditLog.id)).where(*conditions)) or 0
    latest_failure = db.scalar(select(func.max(AuditLog.created_at)).where(*conditions))
    return int(count), latest_failure


def admin_login_locked_until(
    db: Session, username: str, *, now: datetime | None = None
) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    count, latest_failure = recent_admin_login_failures(db, username, now=now)
    if count < settings.admin_login_max_failures or latest_failure is None:
        return None
    locked_until = latest_failure + timedelta(minutes=settings.admin_login_lock_minutes)
    return locked_until if locked_until > now else None


def recover_admin_account(
    db: Session, *, username: str, new_password: str
) -> tuple[User, int]:
    try:
        validated = validate_emergency_password(new_password)
    except ValueError as exc:
        raise BusinessError(str(exc), code="emergency_password_weak", status_code=422) from exc
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if not user or user.role != "admin":
        raise BusinessError("未找到指定的管理端管理员", code="admin_not_found", status_code=404)
    revoked = revoke_user_sessions(db, user.id)
    user.password_hash = hash_password(validated)
    user.enabled = True
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="auth.emergency_recovery",
            resource_type="users",
            resource_id=str(user.id),
            method="LOCAL",
            path="scripts/recover_admin_access.py",
            status_code=200,
            success=True,
            ip_address="local-console",
            details_json={"sessions_revoked": revoked, "password_logged": False},
        )
    )
    db.commit()
    db.refresh(user)
    return user, revoked
