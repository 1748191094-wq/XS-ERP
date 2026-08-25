from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.runtime_support import configure_test_runtime

GENERATED_PASSWORDS: dict[str, str] = {}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("account-device-call")
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
    data = body["data"]
    if isinstance(data, dict) and data.get("username") and data.get("generated_password"):
        GENERATED_PASSWORDS[data["username"]] = data["generated_password"]
    return data


def create_user(client: TestClient, username: str, role: str = "engineer", *, employee_no: str | None = None):
    payload = {
        "username": username,
        "display_name": username,
        "role": role,
        "password": "WorkerPass123",
    }
    if employee_no:
        payload["employee_no"] = employee_no
    return unwrap(client.post("/api/users", json=payload))


def create_order_bundle(
    client: TestClient,
    suffix: str,
    *,
    engineer_id: int | None = None,
    customer_name: str | None = None,
    serial_number: str | None = None,
    fault_description: str | None = None,
):
    customer = unwrap(client.post("/api/customers", json={
        "name": customer_name or f"客户-{suffix}",
        "phone": f"139{int(suffix):08d}",
        "email": f"customer-{suffix}@example.com",
    }))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"],
        "brand": "DJI",
        "model": f"Air {suffix}",
        "serial_number": serial_number or f"SN-{suffix}",
    }))
    payload = {
        "customer_id": customer["id"],
        "device_id": device["id"],
        "fault_description": fault_description or f"故障-{suffix}",
    }
    if engineer_id is not None:
        payload["engineer_id"] = engineer_id
    order = unwrap(client.post("/api/orders", json=payload))
    return customer, device, order


def test_employee_number_edit_and_safe_account_deletion(client: TestClient):
    worker = create_user(client, "retired-worker", employee_no="OLD-100")
    updated = unwrap(client.patch(f"/api/users/{worker['id']}", json={"employee_no": "new-200"}))
    assert updated["employee_no"] == "NEW-200"
    assert unwrap(client.get("/api/staff/search?employee_no=NEW-200"))["user"]["id"] == worker["id"]
    assert unwrap(client.get("/api/staff/search?employee_no=OLD-100")) is None

    _customer, _device, order = create_order_bundle(client, "1001", engineer_id=worker["id"])
    current_admin_id = unwrap(client.get("/api/auth/me"))["user"]["id"]
    cannot_self_delete = client.delete(f"/api/users/{current_admin_id}")
    assert cannot_self_delete.status_code == 409
    assert cannot_self_delete.json()["error"]["code"] == "cannot_delete_current_user"

    with TestClient(client.app) as worker_client:
        login = unwrap(worker_client.post(
            "/api/auth/login", json={"username": "retired-worker", "password": GENERATED_PASSWORDS["retired-worker"]}
        ))
        worker_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        assert worker_client.get("/api/auth/me").status_code == 200

        deleted = unwrap(client.delete(f"/api/users/{worker['id']}"))
        assert deleted["deleted"] is True
        assert deleted["user"]["enabled"] is False
        assert worker_client.get("/api/auth/me").status_code == 401

    users = unwrap(client.get("/api/users"))
    retired = next(row for row in users if row["id"] == worker["id"])
    assert retired["employee_no"] == "NEW-200"
    assert retired["enabled"] is False
    assert unwrap(client.get(f"/api/orders/{order['id']}"))["order"]["engineer_id"] == worker["id"]


def test_device_soft_delete_preserves_historical_sn_and_sync_tombstone(client: TestClient):
    customer, device, order = create_order_bundle(
        client,
        "2002",
        serial_number="DELETE-SN-2002",
        fault_description="云台需检测",
    )
    assert order["device_serial_number"] == "DELETE-SN-2002"

    deletion = unwrap(client.delete(f"/api/devices/{device['id']}"))
    assert all(row["id"] != device["id"] for row in unwrap(client.get("/api/devices")))
    assert all(row["id"] != device["id"] for row in unwrap(client.get(f"/api/customers/{customer['id']}"))["devices"])
    assert not any(
        row["kind"] == "device" and row["id"] == device["id"]
        for row in unwrap(client.get("/api/search?q=DELETE-SN-2002"))
    )

    matched_orders = unwrap(client.get("/api/orders?q=DELETE-SN-2002"))
    assert [row["id"] for row in matched_orders] == [order["id"]]
    assert matched_orders[0]["device_serial_number"] == "DELETE-SN-2002"
    detail = unwrap(client.get(f"/api/orders/{order['id']}"))
    assert detail["device"]["serial_number"] == "DELETE-SN-2002"
    assert detail["order"]["device_serial_number"] == "DELETE-SN-2002"

    cannot_reuse = client.post("/api/orders", json={
        "customer_id": customer["id"],
        "device_id": device["id"],
        "fault_description": "不应关联已删除设备",
    })
    assert cannot_reuse.status_code == 404
    assert cannot_reuse.json()["error"]["code"] == "device_not_found"
    cannot_link_ticket = client.post("/api/service-tickets", json={
        "ticket_type": "consultation",
        "title": "不应关联已删除设备",
        "description": "tombstone",
        "customer_id": customer["id"],
        "device_id": device["id"],
    })
    assert cannot_link_ticket.status_code == 404
    assert cannot_link_ticket.json()["error"]["code"] == "device_not_found"

    from app.core.database import SessionLocal
    from app.models.entities import DroneDevice
    from app.services.sync import apply_payload, scan_supported_records

    with SessionLocal() as db:
        sync_payload = next(
            payload
            for entity_type, _key, payload in scan_supported_records(db)
            if entity_type == "device" and payload["serial_number"] == "DELETE-SN-2002"
        )
        assert sync_payload["deleted_at"] is not None
        assert sync_payload["deletion_batch_id"]
        row = db.get(DroneDevice, device["id"])
        row.deleted_at = None
        row.deletion_batch_id = None
        db.commit()
        apply_payload(db, "device", sync_payload)
        db.commit()
        assert db.get(DroneDevice, device["id"]).deleted_at is not None

    unwrap(client.post(f"/api/trash/{deletion['id']}/restore"))
    restored = unwrap(client.get("/api/devices"))
    assert any(row["id"] == device["id"] for row in restored)


def test_call_operator_can_read_all_work_orders_but_cannot_mutate(client: TestClient):
    owner_a = create_user(client, "call-owner-a")
    owner_b = create_user(client, "call-owner-b")
    customer_a, _da, order_a = create_order_bundle(client, "3003", engineer_id=owner_a["id"])
    customer_b, _db, order_b = create_order_bundle(client, "3004", engineer_id=owner_b["id"])
    call_operator = create_user(client, "call-desk", role="call_operator")
    own_call = unwrap(client.post("/api/outbound-calls", json={
        "customer_id": customer_a["id"],
        "repair_order_id": order_a["id"],
        "assigned_to": call_operator["id"],
        "contact_number": customer_a["phone"],
        "purpose": "话务专员本人结果登记",
    }))
    other_call = unwrap(client.post("/api/outbound-calls", json={
        "customer_id": customer_b["id"],
        "repair_order_id": order_b["id"],
        "assigned_to": owner_b["id"],
        "contact_number": customer_b["phone"],
        "purpose": "其他负责人外呼任务",
    }))

    with TestClient(client.app) as call_client:
        login = unwrap(call_client.post(
            "/api/auth/login", json={"username": "call-desk", "password": GENERATED_PASSWORDS["call-desk"]}
        ))
        call_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        orders = unwrap(call_client.get("/api/orders"))
        order_ids = {row["id"] for row in orders}
        assert {order_a["id"], order_b["id"]}.issubset(order_ids)
        assert all("total_cost" not in row and "gross_profit" not in row for row in orders)
        tickets = unwrap(call_client.get("/api/service-tickets"))
        linked_order_ids = {row["repair_order_id"] for row in tickets}
        assert {order_a["id"], order_b["id"]}.issubset(linked_order_ids)
        assert call_client.get(f"/api/orders/{order_a['id']}").status_code == 200
        assert call_client.get(f"/api/service-tickets/{tickets[0]['id']}").status_code == 200

        visible_calls = unwrap(call_client.get("/api/outbound-calls?status=planned"))
        assert {own_call["id"], other_call["id"]}.issubset({row["id"] for row in visible_calls})
        completed = unwrap(call_client.post(f"/api/outbound-calls/{own_call['id']}/complete", json={
            "result": "connected",
            "duration_seconds": 42,
            "summary": "话务专员已完成本人任务",
            "customer_intent": "同意继续跟进",
        }))
        assert completed["status"] == "completed"
        assert completed["assigned_to"] == call_operator["id"]
        assert completed["result"] == "connected"
        assert completed["summary"] == "话务专员已完成本人任务"

        denied = call_client.post(f"/api/outbound-calls/{other_call['id']}/complete", json={
            "result": "no_answer",
            "summary": "不得登记他人任务",
        })
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "call_access_denied"

        denied = call_client.post("/api/outbound-calls", json={
            "customer_id": customer_a["id"],
            "repair_order_id": order_a["id"],
            "contact_number": customer_a["phone"],
            "purpose": "结果登记白名单不得放开新建",
        })
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "permission_denied"

        denied = call_client.post("/api/customers", json={"name": "话务账号不得写入"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "permission_denied"
        denied = call_client.patch(
            f"/api/orders/{order_a['id']}/inspection", json={"internal_notes": "不得修改"}
        )
        assert denied.status_code == 403
        denied = call_client.delete(f"/api/users/{call_operator['id']}")
        assert denied.status_code == 403

    refreshed_other = next(
        row for row in unwrap(client.get("/api/outbound-calls")) if row["id"] == other_call["id"]
    )
    assert refreshed_other["status"] == "planned"
    assert refreshed_other["result"] is None


def test_engineer_call_completion_keeps_existing_work_order_acl(client: TestClient):
    owner = create_user(client, "call-acl-owner")
    assignee = create_user(client, "call-acl-assignee")
    customer_a, _device_a, order_a = create_order_bundle(client, "3013", engineer_id=owner["id"])
    customer_b, _device_b, order_b = create_order_bundle(client, "3014", engineer_id=assignee["id"])
    accessible_call = unwrap(client.post("/api/outbound-calls", json={
        "customer_id": customer_a["id"],
        "repair_order_id": order_a["id"],
        "assigned_to": assignee["id"],
        "contact_number": customer_a["phone"],
        "purpose": "按工单权限处理而非按外呼负责人限制",
    }))
    inaccessible_call = unwrap(client.post("/api/outbound-calls", json={
        "customer_id": customer_b["id"],
        "repair_order_id": order_b["id"],
        "assigned_to": owner["id"],
        "contact_number": customer_b["phone"],
        "purpose": "无权访问的维修工单",
    }))

    with TestClient(client.app) as owner_client:
        login = unwrap(owner_client.post(
            "/api/auth/login", json={"username": owner["username"], "password": GENERATED_PASSWORDS[owner["username"]]}
        ))
        owner_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        completed = unwrap(owner_client.post(f"/api/outbound-calls/{accessible_call['id']}/complete", json={
            "result": "callback",
            "summary": "工程师按既有工单权限完成登记",
        }))
        assert completed["status"] == "completed"
        assert completed["assigned_to"] == assignee["id"]

        denied = owner_client.post(f"/api/outbound-calls/{inaccessible_call['id']}/complete", json={
            "result": "no_answer",
            "summary": "不得跨工单权限登记",
        })
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "order_access_denied"


def test_service_ticket_search_keeps_scope_and_searches_related_records(client: TestClient):
    owner = create_user(client, "search-owner")
    outsider = create_user(client, "search-outsider")
    customer, device, order = create_order_bundle(
        client,
        "4005",
        engineer_id=owner["id"],
        customer_name="检索客户甲",
        serial_number="SEARCH-SN-4005",
        fault_description="检索专用云台抖动",
    )
    independent = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "consultation",
        "title": "检索专用咨询标题",
        "description": "检索专用描述关键字",
        "customer_id": customer["id"],
        "device_id": device["id"],
        "current_owner_id": owner["id"],
    }))
    repair_ticket = next(
        row for row in unwrap(client.get("/api/service-tickets"))
        if row["repair_order_id"] == order["id"]
    )

    call_operator = next(row for row in unwrap(client.get("/api/users")) if row["username"] == "call-desk")
    assert call_operator["role"] == "call_operator"
    with TestClient(client.app) as call_client:
        login = unwrap(call_client.post(
            "/api/auth/login", json={"username": "call-desk", "password": GENERATED_PASSWORDS["call-desk"]}
        ))
        call_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        for query in (
            repair_ticket["ticket_no"],
            "检索客户甲",
            customer["phone"],
            "SEARCH-SN-4005",
            order["order_no"],
            "检索专用云台抖动",
        ):
            rows = unwrap(call_client.get(f"/api/service-tickets?q={query}"))
            assert repair_ticket["id"] in {row["id"] for row in rows}, query
        for query in ("检索专用咨询标题", "检索专用描述关键字"):
            rows = unwrap(call_client.get(f"/api/service-tickets?q={query}&ticket_type=consultation"))
            assert [row["id"] for row in rows] == [independent["id"]]

    with TestClient(client.app) as outsider_client:
        login = unwrap(outsider_client.post(
            "/api/auth/login", json={"username": outsider["username"], "password": GENERATED_PASSWORDS[outsider["username"]]}
        ))
        outsider_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        assert unwrap(outsider_client.get("/api/service-tickets?q=SEARCH-SN-4005")) == []

    unwrap(client.delete(f"/api/service-tickets/{independent['id']}"))
    assert independent["id"] not in {
        row["id"] for row in unwrap(client.get("/api/service-tickets?q=检索专用咨询标题"))
    }
