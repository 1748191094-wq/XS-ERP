from __future__ import annotations

from datetime import datetime, timezone
import math

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.helpers import ok
from app.core.auth import AuthContext, admin_access, audit_access, get_auth_context
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.core.security import generate_management_password, hash_password, verify_password
from app.models.entities import AuditLog, User, UserSession
from app.schemas.domain import (
    AuthSetupRequest,
    LoginRequest,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.services.admin_credentials import (
    admin_login_locked_until,
    recent_admin_login_failures,
    revoke_user_sessions,
)
from app.services.auth import add_audit_log, create_session
from app.services.branding import branding_payload, load_brand_name, save_initial_brand_name


router = APIRouter(prefix="/api")


def _next_employee_no(db: Session) -> str:
    """Allocate a compact, human-readable staff number without reusing existing values."""
    candidate = (db.scalar(select(func.max(User.id))) or 0) + 1
    while db.scalar(select(User.id).where(User.employee_no == f"ST{candidate:04d}")):
        candidate += 1
    return f"ST{candidate:04d}"


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )


def _auth_data(user: User, session: UserSession, db: Session) -> dict:
    return {
        "user": UserRead.model_validate(user),
        "csrf_token": session.csrf_token,
        "expires_at": session.expires_at,
        "brand_name": load_brand_name(db),
    }


@router.get("/health")
def health() -> dict:
    return ok({
        "service_id": "service-management-erp",
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/auth/status")
def auth_status(db: Session = Depends(get_db)) -> dict:
    initialized = bool(
        db.scalar(select(func.count(User.id)).where(User.password_hash.is_not(None), User.enabled.is_(True)))
    )
    return ok({"initialized": initialized, **branding_payload(db)})


@router.get("/branding")
def public_branding(db: Session = Depends(get_db)) -> dict:
    return ok(branding_payload(db))


@router.post("/auth/setup", status_code=201)
def setup(payload: AuthSetupRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(select(func.count(User.id)).where(User.password_hash.is_not(None))) or 0
    if existing:
        raise BusinessError("系统已经完成初始化", code="already_initialized", status_code=409)
    generated_password = generate_management_password()
    user = User(
        username=payload.username.strip(),
        employee_no=_next_employee_no(db),
        display_name=payload.display_name.strip(),
        role="admin",
        enabled=True,
        password_hash=hash_password(generated_password),
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    brand_name = save_initial_brand_name(db, payload.brand_name)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError("用户名已被占用，请换一个用户名", code="username_conflict", status_code=409) from exc
    raw_token, session = create_session(db, user, request)
    add_audit_log(db, request, action="auth.setup", success=True, status_code=201, user=user)
    db.commit()
    _set_session_cookie(response, raw_token)
    data = _auth_data(user, session, db)
    data["brand_name"] = brand_name
    data["generated_password"] = generated_password
    data["password_shown_once"] = True
    return ok(data)


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    username = payload.username.strip()
    now = datetime.now(timezone.utc)
    locked_until = admin_login_locked_until(db, username, now=now)
    if locked_until is not None:
        retry_after = max(1, math.ceil((locked_until - now).total_seconds()))
        response.headers["Retry-After"] = str(retry_after)
        add_audit_log(
            db,
            request,
            action="auth.login.locked",
            success=False,
            status_code=429,
            username=username,
            details={"reason": "rate_limited", "retry_after_seconds": retry_after},
        )
        db.commit()
        raise BusinessError(
            "登录失败次数过多，请稍后重试",
            code="admin_login_locked",
            status_code=429,
        )
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if not user or not user.enabled or not verify_password(payload.password, user.password_hash):
        failures, _ = recent_admin_login_failures(db, username, now=now)
        will_lock = failures + 1 >= settings.admin_login_max_failures
        status_code = 429 if will_lock else 401
        add_audit_log(
            db, request, action="auth.login", success=False, status_code=status_code,
            username=username,
            details={
                "reason": "invalid_credentials",
                "failed_attempt": failures + 1,
                "lock_threshold": settings.admin_login_max_failures,
            },
        )
        db.commit()
        if will_lock:
            response.headers["Retry-After"] = str(settings.admin_login_lock_minutes * 60)
            raise BusinessError(
                "登录失败次数过多，账户登录已临时锁定",
                code="admin_login_locked",
                status_code=429,
            )
        raise BusinessError("用户名或密码错误", code="invalid_credentials", status_code=401)
    user.last_login_at = datetime.now(timezone.utc)
    raw_token, session = create_session(db, user, request)
    add_audit_log(db, request, action="auth.login", success=True, status_code=200, user=user)
    db.commit()
    _set_session_cookie(response, raw_token)
    return ok(_auth_data(user, session, db))


@router.get("/auth/me")
def me(context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> dict:
    return ok(_auth_data(context.user, context.session, db))


@router.post("/auth/logout")
def logout(
    response: Response,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    context.session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    return ok({"logged_out": True})


@router.post("/auth/change-password")
def change_password(
    _context: AuthContext = Depends(get_auth_context),
) -> dict:
    raise BusinessError(
        "管理端密码只能由管理员在账号管理中重置",
        code="self_password_change_disabled",
        status_code=410,
    )


@router.get("/users", dependencies=[Depends(admin_access)])
def list_users(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(User).order_by(User.created_at)).all()
    return ok([UserRead.model_validate(row) for row in rows])


@router.post("/users", status_code=201, dependencies=[Depends(admin_access)])
def create_user(payload: UserCreate, db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(User.id).where(func.lower(User.username) == payload.username.strip().lower())):
        raise BusinessError("用户名已存在", code="username_conflict", status_code=409)
    if payload.wecom_userid and db.scalar(
        select(User.id).where(User.wecom_userid == payload.wecom_userid)
    ):
        raise BusinessError("企业微信 UserID 已绑定其他账号", code="wecom_userid_conflict", status_code=409)
    generated_password = generate_management_password()
    user = User(
        username=payload.username.strip(),
        employee_no=(payload.employee_no or _next_employee_no(db)).upper(),
        display_name=payload.display_name.strip(),
        role=payload.role,
        enabled=True,
        wecom_userid=payload.wecom_userid,
        password_hash=hash_password(generated_password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError("用户名已存在", code="username_conflict", status_code=409) from exc
    db.refresh(user)
    data = UserRead.model_validate(user).model_dump()
    data["generated_password"] = generated_password
    data["password_shown_once"] = True
    return ok(data)


@router.patch("/users/{user_id}", dependencies=[Depends(admin_access)])
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise BusinessError("账号不存在", code="user_not_found", status_code=404)
    removing_admin = user.role == "admin" and (
        payload.role not in {None, "admin"} or payload.enabled is False
    )
    if removing_admin:
        other_admins = db.scalar(
            select(func.count(User.id)).where(User.role == "admin", User.enabled.is_(True), User.id != user.id)
        ) or 0
        if not other_admins:
            raise BusinessError("必须至少保留一个启用的管理员账号", code="last_admin", status_code=409)
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.employee_no is not None:
        employee_no = payload.employee_no.upper()
        if db.scalar(select(User.id).where(User.employee_no == employee_no, User.id != user.id)):
            raise BusinessError("工号已被其他账号使用", code="employee_no_conflict", status_code=409)
        user.employee_no = employee_no
    if "wecom_userid" in payload.model_fields_set:
        if payload.wecom_userid and db.scalar(
            select(User.id).where(
                User.wecom_userid == payload.wecom_userid,
                User.id != user.id,
            )
        ):
            raise BusinessError("企业微信 UserID 已绑定其他账号", code="wecom_userid_conflict", status_code=409)
        user.wecom_userid = payload.wecom_userid
    if payload.role is not None:
        user.role = payload.role
    if payload.enabled is not None:
        user.enabled = payload.enabled
    if payload.enabled is False:
        revoke_user_sessions(db, user.id)
    db.commit()
    db.refresh(user)
    return ok(UserRead.model_validate(user))


@router.post("/users/{user_id}/password/reset")
def reset_user_password(
    user_id: int,
    current_user: User = Depends(admin_access),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise BusinessError("账号不存在", code="user_not_found", status_code=404)
    generated_password = generate_management_password()
    user.password_hash = hash_password(generated_password)
    revoked = revoke_user_sessions(db, user.id)
    db.commit()
    db.refresh(user)
    return ok({
        "user": UserRead.model_validate(user),
        "generated_password": generated_password,
        "password_shown_once": True,
        "sessions_revoked": revoked,
        "reset_current_user": user.id == current_user.id,
    })


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access),
) -> dict:
    """Safely retire an account while preserving every historical foreign-key reference."""
    user = db.get(User, user_id)
    if not user:
        raise BusinessError("账号不存在", code="user_not_found", status_code=404)
    if user.id == current_user.id:
        raise BusinessError("不能删除当前登录账号", code="cannot_delete_current_user", status_code=409)
    if user.role == "admin" and user.enabled:
        other_admins = db.scalar(
            select(func.count(User.id)).where(
                User.role == "admin",
                User.enabled.is_(True),
                User.id != user.id,
            )
        ) or 0
        if not other_admins:
            raise BusinessError("必须至少保留一个启用的管理员账号", code="last_admin", status_code=409)

    user.enabled = False
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.commit()
    db.refresh(user)
    return ok({"deleted": True, "user": UserRead.model_validate(user)})


@router.get("/audit-logs", dependencies=[Depends(audit_access)])
def list_audit_logs(
    username: str | None = None,
    success: bool | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if username:
        stmt = stmt.where(AuditLog.username == username.strip().lower())
    if success is not None:
        stmt = stmt.where(AuditLog.success.is_(success))
    return ok(list(db.scalars(stmt.limit(min(max(limit, 1), 500)))))
