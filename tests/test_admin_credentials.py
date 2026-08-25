from __future__ import annotations

import re
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.core.security import MANAGEMENT_PASSWORD_PATTERN, generate_management_password
from tests.runtime_support import configure_test_runtime


@pytest.fixture()
def admin_client(tmp_path):
    configure_test_runtime(tmp_path)
    from app.main import app

    with TestClient(app) as client:
        setup = client.post(
            "/api/auth/setup",
            json={
                "brand_name": "测试商标",
                "username": "admin",
                "display_name": "安全管理员",
                "password": "this-user-supplied-value-must-be-ignored",
            },
        )
        assert setup.status_code == 201, setup.text
        data = setup.json()["data"]
        assert MANAGEMENT_PASSWORD_PATTERN.fullmatch(data["generated_password"])
        client.admin_password = data["generated_password"]
        client.headers["X-CSRF-Token"] = data["csrf_token"]
        yield client


def unwrap(response):
    assert response.status_code < 400, response.text
    body = response.json()
    assert body["success"] is True
    return body["data"]


def test_initial_setup_requires_and_persists_brand_name(tmp_path):
    configure_test_runtime(tmp_path, database_name="branding.db")
    from app.main import app

    with TestClient(app) as client:
        initial = unwrap(client.get("/api/auth/status"))
        assert initial == {
            "initialized": False,
            "brand_name": "服务品牌",
            "configured": False,
        }

        missing = client.post(
            "/api/auth/setup",
            json={"username": "admin", "display_name": "管理员"},
        )
        assert missing.status_code == 422

        setup = unwrap(client.post(
            "/api/auth/setup",
            json={
                "brand_name": "  示例 商标  ",
                "username": "admin",
                "display_name": "管理员",
            },
        ))
        assert setup["brand_name"] == "示例 商标"

        public = unwrap(client.get("/api/branding"))
        assert public == {"brand_name": "示例 商标", "configured": True}
        status = unwrap(client.get("/api/auth/status"))
        assert status["initialized"] is True
        assert status["brand_name"] == "示例 商标"

        repeated = client.post(
            "/api/auth/setup",
            json={
                "brand_name": "另一个商标",
                "username": "other-admin",
                "display_name": "其他管理员",
            },
        )
        assert repeated.status_code == 409


def test_generated_management_password_has_four_human_groups():
    values = {generate_management_password() for _ in range(64)}
    assert len(values) == 64
    assert all(MANAGEMENT_PASSWORD_PATTERN.fullmatch(value) for value in values)
    assert all(len(value.replace("-", "")) == 16 for value in values)


def test_setup_and_user_creation_ignore_caller_password_and_show_generated_once(admin_client):
    wrong_setup_login = admin_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "this-user-supplied-value-must-be-ignored"},
    )
    assert wrong_setup_login.status_code == 401
    relogin = unwrap(admin_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": admin_client.admin_password},
    ))
    assert relogin["user"]["username"] == "admin"
    admin_client.headers["X-CSRF-Token"] = relogin["csrf_token"]

    created = unwrap(admin_client.post(
        "/api/users",
        json={
            "username": "generated-worker",
            "display_name": "生成密码员工",
            "role": "engineer",
            "password": "WorkerSelected123",
        },
    ))
    assert MANAGEMENT_PASSWORD_PATTERN.fullmatch(created["generated_password"])
    assert created["password_shown_once"] is True
    assert admin_client.post(
        "/api/auth/login",
        json={"username": created["username"], "password": "WorkerSelected123"},
    ).status_code == 401
    assert admin_client.post(
        "/api/auth/login",
        json={"username": created["username"], "password": created["generated_password"]},
    ).status_code == 200


def test_only_admin_can_reset_password_and_reset_revokes_sessions(admin_client):
    worker = unwrap(admin_client.post(
        "/api/users",
        json={"username": "reset-worker", "display_name": "重置员工", "role": "engineer"},
    ))
    with TestClient(admin_client.app) as worker_client:
        login = unwrap(worker_client.post(
            "/api/auth/login",
            json={"username": worker["username"], "password": worker["generated_password"]},
        ))
        worker_client.headers["X-CSRF-Token"] = login["csrf_token"]
        denied = worker_client.post(f"/api/users/{worker['id']}/password/reset")
        assert denied.status_code == 403

        reset = unwrap(admin_client.post(f"/api/users/{worker['id']}/password/reset"))
        assert MANAGEMENT_PASSWORD_PATTERN.fullmatch(reset["generated_password"])
        assert reset["generated_password"] != worker["generated_password"]
        assert reset["sessions_revoked"] >= 1
        assert worker_client.get("/api/auth/me").status_code == 401
        assert worker_client.post(
            "/api/auth/login",
            json={"username": worker["username"], "password": worker["generated_password"]},
        ).status_code == 401
        assert worker_client.post(
            "/api/auth/login",
            json={"username": worker["username"], "password": reset["generated_password"]},
        ).status_code == 200

    disabled = admin_client.post(
        "/api/auth/change-password",
        json={"current_password": admin_client.admin_password, "new_password": "SelfChosen123456"},
    )
    assert disabled.status_code == 410
    assert disabled.json()["error"]["code"] == "self_password_change_disabled"


def test_management_login_is_temporarily_locked_after_repeated_failures(admin_client):
    statuses = []
    for _ in range(5):
        response = admin_client.post(
            "/api/auth/login", json={"username": "missing-admin", "password": "wrong"}
        )
        statuses.append(response.status_code)
    assert statuses == [401, 401, 401, 401, 429]
    locked = admin_client.post(
        "/api/auth/login", json={"username": "missing-admin", "password": "wrong"}
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "admin_login_locked"


def test_security_headers_and_attachment_signature_enforcement(admin_client):
    root = admin_client.get("/")
    assert root.status_code == 200
    assert root.headers["x-content-type-options"] == "nosniff"
    assert root.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in root.headers["content-security-policy"]

    spoofed = admin_client.post(
        "/api/attachments",
        files={"file": ("not-an-image.png", b"<script>alert(1)</script>", "image/png")},
    )
    assert spoofed.status_code == 415
    assert spoofed.json()["error"]["code"] == "file_signature_mismatch"

    image = BytesIO()
    Image.new("RGB", (4, 4), "#3366ff").save(image, format="PNG")
    stored = unwrap(admin_client.post(
        "/api/attachments",
        files={"file": ("evidence.png", image.getvalue(), "application/octet-stream")},
    ))
    assert stored["content_type"] == "image/png"
    downloaded = admin_client.get(f"/api/files/attachment/{stored['id']}")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("image/png")
    assert downloaded.headers["x-content-type-options"] == "nosniff"


def test_local_emergency_recovery_revokes_sessions_and_is_audited(admin_client):
    from app.core.database import SessionLocal
    from app.models.entities import AuditLog
    from app.services.admin_credentials import recover_admin_account

    with SessionLocal() as db:
        user, revoked = recover_admin_account(
            db,
            username="admin",
            new_password="LocalRecovery2026-Strong",
        )
        assert user.enabled is True
        assert revoked >= 1
        audit = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "auth.emergency_recovery")
            .order_by(AuditLog.id.desc())
        )
        assert audit is not None
        assert audit.details_json["password_logged"] is False

    assert admin_client.get("/api/auth/me").status_code == 401
    recovered = admin_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "LocalRecovery2026-Strong"},
    )
    assert recovered.status_code == 200


def test_static_admin_ui_has_no_user_supplied_management_password_fields():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    workflow_js = (root / "app" / "static" / "workflow.js").read_text(encoding="utf-8")
    index = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "generated_password" in app_js
    assert "resetUserPassword" in workflow_js
    assert "初始密码（至少 5 位" not in workflow_js
    assert "重置密码（留空不修改）" not in workflow_js
    assert "由管理员在账号管理中重置" in index
