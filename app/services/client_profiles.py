from __future__ import annotations

from app.models.client import ClientAccount


def client_avatar_url(account: ClientAccount) -> str | None:
    """Return a cache-safe public avatar URL without exposing its storage path."""
    if not account.avatar_path:
        return None
    version = int(account.updated_at.timestamp()) if account.updated_at else 0
    return f"/api/client/avatars/{account.id}?v={version}"
