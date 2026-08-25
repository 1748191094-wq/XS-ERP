from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.core.security import token_digest
from app.models.entities import User, UserSession


@dataclass(slots=True)
class AuthContext:
    user: User
    session: UserSession


def _call_operator_write_allowed(request: Request) -> bool:
    route = request.scope.get("route")
    return (
        request.method == "POST"
        and getattr(route, "path", None) == "/api/outbound-calls/{call_id}/complete"
    )


def get_auth_context(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise BusinessError("请先登录", code="authentication_required", status_code=401)
    session = db.scalar(
        select(UserSession)
        .where(UserSession.token_hash == token_digest(raw_token))
        .options(joinedload(UserSession.user))
    )
    now = datetime.now(timezone.utc)
    if not session or session.revoked_at or session.expires_at <= now or not session.user.enabled:
        raise BusinessError("登录状态已失效，请重新登录", code="session_expired", status_code=401)
    if (now - session.last_seen_at).total_seconds() >= 30:
        session.last_seen_at = now
        db.commit()
    request.state.user = session.user
    request.state.auth_session = session
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not hmac.compare_digest(csrf, session.csrf_token):
            raise BusinessError("安全校验失败，请刷新页面后重试", code="csrf_failed", status_code=403)
        if session.user.role == "viewer" or (
            session.user.role == "call_operator" and not _call_operator_write_allowed(request)
        ):
            raise BusinessError("当前账号只有查看权限", code="permission_denied", status_code=403)
    return AuthContext(user=session.user, session=session)


def require_authenticated_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user


def require_roles(*allowed_roles: str) -> Callable:
    def _dependency(context: AuthContext = Depends(get_auth_context)) -> User:
        if context.user.role not in allowed_roles:
            raise BusinessError("当前账号无权执行此操作", code="permission_denied", status_code=403)
        return context.user

    return _dependency


finance_access = require_roles("admin", "manager", "finance")
inventory_access = require_roles("admin", "manager", "warehouse")
admin_access = require_roles("admin")
audit_access = require_roles("admin", "manager")
