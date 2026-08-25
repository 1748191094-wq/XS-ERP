from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.core.security import token_digest
from app.models.client import ClientAccount, ClientSession


@dataclass(slots=True)
class ClientContext:
    account: ClientAccount
    session: ClientSession


def get_client_context(
    request: Request, db: Session = Depends(get_db)
) -> ClientContext:
    context = get_optional_client_context(request, db)
    if context is None:
        if request.cookies.get(settings.client_session_cookie_name):
            raise BusinessError(
                "客户登录状态已失效，请重新登录",
                code="client_session_expired",
                status_code=401,
            )
        raise BusinessError(
            "请先登录客户账号", code="client_authentication_required", status_code=401
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not hmac.compare_digest(csrf, context.session.csrf_token):
            raise BusinessError(
                "安全校验失败，请刷新页面后重试",
                code="client_csrf_failed",
                status_code=403,
            )
    return context


def get_optional_client_context(
    request: Request, db: Session = Depends(get_db)
) -> ClientContext | None:
    """Resolve a valid client session without making public routes require login."""
    raw_token = request.cookies.get(settings.client_session_cookie_name)
    if not raw_token:
        return None
    session = db.scalar(
        select(ClientSession)
        .where(ClientSession.token_hash == token_digest(raw_token))
        .options(joinedload(ClientSession.account))
    )
    now = datetime.now(timezone.utc)
    if (
        not session
        or session.revoked_at
        or session.expires_at <= now
        or session.account.status != "active"
    ):
        return None
    if (now - session.last_seen_at).total_seconds() >= 30:
        session.last_seen_at = now
        db.commit()
    request.state.client_account = session.account
    request.state.client_session = session
    return ClientContext(account=session.account, session=session)


def require_client_account(
    context: ClientContext = Depends(get_client_context),
) -> ClientAccount:
    return context.account
