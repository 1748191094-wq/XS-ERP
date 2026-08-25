from __future__ import annotations

import argparse
import errno
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _runtime_data_dir() -> Path:
    override = os.getenv("SERVICE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (root / "ServiceManager-ERP").resolve()


RUNTIME_DATA_DIR = _runtime_data_dir()
RUNTIME_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.chdir(RUNTIME_DATA_DIR)
os.environ.setdefault("SERVICE_DATA_DIR", str(RUNTIME_DATA_DIR))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(RUNTIME_DATA_DIR / ".env")
if not getattr(sys, "frozen", False):
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")

_runtime_paths = {
    "DATABASE_URL": f"sqlite:///{(RUNTIME_DATA_DIR / 'repair_management.db').as_posix()}",
    "UPLOAD_DIR": str(RUNTIME_DATA_DIR / "uploads"),
    "REPORT_DIR": str(RUNTIME_DATA_DIR / "output" / "pdf"),
    "EMAIL_SNAPSHOT_DIR": str(RUNTIME_DATA_DIR / "output" / "email_snapshots"),
    "BACKUP_DIR": str(RUNTIME_DATA_DIR / "backups" / "system"),
    "POINT_MAP_REFERENCE_ROOT": str(RUNTIME_DATA_DIR / "point-maps"),
    "SERVER_PORT": "8765",
}
for _name, _value in _runtime_paths.items():
    os.environ.setdefault(_name, _value)

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.core.config import BASE_DIR, RESOURCE_DIR, settings
from scripts.safe_backup_sqlite import backup_one


class StartupPortConflict(RuntimeError):
    """The requested service address is already owned by another process."""


SERVICE_ID = "service-management-erp"
LEGACY_IDENTITY_RETRY_DELAYS = (0.0, 0.1, 0.25)


def _probe_hosts(bind_host: str) -> list[str]:
    hosts = ["127.0.0.1"]
    if bind_host not in {"", "0.0.0.0", "127.0.0.1", "localhost", "::", "[::]"}:
        hosts.append(bind_host.strip("[]"))
    try:
        from app.services.network import local_ipv4_addresses

        hosts.extend(local_ipv4_addresses())
    except Exception:
        pass
    return list(dict.fromkeys(host for host in hosts if host))


def _tcp_port_open(host: str, port: int, *, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _local_http_text(host: str, port: int, path: str, *, timeout: float = 0.7) -> str | None:
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    request = urllib.request.Request(
        f"http://{url_host}:{port}{path}",
        headers={"User-Agent": "SRV-Windows-Launcher/1.0"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            return response.read(512 * 1024).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, UnicodeError):
        return None


def _is_service_platform(host: str, port: int) -> bool:
    health_text = _local_http_text(host, port, "/api/health")
    if not health_text:
        return False
    try:
        health = json.loads(health_text)
    except json.JSONDecodeError:
        return False
    if not (
        health.get("success") is True
        and isinstance(health.get("data"), dict)
        and health["data"].get("status") == "ok"
    ):
        return False
    health_data = health["data"]
    if "service_id" in health_data:
        return health_data["service_id"] == SERVICE_ID

    # 旧版本没有 service_id，短暂重试首页标识。
    for delay in LEGACY_IDENTITY_RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        index = _local_http_text(host, port, "/", timeout=1.0) or ""
        if "<title>服务管理系统</title>" in index:
            return True
    return False


def _reserve_host_socket(bind_host: str, port: int) -> socket.socket:
    host = bind_host.strip("[]") or "0.0.0.0"
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        return probe
    except OSError as exc:
        probe.close()
        if exc.errno in {errno.EADDRINUSE, 10048} or getattr(exc, "winerror", None) == 10048:
            raise StartupPortConflict(
                f"端口 {port} 已被其他程序占用，未启动第二个服务。"
                f"请先关闭占用该端口的程序，或使用 netstat -ano | findstr :{port} 查看进程。"
            ) from exc
        raise


def _print_existing_service(port: int, *, lan_mode: bool, hosts: list[str]) -> None:
    print("主机服务已在运行，无需重复启动。")
    print(f"本机访问：http://127.0.0.1:{port}/")
    if lan_mode:
        for host in hosts:
            if host != "127.0.0.1":
                print(f"成员端访问：http://{host}:{port}/")
    print("请直接使用以上地址；本窗口将自动关闭。")


def _preflight_host_start(bind_host: str, port: int, requested_lan: bool) -> bool:
    """Return True when the requested service is already running."""
    hosts = _probe_hosts(bind_host)
    open_hosts = [host for host in hosts if _tcp_port_open(host, port)]
    service_hosts = [host for host in open_hosts if _is_service_platform(host, port)]
    if service_hosts:
        running_lan = any(host != "127.0.0.1" for host in service_hosts)
        if running_lan != requested_lan:
            running_label = "局域网协作" if running_lan else "管理员单机脱机"
            requested_label = "局域网协作" if requested_lan else "管理员单机脱机"
            raise StartupPortConflict(
                f"端口 {port} 上已有服务管理{running_label}服务。"
                f"如需切换为{requested_label}模式，请先关闭当前服务窗口后再启动。"
            )
        _print_existing_service(port, lan_mode=running_lan, hosts=service_hosts)
        return True
    if open_hosts:
        raise StartupPortConflict(
            f"端口 {port} 已被其他程序占用，未启动第二个服务。"
            f"请先关闭占用该端口的程序，或使用 netstat -ano | findstr :{port} 查看进程。"
        )
    return False


def _write_env(lines: list[str]) -> None:
    path = RUNTIME_DATA_DIR / ".env"
    temp_path = path.with_suffix(".env.tmp")
    temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def configure_host() -> None:
    secret = secrets.token_urlsafe(36)
    _write_env([
        "APP_NAME=服务管理系统",
        "DATABASE_URL=sqlite:///./repair_management.db",
        "SERVER_HOST=0.0.0.0",
        "SERVER_PORT=8000",
        "ALLOW_LAN=true",
        "SESSION_COOKIE_SECURE=false",
        "TRUSTED_HOSTS=*",
        "SYNC_ROLE=host",
        f"SYNC_NODE_NAME={socket.gethostname()}-主机",
        "SYNC_NODE_ID=",
        "SYNC_HOST_URL=",
        f"SYNC_SHARED_SECRET={secret}",
        "SYNC_INTERVAL_SECONDS=300",
        "EMAIL_MODE=mock",
    ])
    (BASE_DIR / "同步密钥.txt").write_text(
        "请妥善保管，仅提供给本门店终端：\n" + secret + "\n",
        encoding="utf-8",
    )
    print("主机配置已创建：.env")
    print("同步密钥已保存：同步密钥.txt")


def configure_terminal() -> None:
    name = input("终端名称（例如：前台-01）：").strip()
    host = input("主机局域网 IP（例如：192.168.1.20）：").strip()
    secret = input("粘贴主机“同步密钥.txt”中的密钥：").strip()
    if not name:
        raise ValueError("终端名称不能为空")
    if not host or any(character.isspace() for character in host):
        raise ValueError("主机 IP 格式不正确")
    if len(secret) < 24:
        raise ValueError("同步密钥必须至少 24 位")
    _write_env([
        "APP_NAME=服务管理系统",
        "DATABASE_URL=sqlite:///./repair_management.db",
        "SERVER_HOST=127.0.0.1",
        "SERVER_PORT=8000",
        "ALLOW_LAN=false",
        "SESSION_COOKIE_SECURE=false",
        "TRUSTED_HOSTS=localhost,127.0.0.1",
        "SYNC_ROLE=terminal",
        f"SYNC_NODE_NAME={name}",
        "SYNC_NODE_ID=",
        f"SYNC_HOST_URL=http://{host}:8000",
        f"SYNC_SHARED_SECRET={secret}",
        "SYNC_INTERVAL_SECONDS=300",
        "EMAIL_MODE=mock",
    ])
    print("终端配置已创建：.env")


def _alembic_config() -> Config:
    ini_path = RESOURCE_DIR / "alembic.ini"
    script_path = RESOURCE_DIR / "alembic"
    if not ini_path.is_file() or not script_path.is_dir():
        raise RuntimeError("程序包缺少数据库迁移资源，请重新解压完整安装包")
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(script_path))
    config.set_main_option("prepend_sys_path", str(RESOURCE_DIR))
    return config


def _sqlite_path() -> Path | None:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    return path.resolve() if path.is_absolute() else (RUNTIME_DATA_DIR / path).resolve()


def _current_revision(database: Path) -> str | None:
    if not database.is_file() or database.stat().st_size == 0:
        return None
    with sqlite3.connect(database) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if not table:
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row else None


def _database_has_business_rows(database: Path) -> bool:
    if not database.is_file() or database.stat().st_size == 0:
        return False
    with sqlite3.connect(database) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'alembic_version'"
            )
        ]
        for table in tables:
            quoted = table.replace('"', '""')
            if connection.execute(f'SELECT 1 FROM "{quoted}" LIMIT 1').fetchone():
                return True
    return False


def _backup_before_migration(database: Path, current: str | None, head: str) -> None:
    if not database.is_file() or database.stat().st_size == 0 or current == head:
        return
    backup_dir = RUNTIME_DATA_DIR / "backups" / "pre-migration"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    record = backup_one(database, backup_dir, stamp)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "sqlite3.Connection.backup + PRAGMA integrity_check + SHA-256",
        "notes": f"Windows 免 Python 运行包自动升级：{current or 'legacy'} -> {head}",
        "databases": [record],
    }
    manifest_path = backup_dir / f"backup-manifest-{stamp}.json"
    temp_path = manifest_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, manifest_path)
    print(f"数据库升级前备份已验证：{record['backup']}")


def migrate_database() -> None:
    config = _alembic_config()
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head:
        raise RuntimeError("无法确定数据库迁移目标版本")
    database = _sqlite_path()
    current = _current_revision(database) if database else None
    fresh_database = bool(
        database
        and (not database.exists() or database.stat().st_size == 0)
    )
    interrupted_empty_database = bool(
        database
        and database.exists()
        and database.stat().st_size > 0
        and current is None
        and not _database_has_business_rows(database)
    )
    if fresh_database or interrupted_empty_database:
        from app.core.database import create_schema

        create_schema()
        command.stamp(config, head)
        current = head
        message = "已修复中断空库" if interrupted_empty_database else "已创建新数据库"
        print(f"{message}并标记版本：{head}")
    elif database and current is None:
        raise RuntimeError(
            "发现已有数据库但缺少 Alembic 版本号。为避免误判旧结构，程序不会自动覆盖；"
            "请使用旧版迁移工具处理后再启动。"
        )
    if database:
        _backup_before_migration(database, current, head)
    if not fresh_database and not interrupted_empty_database:
        command.upgrade(config, "head")
    if database and database.is_file():
        with sqlite3.connect(database) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{integrity[0] if integrity else 'unknown'}")
    print(f"数据库版本：{head}；完整性：ok")


def _open_browser_later(port: int) -> None:
    def worker() -> None:
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}/")

    threading.Thread(target=worker, daemon=True).start()


def _run_host(
    arguments: list[str],
    *,
    open_browser: bool = True,
    prebound_socket: socket.socket | None = None,
) -> int:
    from scripts.run_host import main as host_main

    if open_browser:
        _open_browser_later(settings.server_port)
    previous = sys.argv[:]
    try:
        sys.argv = [previous[0], *arguments]
        return host_main(prebound_socket=prebound_socket)
    finally:
        sys.argv = previous


def _run_sync(arguments: list[str]) -> int:
    from scripts.run_sync_node import main as sync_main

    previous = sys.argv[:]
    try:
        sys.argv = [previous[0], *arguments]
        return sync_main()
    finally:
        sys.argv = previous


def _sync_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "sync", "--watch"]
    return [sys.executable, str(Path(__file__).resolve()), "sync", "--watch"]


def _run_terminal(*, prebound_socket: socket.socket | None = None) -> int:
    log_dir = RUNTIME_DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (log_dir / "sync-agent.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            _sync_command(),
            cwd=RUNTIME_DATA_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        try:
            return _run_host(["--standalone"], prebound_socket=prebound_socket)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="服务管理系统 Windows 免 Python 启动器")
    subcommands = parser.add_subparsers(dest="command", required=True)
    host = subcommands.add_parser("host", help="启动主机或单机服务")
    mode = host.add_mutually_exclusive_group(required=True)
    mode.add_argument("--allow-lan", action="store_true")
    mode.add_argument("--standalone", action="store_true")
    host.add_argument("--host")
    host.add_argument("--port", type=int)
    host.add_argument("--no-browser", action="store_true")
    sync = subcommands.add_parser("sync", help="运行终端同步代理")
    sync.add_argument("--watch", action="store_true")
    sync.add_argument("--interval", type=int)
    subcommands.add_parser("terminal", help="启动终端本机服务并在后台同步")
    subcommands.add_parser("migrate", help="只执行数据库备份、升级和校验")
    recovery = subcommands.add_parser(
        "recover-admin", help="在本机交互式紧急恢复管理端管理员"
    )
    recovery.add_argument("--username")
    subcommands.add_parser("configure-host", help="创建主机配置和随机同步密钥")
    subcommands.add_parser("configure-terminal", help="交互创建终端配置")

    # 双击 EXE 时默认启动局域网主机。
    if len(sys.argv) <= 1:
        sys.argv = [sys.argv[0], "host", "--allow-lan"]

    return parser.parse_args()


def main() -> int:
    os.chdir(RUNTIME_DATA_DIR)
    args = parse_args()
    if args.command == "configure-host":
        configure_host()
        return 0
    if args.command == "configure-terminal":
        configure_terminal()
        return 0
    prebound_socket: socket.socket | None = None
    if args.command in {"host", "terminal"}:
        port = args.port if args.command == "host" and args.port is not None else settings.server_port
        if not 1 <= port <= 65535:
            print("\n启动未执行：端口必须在 1 到 65535 之间", file=sys.stderr)
            return 2
        if args.command == "terminal":
            bind_host, requested_lan = "127.0.0.1", False
        else:
            requested_lan = bool(
                args.allow_lan
                or (args.host and args.host not in {"127.0.0.1", "localhost", "::1", "[::1]"})
                or (not args.standalone and not args.host and settings.allow_lan)
            )
            bind_host = args.host or ("0.0.0.0" if requested_lan else "127.0.0.1")
        try:
            already_running = _preflight_host_start(bind_host, port, requested_lan)
        except StartupPortConflict as exc:
            print(f"\n启动未执行：{exc}", file=sys.stderr)
            return 2
        if already_running:
            if args.command == "host" and not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{port}/")
            return 0
        try:
            prebound_socket = _reserve_host_socket(bind_host, port)
        except StartupPortConflict as exc:
            print(f"\n启动未执行：{exc}", file=sys.stderr)
            return 2
    try:
        migrate_database()
    except Exception:
        if prebound_socket is not None:
            prebound_socket.close()
        raise
    if args.command == "migrate":
        return 0
    if args.command == "recover-admin":
        from app.services.emergency_recovery import run_interactive_admin_recovery

        return run_interactive_admin_recovery(args.username)
    if args.command == "terminal":
        return _run_terminal(prebound_socket=prebound_socket)
    if args.command == "sync":
        sync_args: list[str] = []
        if args.watch:
            sync_args.append("--watch")
        if args.interval:
            sync_args.extend(["--interval", str(args.interval)])
        return _run_sync(sync_args)
    host_args: list[str] = []
    if args.allow_lan:
        host_args.append("--allow-lan")
    elif args.standalone:
        host_args.append("--standalone")
    if args.host:
        host_args.extend(["--host", args.host])
    if args.port:
        host_args.extend(["--port", str(args.port)])
    return _run_host(
        host_args,
        open_browser=not args.no_browser,
        prebound_socket=prebound_socket,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n启动失败：{exc}", file=sys.stderr)
        if os.name == "nt" and sys.stdin.isatty():
            try:
                input("按回车键关闭窗口……")
            except EOFError:
                pass
        raise
