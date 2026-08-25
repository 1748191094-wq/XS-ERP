from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.runtime_support import configure_test_runtime


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("followup-integer-inventory")
    configure_test_runtime(
        tmp_path,
        sync_role="host",
        sync_shared_secret="integer-stock-sync-secret-2026",
    )
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


def create_order_context(client: TestClient):
    customer = unwrap(client.post("/api/customers", json={"name": "排序测试客户", "phone": "13900009001"}))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"], "brand": "DJI", "model": "Air 3S", "serial_number": "SORT-SN-001",
    }))
    order = unwrap(client.post("/api/orders", json={
        "customer_id": customer["id"], "device_id": device["id"], "fault_description": "回访排序验证",
    }))
    return customer, order


def test_pending_followups_are_first_with_stable_order_everywhere(client: TestClient):
    customer, order = create_order_context(client)

    completed = unwrap(client.post("/api/follow-ups", json={
        "repair_order_id": order["id"], "customer_id": customer["id"],
        "scheduled_at": "2029-01-01T09:00:00+08:00", "content": "最早但已完成",
    }))
    unwrap(client.patch(f"/api/follow-ups/{completed['id']}", json={
        "status": "completed", "result": "已完成",
    }))
    pending_a = unwrap(client.post("/api/follow-ups", json={
        "repair_order_id": order["id"], "customer_id": customer["id"],
        "scheduled_at": "2030-01-01T09:00:00+08:00", "content": "同时间待办 A",
    }))
    pending_b = unwrap(client.post("/api/follow-ups", json={
        "repair_order_id": order["id"], "customer_id": customer["id"],
        "scheduled_at": "2030-01-01T09:00:00+08:00", "content": "同时间待办 B",
    }))

    listed = unwrap(client.get("/api/follow-ups"))
    assert [row["id"] for row in listed] == [pending_a["id"], pending_b["id"], completed["id"]]

    detail = unwrap(client.get(f"/api/orders/{order['id']}"))
    assert [row["id"] for row in detail["followups"]] == [pending_a["id"], pending_b["id"], completed["id"]]

    work_items = [
        row for row in unwrap(client.get("/api/work-center?view=all"))["items"]
        if row["kind"] == "followup"
    ]
    assert [row["id"] for row in work_items] == [pending_a["id"], pending_b["id"]]
    assert unwrap(client.get("/api/dashboard"))["pending_followups"] == 2


def test_inventory_endpoints_reject_fractions_and_return_integer_counts(client: TestClient):
    fractional_item = client.post("/api/inventory/items", json={
        "sku": "FRACTIONAL-ITEM", "name": "非法小数库存", "stock_quantity": "1.5", "safety_stock": 0,
    })
    assert fractional_item.status_code == 422

    item = unwrap(client.post("/api/inventory/items", json={
        "sku": "INTEGER-ITEM", "name": "整数库存", "stock_quantity": 5, "safety_stock": 1,
    }))
    assert item["stock_quantity"] == 5
    assert item["safety_stock"] == 1

    fractional_change = client.post("/api/inventory/transactions", json={
        "inventory_item_id": item["id"], "transaction_type": "stock_out", "quantity": "0.5",
    })
    assert fractional_change.status_code == 422

    transaction = unwrap(client.post("/api/inventory/transactions", json={
        "inventory_item_id": item["id"], "transaction_type": "stock_out", "quantity": 2,
    }))
    assert (transaction["quantity"], transaction["before_quantity"], transaction["after_quantity"]) == (2, 5, 3)

    supplier = unwrap(client.post("/api/suppliers", json={"name": "整数采购供应商"}))
    fractional_purchase = client.post("/api/purchase-orders", json={
        "supplier_id": supplier["id"],
        "items": [{"inventory_item_id": item["id"], "quantity": "1.5", "unit_cost": "10"}],
    })
    assert fractional_purchase.status_code == 422

    purchase = unwrap(client.post("/api/purchase-orders", json={
        "supplier_id": supplier["id"],
        "items": [{"inventory_item_id": item["id"], "quantity": 4, "unit_cost": "10"}],
    }))
    assert purchase["items"][0]["quantity"] == 4
    fractional_receipt = client.post(f"/api/purchase-orders/{purchase['id']}/receive", json={
        "lines": [{"purchase_order_item_id": purchase["items"][0]["id"], "quantity": "1.5"}],
    })
    assert fractional_receipt.status_code == 422

    fractional_stocktake = client.post("/api/stocktakes", json={
        "items": [{"inventory_item_id": item["id"], "counted_quantity": "2.5"}],
    })
    assert fractional_stocktake.status_code == 422


def test_sync_rejects_fractional_inventory_before_any_write(client: TestClient):
    from app.core.config import settings
    from app.services.sync import payload_hash

    payload = {
        "sku": "SYNC-FRACTIONAL",
        "name": "同步非法小数库存",
        "stock_quantity": "1.5",
        "safety_stock": "0",
    }
    response = client.post(
        "/api/sync/push",
        headers={"X-Sync-Secret": settings.sync_shared_secret},
        json={
            "node_id": str(uuid.uuid4()),
            "events": [{
                "event_id": str(uuid.uuid4()),
                "entity_type": "inventory_item",
                "record_key": payload["sku"],
                "operation": "upsert",
                "base_revision": 0,
                "base_payload_json": None,
                "payload_json": payload,
                "payload_hash": payload_hash(payload),
            }],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "inventory_quantity_must_be_integer"
    assert payload["sku"] not in {row["sku"] for row in unwrap(client.get("/api/inventory/items"))}


def test_inventory_ui_uses_integer_controls_and_formatting():
    app_source = Path("app/static/app.js").read_text(encoding="utf-8")
    workflow_source = Path("app/static/workflow.js").read_text(encoding="utf-8")
    assert "function inventoryQty(value)" in app_source
    assert "inventoryQty(x.stock_quantity)" in app_source
    assert "inventoryQty(x.before_quantity)" in app_source
    assert "inventoryQty(x.received_quantity)" in app_source
    assert "inventoryQty(x.net_received_quantity)" in app_source
    assert 'data-stocktake="${x.id}" type="number" min="0" step="1"' in app_source
    assert 'name="quantity" type="number" min="1" step="1"' in app_source
    assert "inventoryQty(x.stock_quantity)" in workflow_source
