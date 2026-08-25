from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.helpers import ok
from app.core.client_auth import ClientContext, get_client_context
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.core.security import hash_password, verify_password
from app.models.client import Cart, ClientAccount, ClientSession
from app.models.entities import Customer
from app.schemas.client import (
    ClientLogin,
    ClientIdentifierUpdate,
    ClientPasswordChange,
    ClientProfileUpdate,
    ClientRegister,
)
from app.services.client_auth import (
    CLIENT_BUSINESS_TIMEZONE,
    IDENTIFIER_CHANGE_ACTION,
    add_client_action_log,
    create_client_session,
    identifier_change_status,
)
from app.services.client_profiles import client_avatar_url
from app.services.numbering import make_no


router = APIRouter(prefix="/api/client/auth", tags=["client-auth"])


def _set_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        settings.client_session_cookie_name,
        raw_token,
        max_age=settings.client_session_days * 24 * 3600,
        httponly=True,
        secure=settings.client_session_cookie_secure,
        samesite="strict",
        path="/",
    )


def account_data(account: ClientAccount, session: ClientSession | None = None) -> dict:
    data = {
        "id": account.id,
        "customer_id": account.customer_id,
        "username": account.username,
        "identifier": f"@{account.username}",
        "phone": account.phone,
        "email": account.email,
        "nickname": account.nickname,
        "avatar_url": client_avatar_url(account),
        "status": account.status,
        "last_login_at": account.last_login_at,
        "created_at": account.created_at,
    }
    if session:
        data["csrf_token"] = session.csrf_token
        data["expires_at"] = session.expires_at
    return data


@router.get("/status")
def client_auth_status() -> dict:
    return ok({"registration_enabled": True, "authentication": "secure_cookie"})


@router.post("/register", status_code=201)
def register(
    payload: ClientRegister,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    email = payload.email.lower() if payload.email else None
    conflict = db.scalar(
        select(ClientAccount.id).where(
            or_(
                func.lower(ClientAccount.username) == payload.username.lower(),
                ClientAccount.phone == payload.phone,
                ClientAccount.email == email if email else False,
            )
        )
    )
    if conflict:
        raise BusinessError(
            "用户名、手机号或邮箱已被使用", code="client_account_conflict", status_code=409
        )
    customer = db.scalar(
        select(Customer).where(
            Customer.phone == payload.phone, Customer.deleted_at.is_(None)
        )
    )
    if customer and db.scalar(
        select(ClientAccount.id).where(ClientAccount.customer_id == customer.id)
    ):
        raise BusinessError(
            "该客户资料已经绑定账号", code="customer_already_bound", status_code=409
        )
    if not customer:
        customer = Customer(
            customer_no=make_no("CU"),
            name=payload.nickname,
            phone=payload.phone,
            email=email,
            customer_type="individual",
        )
        db.add(customer)
        db.flush()
    elif email and not customer.email:
        customer.email = email
    account = ClientAccount(
        customer_id=customer.id,
        username=payload.username,
        phone=payload.phone,
        email=email,
        password_hash=hash_password(payload.password),
        nickname=payload.nickname,
        status="active",
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(
            "用户名、手机号或邮箱已被使用", code="client_account_conflict", status_code=409
        ) from exc
    db.add(Cart(account_id=account.id))
    raw_token, session = create_client_session(db, account, request)
    account.last_login_at = datetime.now(timezone.utc)
    add_client_action_log(db, request, action="client.auth.register", account=account)
    db.commit()
    _set_cookie(response, raw_token)
    return ok(account_data(account, session))


@router.post("/login")
def login(
    payload: ClientLogin,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    login_value = payload.login.lower()
    account = db.scalar(
        select(ClientAccount).where(
            or_(
                func.lower(ClientAccount.username) == login_value,
                func.lower(ClientAccount.phone) == login_value,
                func.lower(ClientAccount.email) == login_value,
            )
        )
    )
    now = datetime.now(timezone.utc)
    if account and account.locked_until and account.locked_until > now:
        add_client_action_log(
            db,
            request,
            action="client.auth.login",
            account=account,
            success=False,
            details={"reason": "locked"},
        )
        db.commit()
        raise BusinessError(
            "登录尝试过多，请稍后再试", code="client_login_locked", status_code=429
        )
    valid = bool(
        account
        and account.status == "active"
        and verify_password(payload.password, account.password_hash)
    )
    if not valid:
        if account:
            account.failed_login_count += 1
            if account.failed_login_count >= settings.client_login_max_failures:
                account.locked_until = now + timedelta(
                    minutes=settings.client_login_lock_minutes
                )
                account.failed_login_count = 0
        add_client_action_log(
            db,
            request,
            action="client.auth.login",
            account=account,
            success=False,
            details={"reason": "invalid_credentials"},
        )
        db.commit()
        raise BusinessError("用户名或密码错误", code="invalid_credentials", status_code=401)
    account.failed_login_count = 0
    account.locked_until = None
    account.last_login_at = now
    raw_token, session = create_client_session(db, account, request)
    add_client_action_log(db, request, action="client.auth.login", account=account)
    db.commit()
    _set_cookie(response, raw_token)
    return ok(account_data(account, session))


@router.get("/me")
def me(context: ClientContext = Depends(get_client_context)) -> dict:
    return ok(account_data(context.account, context.session))


@router.post("/logout")
def logout(
    response: Response,
    request: Request,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    context.session.revoked_at = datetime.now(timezone.utc)
    add_client_action_log(
        db, request, action="client.auth.logout", account=context.account
    )
    db.commit()
    response.delete_cookie(settings.client_session_cookie_name, path="/")
    return ok({"logged_out": True})


@router.patch("/profile")
def update_profile(
    payload: ClientProfileUpdate,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    values = payload.model_dump(exclude_unset=True)
    account = context.account
    customer = db.get(Customer, account.customer_id)
    if "phone" in values and values["phone"] is not None:
        account.phone = values["phone"]
        if customer:
            customer.phone = values["phone"]
    if "email" in values:
        normalized = values["email"].lower() if values["email"] else None
        account.email = normalized
        if customer:
            customer.email = normalized
    if values.get("nickname"):
        account.nickname = values["nickname"]
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(
            "手机号或邮箱已被使用", code="client_profile_conflict", status_code=409
        ) from exc
    return ok(account_data(account, context.session))


@router.patch("/identifier")
def update_identifier(
    payload: ClientIdentifierUpdate,
    request: Request,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    account = context.account
    status = identifier_change_status(db, account.id)
    if status["remaining"] <= 0:
        raise BusinessError(
            "本自然年两次识别码修改机会已用完",
            code="identifier_change_limit_reached",
            status_code=429,
        )
    if payload.identifier == account.username:
        raise BusinessError(
            "新识别码与当前识别码相同",
            code="identifier_unchanged",
            status_code=409,
        )
    conflict = db.scalar(
        select(ClientAccount.id).where(
            func.lower(ClientAccount.username) == payload.identifier,
            ClientAccount.id != account.id,
        )
    )
    if conflict:
        raise BusinessError(
            "该识别码已被使用",
            code="identifier_conflict",
            status_code=409,
        )
    previous = account.username
    account.username = payload.identifier
    add_client_action_log(
        db,
        request,
        action=IDENTIFIER_CHANGE_ACTION,
        account=account,
        resource_type="client_account",
        resource_id=account.id,
        details={
            "previous_identifier": f"@{previous}",
            "new_identifier": f"@{payload.identifier}",
            "calendar_year": datetime.now(timezone.utc).astimezone(CLIENT_BUSINESS_TIMEZONE).year,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(
            "该识别码已被使用",
            code="identifier_conflict",
            status_code=409,
        ) from exc
    db.refresh(account)
    return ok(
        {
            "account": account_data(account, context.session),
            "identifier_change": identifier_change_status(db, account.id),
        }
    )


@router.post("/change-password")
def change_password(
    payload: ClientPasswordChange,
    context: ClientContext = Depends(get_client_context),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(payload.current_password, context.account.password_hash):
        raise BusinessError(
            "当前密码不正确", code="invalid_current_password", status_code=400
        )
    context.account.password_hash = hash_password(payload.new_password)
    db.execute(
        update(ClientSession)
        .where(
            ClientSession.account_id == context.account.id,
            ClientSession.id != context.session.id,
            ClientSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.commit()
    return ok({"changed": True})
