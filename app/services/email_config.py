from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.secret_vault import SecretVault
from app.models.entities import SystemSetting
from app.services.branding import load_brand_name


@dataclass(slots=True)
class EmailRuntimeConfig:
    mode: str
    sender: str
    smtp_host: str
    smtp_port: int
    password: str
    from_name: str
    reply_to: str
    use_starttls: bool
    timeout_seconds: int
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.smtp_host and self.sender and self.password)


KEYS = {
    "mode": "email.mode",
    "sender": "email.sender",
    "smtp_host": "email.smtp_host",
    "smtp_port": "email.smtp_port",
    "password": "email.smtp_password",
    "from_name": "email.from_name",
    "reply_to": "email.reply_to",
    "use_starttls": "email.use_starttls",
    "timeout_seconds": "email.timeout_seconds",
}


def load_email_config(db: Session) -> EmailRuntimeConfig:
    rows = {row.key: row.value for row in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(KEYS.values())))}
    source = "database" if rows else "environment"
    encrypted = rows.get(KEYS["password"], "")
    password = settings.smtp_password
    if encrypted:
        try:
            password = SecretVault.decrypt(encrypted)
        except Exception:
            password = ""
    default_from_name = f"{load_brand_name(db)}服务中心"
    return EmailRuntimeConfig(
        mode=rows.get(KEYS["mode"], settings.email_mode).strip().lower() or "mock",
        sender=rows.get(KEYS["sender"], settings.smtp_username or settings.smtp_from_email).strip(),
        smtp_host=rows.get(KEYS["smtp_host"], settings.smtp_host).strip(),
        smtp_port=int(rows.get(KEYS["smtp_port"], str(settings.smtp_port)) or 465),
        password=password,
        from_name=rows.get(KEYS["from_name"], settings.smtp_from_name).strip() or default_from_name,
        reply_to=rows.get(KEYS["reply_to"], settings.smtp_reply_to).strip(),
        use_starttls=rows.get(KEYS["use_starttls"], str(settings.smtp_use_starttls)).lower() in {"1", "true", "yes", "on"},
        timeout_seconds=int(rows.get(KEYS["timeout_seconds"], str(settings.smtp_timeout_seconds)) or 15),
        source=source,
    )


def _set(db: Session, key: str, value: str, *, secret: bool = False, description: str = "") -> None:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row:
        row.value, row.is_secret, row.description = value, secret, description
    else:
        db.add(SystemSetting(key=key, value=value, is_secret=secret, description=description))


def save_email_config(db: Session, payload) -> EmailRuntimeConfig:
    values = {
        "mode": payload.mode,
        "sender": payload.sender.strip(),
        "smtp_host": payload.smtp_host.strip(),
        "smtp_port": str(payload.smtp_port),
        "from_name": payload.from_name.strip(),
        "reply_to": (payload.reply_to or "").strip(),
        "use_starttls": str(payload.use_starttls).lower(),
        "timeout_seconds": str(payload.timeout_seconds),
    }
    for name, value in values.items():
        _set(db, KEYS[name], value, description="邮件服务配置")
    if payload.clear_password:
        _set(db, KEYS["password"], "", secret=True, description="SMTP 授权码（已清除）")
    elif payload.password:
        _set(db, KEYS["password"], SecretVault.encrypt(payload.password), secret=True, description="SMTP 授权码（本机加密）")
    db.commit()
    return load_email_config(db)


def safe_email_config(config: EmailRuntimeConfig) -> dict:
    return {
        "mode": config.mode,
        "configured": config.configured,
        "sender": config.sender,
        "smtp_host": config.smtp_host,
        "smtp_port": config.smtp_port,
        "from_name": config.from_name,
        "reply_to": config.reply_to,
        "use_starttls": config.use_starttls,
        "timeout_seconds": config.timeout_seconds,
        "password_configured": bool(config.password),
        "source": config.source,
        "will_transmit_externally": config.mode == "smtp" and config.configured,
    }
