from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import socket

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.domain import router
from app.api.auth import router as auth_router
from app.api.maintenance import router as maintenance_router
from app.api.sync import admin_router as sync_admin_router
from app.api.sync import router as sync_router
from app.api.operations import router as operations_router
from app.api.workflow import router as workflow_router
from app.api.client_auth import router as client_auth_router
from app.api.client_shop import router as client_shop_router
from app.api.client_service import router as client_service_router
from app.api.client_forum import router as client_forum_router
from app.api.client_admin import router as client_admin_router
from app.core.config import RESOURCE_DIR, settings
from app.core.database import create_schema
from app.core.migrations import assert_database_at_head
from app.core.exceptions import install_exception_handlers
from app.core.logging import configure_logging
from app.core.database import SessionLocal
from app.services.auth import add_audit_log
import secrets


logger = logging.getLogger(__name__)


def _effective_trusted_hosts() -> list[str]:
    configured = set(settings.trusted_hosts)
    if "*" in configured and settings.allow_unsafe_trusted_host_wildcard:
        return ["*"]
    configured.discard("*")
    configured.update({"localhost", "127.0.0.1", "::1"})
    try:
        configured.update(
            info[4][0]
            for info in socket.getaddrinfo(socket.gethostname(), None)
            if info[4] and info[4][0]
        )
    except OSError:
        logger.warning("无法枚举本机地址；Trusted Host 仅保留显式配置")
    return sorted(configured)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.enforce_database_revision:
        assert_database_at_head()
    else:
        create_schema()
    yield


configure_logging()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_effective_trusted_hosts())
install_exception_handlers(app)
app.include_router(auth_router)
app.include_router(router)
app.include_router(maintenance_router)
app.include_router(sync_router)
app.include_router(sync_admin_router)
app.include_router(operations_router)
app.include_router(workflow_router)
app.include_router(client_auth_router)
app.include_router(client_shop_router)
app.include_router(client_service_router)
app.include_router(client_forum_router)
app.include_router(client_admin_router)
static_dir = RESOURCE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
client_dist = static_dir / "client"
if (client_dist / "assets").is_dir():
    app.mount(
        "/client-assets",
        StaticFiles(directory=client_dist),
        name="client-assets",
    )


@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or secrets.token_hex(12)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if (
        request.url.path.startswith("/api/")
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.url.path not in {"/api/auth/login", "/api/auth/setup"}
        and getattr(request.state, "user", None) is not None
    ):
        try:
            with SessionLocal() as db:
                route = request.scope.get("route")
                route_path = getattr(route, "path", request.url.path)
                add_audit_log(
                    db,
                    request,
                    action=f"{request.method} {route_path}",
                    success=response.status_code < 400,
                    status_code=response.status_code,
                    user=request.state.user,
                    details={"request_id": request_id},
                )
                db.commit()
        except Exception:
            logger.exception(
                "写入请求审计日志失败：%s %s request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self'; font-src 'self' data:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
    )
    if (
        request.url.path.startswith("/api/")
        or request.url.path == "/"
        or request.url.path == "/client"
        or request.url.path.startswith("/client/")
    ):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/client-assets/assets/"):
        # 哈希资源长期缓存，HTML 壳始终检查新版本。
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/client", include_in_schema=False)
@app.get("/client/{path:path}", include_in_schema=False)
def client_index(path: str = "") -> FileResponse:
    index_path = client_dist / "index.html"
    if not index_path.is_file():
        return FileResponse(static_dir / "index.html", status_code=503)
    return FileResponse(index_path)
