from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import ok
from app.core.auth import admin_access
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.models.entities import SyncConflict, SyncOutboxEvent
from app.services.sync import changes_after, receive_events, resolve_conflict, status
from app.services.numbering import allocate_local_repair_order_no


router = APIRouter(prefix="/api/sync")
admin_router = APIRouter(prefix="/api/sync", dependencies=[Depends(admin_access)])


class SyncEventInput(BaseModel):
    event_id: str = Field(min_length=36, max_length=36)
    entity_type: str = Field(max_length=50)
    record_key: str = Field(max_length=240)
    operation: str = "upsert"
    base_revision: int = Field(ge=0)
    base_payload_json: dict[str, Any] | None = None
    payload_json: dict[str, Any]
    payload_hash: str = Field(min_length=64, max_length=64)


class SyncPushInput(BaseModel):
    node_id: str = Field(min_length=36, max_length=36)
    events: list[SyncEventInput] = Field(default_factory=list, max_length=500)


class SyncConflictResolutionInput(BaseModel):
    resolution: str = Field(pattern="^(keep_host|accept_terminal)$")


def require_sync_secret(x_sync_secret: str = Header(default="")) -> None:
    configured = settings.sync_shared_secret
    if len(configured) < 24:
        raise BusinessError(
            "同步主机尚未配置至少 24 位共享密钥",
            code="sync_secret_not_configured",
            status_code=503,
        )
    if not hmac.compare_digest(configured, x_sync_secret):
        raise BusinessError("同步密钥不正确", code="sync_auth_failed", status_code=401)


@router.post("/numbering/repair-orders/next", dependencies=[Depends(require_sync_secret)])
def next_repair_order_number(db: Session = Depends(get_db)) -> dict:
    if settings.sync_role != "host":
        raise BusinessError("当前节点不是同步主机", code="sync_host_required", status_code=409)
    order_no = allocate_local_repair_order_no(db)
    db.commit()
    return ok({"order_no": order_no})


@router.post("/push", dependencies=[Depends(require_sync_secret)])
def push(payload: SyncPushInput, request: Request, db: Session = Depends(get_db)) -> dict:
    return ok(receive_events(
        db,
        payload.node_id,
        [event.model_dump() for event in payload.events],
        ip_address=request.client.host if request.client else None,
    ))


@router.get("/pull", dependencies=[Depends(require_sync_secret)])
def pull(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    return ok(changes_after(db, after, limit=limit))


@admin_router.get("/status")
def sync_status(db: Session = Depends(get_db)) -> dict:
    return ok(status(db))


@admin_router.get("/conflicts")
def list_conflicts(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(
        select(SyncConflict).order_by(SyncConflict.created_at.desc()).limit(500)
    ))
    return ok(rows)


@admin_router.post("/conflicts/{conflict_id}/resolve")
def resolve_sync_conflict(
    conflict_id: str,
    payload: SyncConflictResolutionInput,
    db: Session = Depends(get_db),
) -> dict:
    return ok(resolve_conflict(db, conflict_id, payload.resolution))


@admin_router.get("/outbox")
def list_outbox(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(
        select(SyncOutboxEvent).order_by(SyncOutboxEvent.created_at.desc()).limit(500)
    ))
    return ok(rows)
