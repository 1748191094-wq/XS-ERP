from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_LAUNCHER = PROJECT_ROOT / "scripts" / "windows_launcher.py"
CMD_LAUNCHERS = (
    PROJECT_ROOT / "启动-局域网模式.cmd",
    PROJECT_ROOT / "启动-单机模式.cmd",
)
RECOVERY_CMD_LAUNCHERS = (
    PROJECT_ROOT / "恢复管理员账户.cmd",
    PROJECT_ROOT / "deploy" / "local" / "免Python-恢复管理员账户.cmd",
)


def _reserve_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _isolated_launcher_environment(tmp_path: Path, port: int) -> tuple[dict[str, str], Path]:
    database = tmp_path / "launcher.db"
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "REPORT_DIR": str(tmp_path / "reports"),
            "EMAIL_SNAPSHOT_DIR": str(tmp_path / "email-snapshots"),
            "BACKUP_DIR": str(tmp_path / "backups"),
            "POINT_MAP_REFERENCE_ROOT": str(tmp_path / "point-maps"),
            "EMAIL_MODE": "mock",
            "SERVER_HOST": "127.0.0.1",
            "SERVER_PORT": str(port),
            "ALLOW_LAN": "false",
            "TRUSTED_HOSTS": "*",
            "SYNC_ROLE": "standalone",
            "ENABLE_API_DOCS": "false",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return environment, database


def _launcher_command(port: int) -> list[str]:
    return [
        sys.executable,
        str(WINDOWS_LAUNCHER),
        "host",
        "--standalone",
        "--port",
        str(port),
        "--no-browser",
    ]


def _wait_for_health(process: subprocess.Popen[str], port: int, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                "隔离启动器在健康检查前退出。\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )
        try:
            request = Request(f"http://127.0.0.1:{port}/api/health", headers={"Cache-Control": "no-store"})
            with urlopen(request, timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if (
                response.status == 200
                and payload.get("success") is True
                and payload.get("data", {}).get("status") == "ok"
                and payload.get("data", {}).get("service_id") == "service-management-erp"
            ):
                return
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"隔离启动器未在 {timeout:.0f} 秒内变为健康状态：{last_error}")


def _stop_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.terminate()
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    return process.communicate(timeout=5)


def test_free_port_starts_uvicorn_and_healthy_duplicate_is_idempotent(tmp_path: Path):
    """A second click must reuse the healthy ERP instead of attempting another bind."""

    port = _reserve_free_port()
    environment, database = _isolated_launcher_environment(tmp_path, port)
    process = subprocess.Popen(
        _launcher_command(port),
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        _wait_for_health(process, port)
        assert database.is_file()

        duplicate = subprocess.run(
            _launcher_command(port),
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        duplicate_output = f"{duplicate.stdout}\n{duplicate.stderr}"
        assert duplicate.returncode == 0, duplicate_output
        assert process.poll() is None, "第二次启动不应终止或替换已经健康运行的服务"
        assert "10048" not in duplicate_output
        assert "address already in use" not in duplicate_output.lower()
    finally:
        _stop_process(process)


class _ForeignServiceHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        # Deliberately mimic a generic health envelope, but not the Service ERP identity.
        body = json.dumps(
            {"success": True, "data": {"status": "ok"}, "error": None}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


def test_foreign_service_on_port_exits_nonzero_before_database_work(tmp_path: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ForeignServiceHandler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment, database = _isolated_launcher_environment(tmp_path, port)
    try:
        result = subprocess.run(
            _launcher_command(port),
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0, output
    assert "端口" in output and "占用" in output, output
    assert "10048" not in output
    assert "address already in use" not in output.lower()
    assert not database.exists(), "端口预检失败时不应迁移或创建数据库"


def _host_args(*, allow_lan: bool, port: int) -> Namespace:
    return Namespace(
        command="host",
        allow_lan=allow_lan,
        standalone=not allow_lan,
        host=None,
        port=port,
        no_browser=True,
    )


def test_launcher_main_short_circuits_before_migration_when_erp_is_already_running(
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import windows_launcher as launcher

    port = 43217
    calls: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(launcher, "parse_args", lambda: _host_args(allow_lan=True, port=port))
    monkeypatch.setattr(launcher.os, "chdir", lambda _path: None)

    def already_running(*args: object, **kwargs: object) -> bool:
        if kwargs:
            calls.append(
                (
                    str(kwargs["bind_host"]),
                    int(kwargs["port"]),
                    bool(kwargs["requested_lan"]),
                )
            )
        else:
            calls.append((str(args[0]), int(args[1]), bool(args[2])))
        return True

    monkeypatch.setattr(
        launcher,
        "_preflight_host_start",
        already_running,
    )
    monkeypatch.setattr(
        launcher,
        "migrate_database",
        lambda: pytest.fail("健康的既有服务不应触发迁移"),
    )
    monkeypatch.setattr(
        launcher,
        "_run_host",
        lambda *_args, **_kwargs: pytest.fail("健康的既有服务不应再次启动 uvicorn"),
    )

    assert launcher.main() == 0
    assert calls == [("0.0.0.0", port, True)]


def test_launcher_main_returns_two_before_migration_for_foreign_port_owner(
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import windows_launcher as launcher

    port = 43218
    monkeypatch.setattr(launcher, "parse_args", lambda: _host_args(allow_lan=False, port=port))
    monkeypatch.setattr(launcher.os, "chdir", lambda _path: None)

    def reject(*_args: object, **_kwargs: object) -> bool:
        raise launcher.StartupPortConflict("端口已被其他程序占用")

    monkeypatch.setattr(launcher, "_preflight_host_start", reject)
    monkeypatch.setattr(
        launcher,
        "migrate_database",
        lambda: pytest.fail("端口冲突必须先于数据库迁移返回"),
    )

    assert launcher.main() == 2


@pytest.mark.parametrize(
    "arguments",
    (
        ["host", "--no-browser"],
        ["host", "--allow-lan", "--standalone", "--no-browser"],
    ),
    ids=("mode-required", "modes-mutually-exclusive"),
)
def test_host_command_requires_one_explicit_mode(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
):
    from scripts import windows_launcher as launcher

    monkeypatch.setattr(sys, "argv", [str(WINDOWS_LAUNCHER), *arguments])
    with pytest.raises(SystemExit) as exc_info:
        launcher.parse_args()
    assert exc_info.value.code == 2


def test_prebound_socket_address_must_match_service_bind_host():
    from scripts.run_host import _prebound_socket_matches_host

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        assert _prebound_socket_matches_host(reserved, "127.0.0.1")
        assert _prebound_socket_matches_host(reserved, "localhost")
        assert not _prebound_socket_matches_host(reserved, "0.0.0.0")


def test_health_exposes_stable_launcher_identity():
    from app.api.auth import health

    payload = health()
    assert payload["success"] is True
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["service_id"] == "service-management-erp"


def test_service_identity_from_health_does_not_request_index(monkeypatch: pytest.MonkeyPatch):
    from scripts import windows_launcher as launcher

    paths: list[str] = []

    def fetch(_host: str, _port: int, path: str, **_kwargs: object) -> str:
        paths.append(path)
        assert path == "/api/health"
        return json.dumps({
            "success": True,
            "data": {"status": "ok", "service_id": "service-management-erp"},
            "error": None,
        })

    monkeypatch.setattr(launcher, "_local_http_text", fetch)
    assert launcher._is_service_platform("127.0.0.1", 8000)
    assert paths == ["/api/health"]


def test_wrong_health_identity_is_rejected_without_title_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import windows_launcher as launcher

    paths: list[str] = []

    def fetch(_host: str, _port: int, path: str, **_kwargs: object) -> str:
        paths.append(path)
        if path == "/api/health":
            return json.dumps({
                "success": True,
                "data": {"status": "ok", "service_id": "another-service"},
                "error": None,
            })
        return "<title>服务管理系统</title>"

    monkeypatch.setattr(launcher, "_local_http_text", fetch)
    assert not launcher._is_service_platform("127.0.0.1", 8000)
    assert paths == ["/api/health"]


def test_legacy_identity_retries_transient_index_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import windows_launcher as launcher

    paths: list[str] = []
    index_responses: list[str | None] = [None, "<title>服务管理系统</title>"]

    def fetch(_host: str, _port: int, path: str, **_kwargs: object) -> str | None:
        paths.append(path)
        if path == "/api/health":
            return json.dumps({
                "success": True,
                "data": {"status": "ok"},
                "error": None,
            })
        return index_responses.pop(0)

    delays: list[float] = []
    monkeypatch.setattr(launcher, "_local_http_text", fetch)
    monkeypatch.setattr(launcher.time, "sleep", delays.append)
    assert launcher._is_service_platform("127.0.0.1", 8000)
    assert paths == ["/api/health", "/", "/"]
    assert delays == [0.1]


def test_reserve_host_socket_preserves_non_conflict_bind_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import windows_launcher as launcher

    class FailingSocket:
        def setsockopt(self, *_args: object) -> None:
            return None

        def bind(self, _address: object) -> None:
            raise OSError(10049, "requested address is not valid")

        def close(self) -> None:
            return None

    monkeypatch.setattr(launcher.socket, "socket", lambda *_args: FailingSocket())
    with pytest.raises(OSError) as exc_info:
        launcher._reserve_host_socket("192.0.2.10", 8000)
    assert exc_info.value.errno == 10049
    assert not isinstance(exc_info.value, launcher.StartupPortConflict)


def test_reserve_host_socket_classifies_only_address_in_use(
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import windows_launcher as launcher

    class OccupiedSocket:
        def setsockopt(self, *_args: object) -> None:
            return None

        def bind(self, _address: object) -> None:
            raise OSError(10048, "address already in use")

        def close(self) -> None:
            return None

    monkeypatch.setattr(launcher.socket, "socket", lambda *_args: OccupiedSocket())
    with pytest.raises(launcher.StartupPortConflict, match="端口 8000.*占用"):
        launcher._reserve_host_socket("127.0.0.1", 8000)


@pytest.mark.parametrize("launcher", CMD_LAUNCHERS, ids=lambda path: path.stem)
def test_cmd_launcher_is_crlf_no_bom_and_success_path_does_not_pause(launcher: Path):
    raw = launcher.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "CMD 必须为 UTF-8 无 BOM"
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b""), "CMD 不能混入裸 LF"
    assert b"\r" not in raw.replace(b"\r\n", b""), "CMD 不能混入裸 CR"

    lines = raw.decode("utf-8").split("\r\n")
    launch_indexes = [
        index
        for index, line in enumerate(lines)
        if "windows_launcher.py host" in line
    ]
    assert len(launch_indexes) == 4
    for index in launch_indexes:
        following = [line.strip().lower() for line in lines[index + 1 : index + 3] if line.strip()]
        assert following[0] == "goto finish"

    finish_index = lines.index(":finish")
    success_tail = [line.strip().lower() for line in lines[finish_index + 1 :] if line.strip()]
    assert success_tail[0] == 'set "service_exit_code=%errorlevel%"'
    assert success_tail[-1] == "exit /b %service_exit_code%"
    pause_index = success_tail.index("pause")
    zero_exit_indexes = [
        index
        for index, line in enumerate(success_tail)
        if "exit /b 0" in line and line.startswith("if ")
    ]
    assert zero_exit_indexes and zero_exit_indexes[0] < pause_index, (
        "成功返回码必须在 pause 前直接退出；pause 只能留给失败路径"
    )


@pytest.mark.parametrize("launcher", RECOVERY_CMD_LAUNCHERS, ids=lambda path: path.stem)
def test_recovery_cmd_is_crlf_no_bom_and_invokes_local_recovery(launcher: Path):
    raw = launcher.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "CMD 必须为 UTF-8 无 BOM"
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b""), "CMD 不能混入裸 LF"
    assert b"\r" not in raw.replace(b"\r\n", b""), "CMD 不能混入裸 CR"

    text = raw.decode("utf-8")
    assert "recover-admin" in text
    assert "pause" in text.lower()
    assert "exit /b %SERVICE_EXIT_CODE%" in text
