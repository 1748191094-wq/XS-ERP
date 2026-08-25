from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import os
import socket
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_RUNTIME_MODULES = {
    "fastapi": "FastAPI",
    "uvicorn": "Uvicorn",
    "sqlalchemy": "SQLAlchemy",
    "reportlab": "ReportLab",
    "pypdfium2": "pypdfium2",
    "numpy": "NumPy",
    "PIL": "Pillow",
}


def ensure_runtime_dependencies() -> None:
    missing = [label for module, label in REQUIRED_RUNTIME_MODULES.items() if importlib.util.find_spec(module) is None]
    if not missing:
        return
    python = Path(sys.executable)
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    names = "、".join(missing)
    raise SystemExit(
        f"启动失败：当前 Python 缺少运行依赖：{names}\n"
        f"当前解释器：{python}\n"
        "请在本目录运行下面的命令完成修复：\n"
        f'"{python}" -m pip install -r "{requirements}"'
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 服务平台管理主机服务")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--allow-lan", action="store_true", help="监听局域网地址，允许成员端连接")
    mode.add_argument("--standalone", action="store_true", help="仅监听本机，进入管理员单机脱机模式")
    parser.add_argument("--host", help="显式监听地址；通常不需要填写")
    parser.add_argument("--port", type=int, help="服务端口，默认读取 SERVER_PORT")
    return parser.parse_args()


def _canonical_ip(value: str) -> str:
    candidate = value.strip().strip("[]")
    address = ipaddress.ip_address(candidate.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return address.compressed


def _prebound_socket_matches_host(prebound_socket: socket.socket, expected_host: str) -> bool:
    reserved_host = _canonical_ip(str(prebound_socket.getsockname()[0]))
    candidate = expected_host.strip().strip("[]")
    try:
        expected_addresses = {
            _canonical_ip(str(info[4][0]))
            for info in socket.getaddrinfo(
                candidate,
                None,
                family=prebound_socket.family,
                type=socket.SOCK_STREAM,
            )
        }
    except OSError:
        return False
    return reserved_host in expected_addresses


def main(*, prebound_socket: socket.socket | None = None) -> int:
    ensure_runtime_dependencies()
    args = parse_args()
    from app.core.config import settings
    from app.core.database import SessionLocal, create_schema
    from app.core.migrations import assert_database_at_head
    from app.services.network import load_network_config, local_ipv4_addresses, save_network_config

    if settings.enforce_database_revision:
        assert_database_at_head()
    else:
        create_schema()
    with SessionLocal() as db:
        persisted = load_network_config(db)
        allow_lan = True if args.allow_lan else False if args.standalone else persisted.allow_lan
        if args.allow_lan or args.standalone:
            save_network_config(db, allow_lan)

    host = args.host or ("0.0.0.0" if allow_lan else "127.0.0.1")
    port = args.port if args.port is not None else settings.server_port
    if not 1 <= port <= 65535:
        raise SystemExit("端口必须在 1 到 65535 之间")
    os.environ["SERVICE_RUNTIME_BIND_HOST"] = host

    print(f"主机服务模式：{'局域网协作' if allow_lan else '管理员单机脱机'}")
    print(f"本机访问：http://127.0.0.1:{port}/")
    if allow_lan:
        for address in local_ipv4_addresses():
            print(f"成员端访问：http://{address}:{port}/")

    import uvicorn
    if prebound_socket is None:
        uvicorn.run("app.main:app", host=host, port=port, reload=False)
    else:
        reserved_host, reserved_port = prebound_socket.getsockname()[:2]
        if int(reserved_port) != port:
            prebound_socket.close()
            raise RuntimeError("启动器预留端口与服务配置不一致")
        if not _prebound_socket_matches_host(prebound_socket, host):
            prebound_socket.close()
            raise RuntimeError(
                f"启动器预留监听地址 {reserved_host} 与服务配置 {host} 不一致"
            )
        config = uvicorn.Config("app.main:app", host=host, port=port, reload=False)
        server = uvicorn.Server(config)
        try:
            server.run(sockets=[prebound_socket])
        finally:
            prebound_socket.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
