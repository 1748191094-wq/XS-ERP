from __future__ import annotations

import socket
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.entities import SystemSetting, UserSession


ALLOW_LAN_KEY = "network.allow_lan"


@dataclass(slots=True)
class NetworkRuntimeConfig:
    allow_lan: bool
    source: str


def load_network_config(db: Session) -> NetworkRuntimeConfig:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == ALLOW_LAN_KEY))
    if not row:
        return NetworkRuntimeConfig(allow_lan=settings.allow_lan, source="environment")
    return NetworkRuntimeConfig(
        allow_lan=row.value.strip().lower() in {"1", "true", "yes", "on"},
        source="database",
    )


def save_network_config(db: Session, allow_lan: bool) -> NetworkRuntimeConfig:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == ALLOW_LAN_KEY))
    if row:
        row.value = str(allow_lan).lower()
        row.description = "是否允许局域网成员端连接（重启主机服务后生效）"
        row.is_secret = False
    else:
        db.add(SystemSetting(
            key=ALLOW_LAN_KEY,
            value=str(allow_lan).lower(),
            description="是否允许局域网成员端连接（重启主机服务后生效）",
            is_secret=False,
        ))
    db.commit()
    return load_network_config(db)


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            value = info[4][0]
            if value and not value.startswith("127.") and value != "0.0.0.0":
                addresses.add(value)
    except OSError:
        pass
    return sorted(addresses)


def runtime_bind_host() -> str:
    return os.getenv("SERVICE_RUNTIME_BIND_HOST", settings.server_host).strip() or settings.server_host


def online_members(db: Session, *, minutes: int = 5) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=minutes)
    sessions = list(db.scalars(
        select(UserSession)
        .where(
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            UserSession.last_seen_at >= cutoff,
        )
        .options(joinedload(UserSession.user))
        .order_by(UserSession.last_seen_at.desc())
    ))
    unique: dict[int, dict] = {}
    for session in sessions:
        if session.user_id in unique:
            continue
        unique[session.user_id] = {
            "user_id": session.user_id,
            "username": session.user.username,
            "display_name": session.user.display_name,
            "role": session.user.role,
            "last_seen_at": session.last_seen_at,
            "ip_address": session.ip_address,
        }
    return list(unique.values())
