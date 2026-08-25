from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.runtime_support import configure_test_runtime

GENERATED_PASSWORDS: dict[str, str] = {}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("work-order-logic-audit")
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


def create_user(client: TestClient, username: str):
    return unwrap(client.post("/api/users", json={
        "username": username,
        "display_name": username,
        "role": "engineer",
        "password": "OwnerPass123",
    }))


def create_order(client: TestClient, suffix: str, *, engineer_id: int | None = None):
    customer = unwrap(client.post("/api/customers", json={
        "name": f"客户{suffix}",
        "phone": f"139{int(suffix):08d}",
    }))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"],
        "brand": "DJI",
        "model": "Air 3S",
        "serial_number": f"AUDIT-{suffix}",
    }))
    payload = {
        "customer_id": customer["id"],
        "device_id": device["id"],
        "fault_description": f"审计工单 {suffix}",
    }
    if engineer_id is not None:
        payload["engineer_id"] = engineer_id
    order = unwrap(client.post("/api/orders", json=payload))
    return customer, device, order


def login_as(app, username: str) -> TestClient:
    user_client = TestClient(app)
    response = user_client.post(
        "/api/auth/login",
        json={"username": username, "password": GENERATED_PASSWORDS[username]},
    )
    assert response.status_code == 200, response.text
    user_client.headers.update({"X-CSRF-Token": response.json()["data"]["csrf_token"]})
    return user_client


def test_ticket_rejects_unknown_status(client: TestClient):
    _customer, _device, order = create_order(client, "101")
    ticket = unwrap(client.get(f"/api/orders/{order['id']}"))["service_ticket"]

    response = client.post(
        f"/api/service-tickets/{ticket['id']}/status",
        json={"status": "not-a-real-status", "reason": "验证非法状态"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["loc"][-1] == "status"

    skipped = client.post(
        f"/api/service-tickets/{ticket['id']}/status",
        json={"status": "closed", "reason": "不得跳过处理与解决阶段"},
    )
    assert skipped.status_code == 409
    assert skipped.json()["error"]["code"] == "invalid_ticket_transition"


def test_bulk_order_completion_uses_normal_workflow(client: TestClient):
    _customer, _device, order = create_order(client, "102")

    result = unwrap(client.post("/api/work-center/bulk", json={
        "items": [{"kind": "repair_order", "id": order["id"]}],
        "action": "status",
        "status": "completed",
        "reason": "维修与复测均已完成",
    }))

    assert result == {"processed": 1, "errors": []}
    detail = unwrap(client.get(f"/api/orders/{order['id']}"))
    assert detail["order"]["status"] == "completed"
    assert detail["service_ticket"]["status"] == "resolved"
    followups = unwrap(client.get("/api/follow-ups"))
    assert any(row["repair_order_id"] == order["id"] for row in followups)

    reopen = client.post(
        f"/api/orders/{order['id']}/status",
        json={"status": "inspecting", "reason": "不应从终态直接退回"},
    )
    assert reopen.status_code == 409
    assert reopen.json()["error"]["code"] == "order_terminal_state"

    quote = client.post("/api/quotes", json={
        "repair_order_id": order["id"],
        "items": [{"item_name": "终态报价", "quantity": "1", "unit_price": "10"}],
    })
    assert quote.status_code == 409
    assert quote.json()["error"]["code"] == "terminal_order_quote_forbidden"


def test_non_manager_cannot_unassign_owned_work(client: TestClient):
    owner = create_user(client, "owner-audit")
    _customer, _device, order = create_order(client, "103", engineer_id=owner["id"])
    ticket = unwrap(client.get(f"/api/orders/{order['id']}"))["service_ticket"]

    with login_as(client.app, "owner-audit") as owner_client:
        bulk = owner_client.post("/api/work-center/bulk", json={
            "items": [{"kind": "repair_order", "id": order["id"]}],
            "action": "assign",
            "owner_id": None,
        })
        assert bulk.status_code == 403
        direct = owner_client.patch(
            f"/api/service-tickets/{ticket['id']}/assignment",
            json={"current_owner_id": None, "processing_group_id": None, "reason": "公开给所有人"},
        )
        assert direct.status_code == 403


def test_order_attachments_are_scoped_to_authorized_staff(client: TestClient):
    owner_a = create_user(client, "attachment-owner-a")
    owner_b = create_user(client, "attachment-owner-b")
    _customer_a, _device_a, _order_a = create_order(client, "104", engineer_id=owner_a["id"])
    customer_b, _device_b, order_b = create_order(client, "105", engineer_id=owner_b["id"])
    attachment = unwrap(client.post(
        "/api/attachments",
        params={"repair_order_id": order_b["id"], "customer_id": customer_b["id"]},
        files={"file": ("private.txt", b"private repair evidence", "text/plain")},
    ))

    with login_as(client.app, "attachment-owner-a") as owner_client:
        listing = owner_client.get("/api/attachments", params={"repair_order_id": order_b["id"]})
        assert listing.status_code == 403
        download = owner_client.get(f"/api/files/attachment/{attachment['id']}")
        assert download.status_code == 403
        diagnosis = owner_client.post("/api/diagnoses", json={
            "repair_order_id": order_b["id"],
            "diagnosis_type": "manual",
            "severity": "high",
            "confidence": "0.9",
            "title": "不应允许的诊断",
            "description": "跨负责人写入",
        })
        assert diagnosis.status_code == 403
        shipment = owner_client.post("/api/shipments", json={
            "repair_order_id": order_b["id"],
            "tracking_no": "AUDIT-PRIVATE-SHIPMENT",
        })
        assert shipment.status_code == 403


def test_group_members_and_collaborators_have_exact_access(client: TestClient):
    member = create_user(client, "group-member")
    outsider = create_user(client, "group-outsider")
    group = unwrap(client.post("/api/processing-groups", json={
        "name": "权限审计服务组",
        "group_type": "service",
        "member_ids": [member["id"]],
    }))["group"]
    _customer, _device, grouped_order = create_order(client, "108")
    unwrap(client.patch(
        f"/api/orders/{grouped_order['id']}/service-group",
        json={"processing_group_id": group["id"]},
    ))
    grouped_ticket = unwrap(client.get(f"/api/orders/{grouped_order['id']}"))["service_ticket"]

    with login_as(client.app, "group-member") as member_client:
        assert member_client.get(f"/api/service-tickets/{grouped_ticket['id']}").status_code == 200
        assert member_client.get(f"/api/orders/{grouped_order['id']}").status_code == 200
    with login_as(client.app, "group-outsider") as outsider_client:
        assert outsider_client.get(f"/api/service-tickets/{grouped_ticket['id']}").status_code == 403
        assert outsider_client.get(f"/api/orders/{grouped_order['id']}").status_code == 403

    _customer2, _device2, owned_order = create_order(client, "109", engineer_id=outsider["id"])
    owned_ticket = unwrap(client.get(f"/api/orders/{owned_order['id']}"))["service_ticket"]
    unwrap(client.post(f"/api/service-tickets/{owned_ticket['id']}/collaborators", json={
        "user_id": member["id"],
        "collaborator_role": "assistant",
    }))
    with login_as(client.app, "group-member") as member_client:
        assert member_client.get(f"/api/service-tickets/{owned_ticket['id']}").status_code == 200
        assert member_client.get(f"/api/orders/{owned_order['id']}").status_code == 200


def test_deleted_work_cannot_be_bulk_mutated_and_linked_ticket_cannot_be_split(client: TestClient):
    _customer, _device, active_order = create_order(client, "110")
    linked_ticket = unwrap(client.get(f"/api/orders/{active_order['id']}"))["service_ticket"]
    linked_delete = client.delete(f"/api/service-tickets/{linked_ticket['id']}")
    assert linked_delete.status_code == 409
    assert linked_delete.json()["error"]["code"] == "linked_repair_ticket_delete_denied"

    unwrap(client.delete(f"/api/orders/{active_order['id']}"))
    bulk = client.post("/api/work-center/bulk", json={
        "items": [{"kind": "repair_order", "id": active_order["id"]}],
        "action": "status",
        "status": "completed",
        "reason": "不应修改回收站工单",
    })
    assert bulk.status_code == 404


def test_only_assigned_specialist_can_advance_escalation(client: TestClient):
    owner = create_user(client, "escalation-owner")
    specialist = create_user(client, "escalation-specialist")
    customer, device, _order = create_order(client, "111", engineer_id=owner["id"])
    ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "technical_support",
        "title": "专员权限审计",
        "description": "仅指定专员可以推进",
        "customer_id": customer["id"],
        "device_id": device["id"],
        "current_owner_id": owner["id"],
    }))
    group = unwrap(client.post("/api/processing-groups", json={
        "name": "权限审计专员组",
        "group_type": "specialist",
        "member_ids": [specialist["id"]],
    }))["group"]
    escalation = unwrap(client.post("/api/specialist-escalations", json={
        "service_ticket_id": ticket["id"],
        "reason": "需要专员",
        "problem_summary": "复杂问题",
        "attempted_solutions": "已完成基础排查",
        "assigned_specialist_id": specialist["id"],
        "specialist_group_id": group["id"],
    }))

    with login_as(client.app, "escalation-owner") as owner_client:
        denied = owner_client.patch(
            f"/api/specialist-escalations/{escalation['id']}",
            json={"status": "accepted"},
        )
        assert denied.status_code == 403
    with login_as(client.app, "escalation-specialist") as specialist_client:
        skipped = specialist_client.patch(
            f"/api/specialist-escalations/{escalation['id']}",
            json={"status": "completed", "solution": "方案", "final_result": "结果"},
        )
        assert skipped.status_code == 409
        accepted = specialist_client.patch(
            f"/api/specialist-escalations/{escalation['id']}",
            json={"status": "accepted"},
        )
        assert accepted.status_code == 200


def test_quick_entry_idempotency_key_checks_payload_and_access(client: TestClient):
    owner = create_user(client, "quick-owner")
    outsider = create_user(client, "quick-outsider")
    payload = {
        "customer_name": "幂等审计客户",
        "phone": "13900000112",
        "brand": "DJI",
        "model": "Mini 4 Pro",
        "serial_number": "AUDIT-QUICK-112",
        "fault_description": "幂等审计",
        "labor_fee": "10",
        "items": [],
        "generate_pdf": False,
        "send_email": False,
    }
    first = unwrap(client.post(
        "/api/quick-entry",
        json=payload,
        headers={"Idempotency-Key": "audit-quick-key-112"},
    ))
    ticket = unwrap(client.get(f"/api/orders/{first['order']['id']}"))["service_ticket"]
    unwrap(client.patch(f"/api/service-tickets/{ticket['id']}/assignment", json={
        "current_owner_id": owner["id"],
        "processing_group_id": None,
        "reason": "分派给负责人",
    }))

    changed = dict(payload)
    changed["fault_description"] = "同一个键但内容不同"
    mismatch = client.post(
        "/api/quick-entry",
        json=changed,
        headers={"Idempotency-Key": "audit-quick-key-112"},
    )
    assert mismatch.status_code == 409
    with login_as(client.app, "quick-outsider") as outsider_client:
        denied = outsider_client.post(
            "/api/quick-entry",
            json=payload,
            headers={"Idempotency-Key": "audit-quick-key-112"},
        )
        assert denied.status_code == 403


def test_finance_and_inventory_cannot_corrupt_order_links(client: TestClient):
    customer_a, _device_a, order_a = create_order(client, "106")
    customer_b, _device_b, _order_b = create_order(client, "107")

    mismatch = client.post("/api/finance", json={
        "repair_order_id": order_a["id"],
        "customer_id": customer_b["id"],
        "transaction_type": "income",
        "category": "维修收款",
        "amount": "100",
    })
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "finance_customer_mismatch"

    finance_payload = {
        "repair_order_id": order_a["id"],
        "transaction_type": "income",
        "category": "维修收款",
        "amount": "100",
    }
    finance = unwrap(client.post(
        "/api/finance",
        json=finance_payload,
        headers={"Idempotency-Key": "audit-finance-key-1"},
    ))
    assert finance["customer_id"] == customer_a["id"]
    replay_mismatch = client.post(
        "/api/finance",
        json={**finance_payload, "amount": "101"},
        headers={"Idempotency-Key": "audit-finance-key-1"},
    )
    assert replay_mismatch.status_code == 409
    assert replay_mismatch.json()["error"]["code"] == "idempotency_key_reused"

    item = unwrap(client.post("/api/inventory/items", json={
        "sku": "AUDIT-PART-1",
        "name": "审计配件",
        "stock_quantity": "2",
    }))
    unwrap(client.delete(f"/api/orders/{order_a['id']}"))
    stock = client.post("/api/inventory/transactions", json={
        "inventory_item_id": item["id"],
        "transaction_type": "repair_issue",
        "quantity": "1",
        "repair_order_id": order_a["id"],
    })
    assert stock.status_code == 404
    assert stock.json()["error"]["code"] == "order_not_found"


def test_inventory_costs_and_transactions_require_inventory_role(client: TestClient):
    engineer = create_user(client, "inventory-outsider")
    item = unwrap(client.post("/api/inventory/items", json={
        "sku": "AUDIT-PRIVATE-COST",
        "name": "成本受限配件",
        "purchase_price": "88.00",
        "sale_price": "168.00",
        "stock_quantity": "1",
    }))
    assert item["purchase_price"] == "88.00"

    with login_as(client.app, engineer["username"]) as engineer_client:
        visible_items = unwrap(engineer_client.get("/api/inventory/items"))
        visible = next(row for row in visible_items if row["id"] == item["id"])
        assert "purchase_price" not in visible
        assert "supplier_id" not in visible
        denied = engineer_client.get("/api/inventory/transactions")
        assert denied.status_code == 403
