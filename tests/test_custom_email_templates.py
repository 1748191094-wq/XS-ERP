from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.runtime_support import configure_test_runtime

GENERATED_PASSWORDS: dict[str, str] = {}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    configure_test_runtime(tmp_path)
    from app.main import app

    with TestClient(app) as test_client:
        setup = test_client.post(
            "/api/auth/setup",
            json={"brand_name": "测试商标", "username": "admin", "display_name": "模板管理员", "password": "AdminPass123"},
        )
        assert setup.status_code == 201, setup.text
        setup_data = setup.json()["data"]
        test_client.admin_password = setup_data["generated_password"]
        test_client.headers.update({"X-CSRF-Token": setup_data["csrf_token"]})
        yield test_client


def unwrap(response):
    assert response.status_code < 400, response.text
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    if isinstance(data, dict) and data.get("username") and data.get("generated_password"):
        GENERATED_PASSWORDS[data["username"]] = data["generated_password"]
    return data


def create_order_context(client: TestClient, *, engineer_id: int | None = None):
    customer = unwrap(client.post("/api/customers", json={
        "name": "模板客户", "phone": "13900000991", "email": "template@example.com",
    }))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"], "brand": "DJI", "model": "Air 3S", "serial_number": "EMAIL-TPL-001",
    }))
    order_payload = {
        "customer_id": customer["id"],
        "device_id": device["id"],
        "fault_description": "测试模板",
    }
    if engineer_id is not None:
        order_payload["engineer_id"] = engineer_id
    order = unwrap(client.post("/api/orders", json=order_payload))
    return customer, device, order


def test_custom_template_crud_safe_render_send_and_soft_delete(client: TestClient):
    customer, _device, order = create_order_context(client)
    metadata = unwrap(client.get("/api/email/template-metadata"))
    assert [item["key"] for item in metadata["categories"]] == [
        "quotation", "repair_progress", "customer_service", "general",
    ]
    assert {item["key"] for item in metadata["allowed_placeholders"]} >= {
        "customer", "device", "order_no", "brand",
    }

    created = unwrap(client.post("/api/email/templates", json={
        "name": "到店提醒",
        "category": "customer_service",
        "subject": "{brand}提醒：工单 {order_no}",
        "body": "尊敬的{customer}：\n设备 {device} 已登记。字面花括号：{{已记录}}。",
    }))
    template_type = created["template_type"]
    assert template_type.startswith("custom_")
    assert created["is_system"] is False
    assert created["category_label"] == "客户服务"
    assert created["category_order"] == 30
    assert created["enabled"] is True
    assert created["template_valid"] is True
    assert created["used_placeholders"] == ["brand", "customer", "device", "order_no"]

    templates = unwrap(client.get("/api/email/templates"))
    assert [item["category_order"] for item in templates] == sorted(
        item["category_order"] for item in templates
    )
    assert template_type in {item["template_type"] for item in templates}
    assert next(item for item in templates if item["template_type"] == "quote")["is_system"] is True

    preview = unwrap(client.post("/api/email/preview", json={
        "template_type": template_type,
        "repair_order_id": order["id"],
    }))
    assert preview["template_category"] == "customer_service"
    assert preview["is_system_template"] is False
    assert order["order_no"] in preview["subject"]
    assert customer["name"] in preview["body"]
    assert "{已记录}" in preview["body"]

    queued = unwrap(client.post("/api/outbound-emails", json={
        "template_type": template_type,
        "repair_order_id": order["id"],
        "auto_attach_report": False,
    }))
    assert queued["mode"] == "mock"
    delivery = next(item for item in unwrap(client.get("/api/outbound-emails")) if item["id"] == queued["email_id"])
    assert delivery["template_type"] == template_type
    assert delivery["subject_snapshot"] == preview["subject"]
    assert delivery["body_snapshot"] == preview["body"]

    unsafe_update = client.patch(f"/api/email/templates/{template_type}", json={
        "subject": "Unsafe {customer.__class__}",
    })
    assert unsafe_update.status_code == 422
    assert unsafe_update.json()["error"]["code"] == "email_template_content_invalid"
    unchanged_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": template_type,
        "repair_order_id": order["id"],
    }))
    assert unchanged_preview["subject"] == preview["subject"]

    disabled = unwrap(client.patch(f"/api/email/templates/{template_type}", json={"enabled": False}))
    assert disabled["enabled"] is False
    assert template_type not in {item["template_type"] for item in unwrap(client.get("/api/email/templates"))}
    assert template_type in {
        item["template_type"] for item in unwrap(client.get("/api/email/templates?include_disabled=true"))
    }
    disabled_preview = client.post("/api/email/preview", json={
        "template_type": template_type, "repair_order_id": order["id"],
    })
    assert disabled_preview.status_code == 409
    assert disabled_preview.json()["error"]["code"] == "email_template_disabled"
    disabled_send = client.post("/api/outbound-emails", json={
        "template_type": template_type,
        "repair_order_id": order["id"],
        "auto_attach_report": False,
    })
    assert disabled_send.status_code == 409
    assert disabled_send.json()["error"]["code"] == "email_template_disabled"

    unwrap(client.patch(f"/api/email/templates/{template_type}", json={"enabled": True, "name": "到店取件提醒"}))
    updated = unwrap(client.patch(f"/api/email/templates/{template_type}", json={
        "subject": "UPDATED {order_no}",
        "body": "UPDATED {customer}",
    }))
    assert updated["subject"] == "UPDATED {order_no}"
    updated_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": template_type,
        "repair_order_id": order["id"],
    }))
    assert updated_preview["subject"] == f"UPDATED {order['order_no']}"
    original_delivery = next(
        item for item in unwrap(client.get("/api/outbound-emails"))
        if item["id"] == queued["email_id"]
    )
    assert original_delivery["subject_snapshot"] == preview["subject"]
    assert original_delivery["body_snapshot"] == preview["body"]

    deleted = unwrap(client.delete(f"/api/email/templates/{template_type}"))
    assert deleted["deleted"] is True
    assert template_type not in {item["template_type"] for item in unwrap(client.get("/api/email/templates"))}
    deleted_rows = unwrap(client.get("/api/email/templates?include_disabled=true&include_deleted=true"))
    assert next(item for item in deleted_rows if item["template_type"] == template_type)["deleted"] is True
    deleted_preview = client.post("/api/email/preview", json={
        "template_type": template_type, "repair_order_id": order["id"],
    })
    assert deleted_preview.status_code == 400
    assert deleted_preview.json()["error"]["code"] == "email_template_invalid"
    deleted_send = client.post("/api/outbound-emails", json={
        "template_type": template_type,
        "repair_order_id": order["id"],
        "auto_attach_report": False,
    })
    assert deleted_send.status_code == 400
    assert deleted_send.json()["error"]["code"] == "email_template_invalid"
    retained_delivery = next(
        item for item in unwrap(client.get("/api/outbound-emails"))
        if item["id"] == queued["email_id"]
    )
    assert retained_delivery["subject_snapshot"] == preview["subject"]
    assert retained_delivery["body_snapshot"] == preview["body"]

    restored = unwrap(client.post(f"/api/email/templates/{template_type}/restore"))
    assert restored["deleted"] is False
    assert restored["enabled"] is True
    assert unwrap(client.post("/api/email/preview", json={
        "template_type": template_type, "repair_order_id": order["id"],
    }))["template_name"] == "到店取件提醒"
    restored_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": template_type,
        "repair_order_id": order["id"],
    }))
    assert restored_preview["subject"] == f"UPDATED {order['order_no']}"

    audit_rows = unwrap(client.get("/api/audit-logs?limit=200"))
    paths = {row["path"] for row in audit_rows}
    assert "/api/email/templates" in paths
    assert f"/api/email/templates/{template_type}" in paths
    assert f"/api/email/templates/{template_type}/restore" in paths


def test_custom_template_rejects_unsafe_content(client: TestClient):
    unsafe_cases = [
        ("{customer.__class__}", "安全正文"),
        ("{customer[0]}", "安全正文"),
        ("{customer!r}", "安全正文"),
        ("{amount:.2f}", "安全正文"),
        ("{unknown}", "安全正文"),
        ("未闭合 {customer", "安全正文"),
        ("通知\r\nBcc: injected@example.com", "安全正文"),
        ("包含空字符\x00的主题", "安全正文"),
        ("安全主题", "包含空字符\x00的正文"),
    ]
    for subject, body in unsafe_cases:
        response = client.post("/api/email/templates", json={
            "name": "非法模板", "category": "general", "subject": subject, "body": body,
        })
        assert response.status_code == 422, (subject, body, response.text)
        assert response.json()["error"]["code"] == "email_template_content_invalid"


def test_system_templates_are_immutable_and_writes_are_admin_only(client: TestClient):
    patch_system = client.patch("/api/email/templates/quote", json={"name": "覆盖系统模板"})
    assert patch_system.status_code == 409
    assert patch_system.json()["error"]["code"] == "system_email_template_immutable"
    delete_system = client.delete("/api/email/templates/quote")
    assert delete_system.status_code == 409
    assert delete_system.json()["error"]["code"] == "system_email_template_immutable"
    restore_system = client.post("/api/email/templates/quote/restore")
    assert restore_system.status_code == 409
    assert restore_system.json()["error"]["code"] == "system_email_template_immutable"

    custom = unwrap(client.post("/api/email/templates", json={
        "name": "权限测试模板",
        "category": "general",
        "subject": "通知 {order_no}",
        "body": "您好 {customer}",
    }))

    unwrap(client.post("/api/users", json={
        "username": "manager-template", "display_name": "模板经理", "role": "manager", "password": "ManagerPass123",
    }))
    owner = unwrap(client.post("/api/users", json={
        "username": "owner-template",
        "display_name": "模板工单负责人",
        "role": "engineer",
        "password": "OwnerPass123",
    }))
    _customer, _device, assigned_order = create_order_context(client, engineer_id=owner["id"])
    unwrap(client.post("/api/auth/logout"))
    login = unwrap(client.post("/api/auth/login", json={
        "username": "manager-template", "password": GENERATED_PASSWORDS["manager-template"],
    }))
    client.headers.update({"X-CSRF-Token": login["csrf_token"]})
    denied_create = client.post("/api/email/templates", json={
        "name": "越权模板", "category": "general", "subject": "主题", "body": "正文",
    })
    assert denied_create.status_code == 403
    assert denied_create.json()["error"]["code"] == "permission_denied"
    for method, path, payload in (
        ("PATCH", f"/api/email/templates/{custom['template_type']}", {"enabled": False}),
        ("DELETE", f"/api/email/templates/{custom['template_type']}", None),
        ("POST", f"/api/email/templates/{custom['template_type']}/restore", None),
    ):
        denied_mutation = client.request(method, path, json=payload)
        assert denied_mutation.status_code == 403
        assert denied_mutation.json()["error"]["code"] == "permission_denied"
    denied_hidden = client.get("/api/email/templates?include_disabled=true")
    assert denied_hidden.status_code == 403
    assert denied_hidden.json()["error"]["code"] == "permission_denied"
    assert custom["template_type"] in {
        item["template_type"] for item in unwrap(client.get("/api/email/templates"))
    }

    denied_preview = client.post("/api/email/preview", json={
        "template_type": custom["template_type"],
        "repair_order_id": assigned_order["id"],
    })
    assert denied_preview.status_code == 403
    assert denied_preview.json()["error"]["code"] == "order_access_denied"
    denied_send = client.post("/api/outbound-emails", json={
        "template_type": custom["template_type"],
        "repair_order_id": assigned_order["id"],
        "auto_attach_report": False,
    })
    assert denied_send.status_code == 403
    assert denied_send.json()["error"]["code"] == "order_access_denied"

    unwrap(client.post("/api/auth/logout"))
    owner_login = unwrap(client.post("/api/auth/login", json={
        "username": "owner-template",
        "password": GENERATED_PASSWORDS["owner-template"],
    }))
    client.headers.update({"X-CSRF-Token": owner_login["csrf_token"]})
    owner_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": custom["template_type"],
        "repair_order_id": assigned_order["id"],
    }))
    assert assigned_order["order_no"] in owner_preview["subject"]
    owner_send = unwrap(client.post("/api/outbound-emails", json={
        "template_type": custom["template_type"],
        "repair_order_id": assigned_order["id"],
        "auto_attach_report": False,
    }))
    assert owner_send["status"] == "queued"
