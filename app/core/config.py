from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parents[2]
FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR)).resolve()
_FROZEN_DATA_DIR = os.getenv("SERVICE_DATA_DIR", "").strip()
BASE_DIR = (
    Path(_FROZEN_DATA_DIR).expanduser().resolve()
    if FROZEN and _FROZEN_DATA_DIR
    else Path(sys.executable).resolve().parent
    if FROZEN
    else SOURCE_DIR
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _env_path(name: str, default: Path) -> Path:
    value = Path(os.getenv(name, str(default)))
    return (BASE_DIR / value).resolve() if not value.is_absolute() else value.resolve()


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "服务管理系统")
    app_version: str = os.getenv("APP_VERSION", "0.1.0")
    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{(BASE_DIR / 'repair_management.db').as_posix()}"
    )
    upload_dir: Path = _env_path("UPLOAD_DIR", BASE_DIR / "uploads")
    report_dir: Path = _env_path("REPORT_DIR", BASE_DIR / "output" / "pdf")
    email_snapshot_dir: Path = _env_path("EMAIL_SNAPSHOT_DIR", BASE_DIR / "output" / "email_snapshots")
    backup_dir: Path = _env_path("BACKUP_DIR", BASE_DIR / "backups" / "system")
    point_map_reference_root: Path = _env_path(
        "POINT_MAP_REFERENCE_ROOT",
        Path(r"F:\大疆官方资料") if os.name == "nt" else BASE_DIR / "reference-point-maps",
    )
    point_map_import_batch_limit: int = int(os.getenv("POINT_MAP_IMPORT_BATCH_LIMIT", "500"))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    allowed_attachment_extensions: tuple[str, ...] = tuple(
        x.strip().lower()
        for x in os.getenv(
            "ALLOWED_ATTACHMENT_EXTENSIONS",
            ".jpg,.jpeg,.png,.webp,.mp4,.mov,.pdf,.txt,.dat,.ulg,.bin,.csv,.zip",
        ).split(",")
        if x.strip()
    )
    pdf_font_path: str = os.getenv("PDF_FONT_PATH", "")
    pdf_logo_path: str = os.getenv("PDF_LOGO_PATH", "")
    pdf_brand_name: str = os.getenv("PDF_BRAND_NAME", "")
    pdf_quote_title: str = os.getenv("PDF_QUOTE_TITLE", "")
    pdf_footer_text: str = os.getenv("PDF_FOOTER_TEXT", "")
    pdf_payment_url: str = os.getenv("PDF_PAYMENT_URL", "")
    wecom_mode: str = os.getenv("WECOM_MODE", "mock").strip().lower()
    wecom_corp_id: str = os.getenv("WECOM_CORP_ID", "").strip()
    wecom_agent_id: str = os.getenv("WECOM_AGENT_ID", "").strip()
    wecom_app_secret: str = os.getenv(
        "WECOM_APP_SECRET", os.getenv("WECOM_SECRET", "")
    ).strip()
    # 保留旧属性，避免已有状态页或扩展代码在升级时中断。
    wecom_secret: str = os.getenv(
        "WECOM_APP_SECRET", os.getenv("WECOM_SECRET", "")
    ).strip()
    wecom_callback_token: str = os.getenv("WECOM_CALLBACK_TOKEN", "").strip()
    wecom_callback_aes_key: str = os.getenv("WECOM_CALLBACK_AES_KEY", "").strip()
    wecom_callback_host: str = os.getenv("WECOM_CALLBACK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    wecom_callback_port: int = int(os.getenv("WECOM_CALLBACK_PORT", "8011"))
    wecom_timeout_seconds: int = max(3, int(os.getenv("WECOM_TIMEOUT_SECONDS", "15")))
    sf_partner_id: str = os.getenv("SF_PARTNER_ID", "")
    sf_checkword: str = os.getenv("SF_CHECKWORD", "")
    email_mode: str = os.getenv("EMAIL_MODE", "mock").strip().lower()
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.feishu.cn")
    smtp_port: int = int(os.getenv("SMTP_PORT", "465"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_email: str = os.getenv("SMTP_FROM_EMAIL", os.getenv("SMTP_USERNAME", ""))
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "")
    smtp_reply_to: str = os.getenv("SMTP_REPLY_TO", "")
    smtp_use_starttls: bool = _env_bool("SMTP_USE_STARTTLS", True)
    smtp_timeout_seconds: int = int(os.getenv("SMTP_TIMEOUT_SECONDS", "15"))
    dji_flight_record_parser_path: str = os.getenv("DJI_FLIGHT_RECORD_PARSER_PATH", "").strip()
    dji_flight_record_parser_timeout_seconds: int = int(
        os.getenv("DJI_FLIGHT_RECORD_PARSER_TIMEOUT_SECONDS", "120")
    )
    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "service_repair_session")
    session_hours: int = int(os.getenv("SESSION_HOURS", "12"))
    session_cookie_secure: bool = _env_bool("SESSION_COOKIE_SECURE", False)
    client_session_cookie_name: str = os.getenv(
        "CLIENT_SESSION_COOKIE_NAME", "service_client_session"
    )
    client_session_days: int = max(1, int(os.getenv("CLIENT_SESSION_DAYS", "30")))
    client_session_cookie_secure: bool = _env_bool(
        "CLIENT_SESSION_COOKIE_SECURE", session_cookie_secure
    )
    client_login_max_failures: int = max(
        3, int(os.getenv("CLIENT_LOGIN_MAX_FAILURES", "5"))
    )
    client_login_lock_minutes: int = max(
        1, int(os.getenv("CLIENT_LOGIN_LOCK_MINUTES", "15"))
    )
    admin_login_max_failures: int = max(
        3, int(os.getenv("ADMIN_LOGIN_MAX_FAILURES", "5"))
    )
    admin_login_lock_minutes: int = max(
        1, int(os.getenv("ADMIN_LOGIN_LOCK_MINUTES", "15"))
    )
    client_max_image_bytes: int = max(
        1024 * 1024, int(os.getenv("CLIENT_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
    )
    client_max_video_bytes: int = max(
        5 * 1024 * 1024,
        int(os.getenv("CLIENT_MAX_VIDEO_BYTES", str(100 * 1024 * 1024))),
    )
    client_max_uploads_per_resource: int = max(
        1, int(os.getenv("CLIENT_MAX_UPLOADS_PER_RESOURCE", "12"))
    )
    password_hash_iterations: int = int(os.getenv("PASSWORD_HASH_ITERATIONS", "310000"))
    trusted_hosts: tuple[str, ...] = tuple(
        host.strip()
        for host in os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1").split(",")
        if host.strip()
    ) or ("localhost", "127.0.0.1")
    allow_unsafe_trusted_host_wildcard: bool = _env_bool(
        "ALLOW_UNSAFE_TRUSTED_HOST_WILDCARD", False
    )
    enable_api_docs: bool = _env_bool("ENABLE_API_DOCS", False)
    enforce_database_revision: bool = _env_bool("ENFORCE_DATABASE_REVISION", True)
    server_host: str = os.getenv("SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    server_port: int = int(os.getenv("SERVER_PORT", "8000"))
    allow_lan: bool = _env_bool("ALLOW_LAN", False)
    sync_role: str = os.getenv("SYNC_ROLE", "standalone").strip().lower()
    sync_node_id: str = os.getenv("SYNC_NODE_ID", "").strip()
    sync_node_name: str = os.getenv("SYNC_NODE_NAME", "").strip()
    sync_host_url: str = os.getenv("SYNC_HOST_URL", "").strip().rstrip("/")
    sync_shared_secret: str = os.getenv("SYNC_SHARED_SECRET", "").strip()
    sync_interval_seconds: int = max(30, int(os.getenv("SYNC_INTERVAL_SECONDS", "300")))


settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.report_dir.mkdir(parents=True, exist_ok=True)
settings.email_snapshot_dir.mkdir(parents=True, exist_ok=True)
settings.backup_dir.mkdir(parents=True, exist_ok=True)
