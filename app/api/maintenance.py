from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.helpers import ok
from app.core.auth import admin_access
from app.core.config import RESOURCE_DIR, settings
from app.core.database import get_db
from app.core.exceptions import BusinessError
from app.models.entities import BackupRecord, User
from app.services.backup import BackupService
from app.services.network import (
    load_network_config,
    local_ipv4_addresses,
    online_members,
    runtime_bind_host,
    save_network_config,
)


router = APIRouter(prefix="/api", dependencies=[Depends(admin_access)])


class BackupCreate(BaseModel):
    notes: str | None = Field(default=None, max_length=500)


class NetworkConfigUpdate(BaseModel):
    allow_lan: bool


@router.get("/host/status")
def host_status(request: Request, db: Session = Depends(get_db)) -> dict:
    config = load_network_config(db)
    port = request.url.port or settings.server_port
    addresses = local_ipv4_addresses()
    bind_host = runtime_bind_host()
    running_lan_bound = bind_host in {"0.0.0.0", "::", "[::]"}
    if settings.database_url.startswith("sqlite"):
        database_health = db.execute(text("PRAGMA quick_check")).scalar_one()
    else:
        database_health = "managed_database"
    return ok({
        "service_status": "running",
        "mode": "lan_host" if config.allow_lan else "standalone",
        "allow_lan": config.allow_lan,
        "config_source": config.source,
        "running_bind_host": bind_host,
        "running_lan_bound": running_lan_bound,
        "restart_required": config.allow_lan != running_lan_bound,
        "port": port,
        "local_addresses": addresses,
        "access_urls": [f"http://{address}:{port}/" for address in addresses] if config.allow_lan else [f"http://127.0.0.1:{port}/"],
        "online_members": online_members(db),
        "database_health": database_health,
        "backup_count": db.scalar(select(func.count(BackupRecord.id))) or 0,
        "external_tasks_policy": "queue_when_offline",
    })


@router.put("/host/network")
def update_host_network(payload: NetworkConfigUpdate, db: Session = Depends(get_db)) -> dict:
    config = save_network_config(db, payload.allow_lan)
    running_lan_bound = runtime_bind_host() in {"0.0.0.0", "::", "[::]"}
    return ok({
        "allow_lan": config.allow_lan,
        "source": config.source,
        "restart_required": config.allow_lan != running_lan_bound,
        "message": "设置已保存；请重启主机服务使监听地址变更生效。",
    })


@router.get("/host/member-client/download")
def download_member_client() -> FileResponse:
    path = RESOURCE_DIR / "member_client.html"
    if not path.is_file():
        raise BusinessError("成员端连接页不存在", code="member_client_missing", status_code=404)
    return FileResponse(path, filename="维修管理成员端.html", media_type="text/html; charset=utf-8")


@router.get("/backups")
def list_backups(db: Session = Depends(get_db)) -> dict:
    return ok(list(db.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(500))))


@router.post("/backups", status_code=201)
def create_backup(payload: BackupCreate, user: User = Depends(admin_access), db: Session = Depends(get_db)) -> dict:
    return ok(BackupService.create(db, created_by=user.id, notes=payload.notes))


@router.post("/backups/{backup_id}/verify")
def verify_backup(backup_id: int, db: Session = Depends(get_db)) -> dict:
    record = db.get(BackupRecord, backup_id)
    if not record:
        raise BusinessError("备份记录不存在", code="backup_not_found", status_code=404)
    return ok(BackupService.verify(db, record))


@router.get("/backups/{backup_id}/download")
def download_backup(backup_id: int, db: Session = Depends(get_db)) -> FileResponse:
    record = db.get(BackupRecord, backup_id)
    if not record:
        raise BusinessError("备份记录不存在", code="backup_not_found", status_code=404)
    path = Path(record.storage_path).resolve()
    if settings.backup_dir.resolve() not in path.parents or not path.is_file():
        raise BusinessError("备份文件不存在", code="backup_file_missing", status_code=404)
    return FileResponse(path, filename=record.filename, media_type="application/x-sqlite3")
