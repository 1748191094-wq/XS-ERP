from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.runtime_support import configure_test_runtime


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    configure_test_runtime(tmp_path)
    from app.main import app

    with TestClient(app) as test_client:
        setup = test_client.post(
            "/api/auth/setup",
            json={"brand_name": "测试商标", "username": "admin", "display_name": "测试管理员", "password": "AdminPass123"},
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
    return body["data"]


def create_order(client: TestClient, suffix: str):
    current_user_id = unwrap(client.get("/api/auth/me"))["user"]["id"]
    customer = unwrap(client.post("/api/customers", json={
        "name": f"客户{suffix}", "phone": f"1390000{int(suffix):04d}", "email": f"{suffix}@example.com",
    }))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"], "brand": "DJI", "model": "Air 3S", "serial_number": f"SN-{suffix}",
    }))
    order = unwrap(client.post("/api/orders", json={
        "customer_id": customer["id"], "device_id": device["id"], "engineer_id": current_user_id,
        "fault_description": f"测试故障 {suffix}",
    }))
    assert re.fullmatch(r"RO-\d{10}-[0-9A-Z]{4}", order["order_no"])
    assert len(order["order_no"]) == 18
    assert any(character.isdigit() for character in order["order_no"][-4:])
    assert any(character.isalpha() for character in order["order_no"][-4:])
    return customer, device, order


def test_service_groups_notes_staff_and_work_order_groups(client: TestClient):
    auth = unwrap(client.get("/api/auth/me"))
    user = auth["user"]
    assert user["employee_no"] == "ST0001"

    service_group = unwrap(client.post("/api/processing-groups", json={
        "name": "DJI 服务组", "group_type": "service", "member_ids": [user["id"]],
    }))["group"]
    customer_a, _device_a, order_a = create_order(client, "1")
    _customer_b, _device_b, order_b = create_order(client, "2")

    updated = unwrap(client.patch(f"/api/orders/{order_a['id']}/service-group", json={
        "processing_group_id": service_group["id"],
    }))
    assert updated["processing_group_id"] == service_group["id"]

    unwrap(client.put(f"/api/customers/{customer_a['id']}/notes/large", json={"content": "全员内部备注"}))
    unwrap(client.put(f"/api/customers/{customer_a['id']}/notes/small", json={
        "content": "DJI 组内备注", "service_group_id": service_group["id"],
    }))
    notes = unwrap(client.get(f"/api/customers/{customer_a['id']}/notes"))
    assert notes["large"]["content"] == "全员内部备注"
    assert notes["large"]["history"][0]["content"] == "全员内部备注"
    assert notes["small"][0]["content"] == "DJI 组内备注"

    group = unwrap(client.post("/api/work-order-groups", json={
        "name": "同批返修", "order_ids": [order_a["id"], order_b["id"]],
    }))
    by_member_no = unwrap(client.get(f"/api/work-order-groups?q={order_b['order_no']}"))
    assert [row["id"] for row in by_member_no] == [group["id"]]
    global_results = unwrap(client.get(f"/api/search?q={order_b['order_no']}"))
    assert any(row["kind"] == "work_order_group" and row["id"] == group["id"] for row in global_results)

    staff = unwrap(client.get(f"/api/staff/search?employee_no={user['employee_no']}"))
    assert staff["user"]["id"] == user["id"]
    assert order_a["id"] in {row["id"] for row in staff["orders"]}

    exported = client.get(f"/api/exports/orders.csv?q={order_a['order_no']}")
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    exported_text = exported.content.decode("utf-8-sig")
    assert "工单号,客户,电话,邮件,服务主题" in exported_text
    assert order_a["order_no"] in exported_text


def test_quote_finance_search_soft_delete_and_restore(client: TestClient):
    customer, _device, order = create_order(client, "3")
    quote = unwrap(client.post("/api/quotes", json={
        "repair_order_id": order["id"], "labor_fee": "120", "items": [],
    }))
    transaction = unwrap(client.post("/api/finance", json={
        "quote_id": quote["id"], "transaction_type": "income", "category": "维修收款",
        "amount": "120", "description": "报价收款",
    }))
    assert transaction["quote_id"] == quote["id"]
    assert transaction["repair_order_id"] == order["id"]
    assert transaction["customer_id"] == customer["id"]
    assert [row["id"] for row in unwrap(client.get(f"/api/finance?q={quote['quote_no']}"))] == [transaction["id"]]
    assert [row["id"] for row in unwrap(client.get("/api/finance?q=报价收款"))] == [transaction["id"]]

    deletion = unwrap(client.delete(f"/api/finance/{transaction['id']}"))
    assert unwrap(client.get("/api/finance")) == []
    after_delete = unwrap(client.get(f"/api/orders/{order['id']}"))["order"]
    assert after_delete["total_received"] == "0.00"
    unwrap(client.post(f"/api/trash/{deletion['deletion_id']}/restore"))
    restored = unwrap(client.get("/api/finance"))
    assert [row["id"] for row in restored] == [transaction["id"]]
    after_restore = unwrap(client.get(f"/api/orders/{order['id']}"))["order"]
    assert after_restore["total_received"] == "120.00"


def test_inventory_soft_delete_and_dhv2_surface_removed(client: TestClient):
    item = unwrap(client.post("/api/inventory/items", json={
        "sku": "DELETE-001", "name": "待删除物料", "stock_quantity": "0",
    }))
    unwrap(client.delete(f"/api/inventory/items/{item['id']}"))
    assert unwrap(client.get("/api/inventory/items")) == []
    assert client.get("/api/technical-tools/dhv2").status_code == 404
    assert client.post("/api/technical-tool-tasks", json={}).status_code == 404

    from app.core.database import engine
    from sqlalchemy import inspect

    names = set(inspect(engine).get_table_names())
    assert not names.intersection({"technical_tool_tasks", "technical_tool_events", "technical_tool_locks"})


def test_sync_host_allocates_random_reserved_repair_order_numbers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.entities import RepairOrderNumberReservation
    from app.services import numbering

    original_role = settings.sync_role
    original_secret = settings.sync_shared_secret
    secret = "numbering-sync-secret-at-least-24-characters"
    try:
        object.__setattr__(settings, "sync_shared_secret", secret)
        object.__setattr__(settings, "sync_role", "standalone")
        rejected = client.post(
            "/api/sync/numbering/repair-orders/next",
            headers={"X-Sync-Secret": secret},
            json={},
        )
        assert rejected.status_code == 409

        object.__setattr__(settings, "sync_role", "host")
        candidates = iter(("B2CF", "7KQ2"))
        monkeypatch.setattr(
            numbering, "_random_repair_order_suffix", lambda: next(candidates)
        )
        first = unwrap(client.post(
            "/api/sync/numbering/repair-orders/next",
            headers={"X-Sync-Secret": secret},
            json={},
        ))["order_no"]
        second = unwrap(client.post(
            "/api/sync/numbering/repair-orders/next",
            headers={"X-Sync-Secret": secret},
            json={},
        ))["order_no"]
        assert re.fullmatch(r"RO-\d{10}-[0-9A-Z]{4}", first)
        assert re.fullmatch(r"RO-\d{10}-[0-9A-Z]{4}", second)
        assert first.endswith("-B2CF")
        assert second.endswith("-7KQ2")
        assert first != second
        assert all(any(character.isdigit() for character in value[-4:]) for value in (first, second))
        assert all(any(character.isalpha() for character in value[-4:]) for value in (first, second))
        with SessionLocal() as db:
            reserved = set(db.scalars(
                select(RepairOrderNumberReservation.order_no).where(
                    RepairOrderNumberReservation.order_no.in_((first, second))
                )
            ))
        assert reserved == {first, second}
    finally:
        object.__setattr__(settings, "sync_role", original_role)
        object.__setattr__(settings, "sync_shared_secret", original_secret)


def test_dji_help_center_uses_requested_search_page():
    workflow = (Path(__file__).resolve().parents[1] / "app" / "static" / "workflow.js").read_text(
        encoding="utf-8"
    )
    expected = (
        "https://support.dji.com/help/search?lang=zh-CN&re=cn&"
        "trackId=9bd9a85d-07b3-460d-8f6a-f62a23819c91&limit=10&page=1&"
        "spaceId=17&keyword=&folderIdList=0&defaultOpened=,17"
    )
    assert f'href="{expected}" target="_blank" rel="noopener noreferrer"' in workflow
    assert "https://www.dji.com/cn/support" not in workflow
