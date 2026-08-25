from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from tests.runtime_support import configure_test_runtime

GENERATED_PASSWORDS: dict[str, str] = {}


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
    data = body["data"]
    if isinstance(data, dict) and data.get("username") and data.get("generated_password"):
        GENERATED_PASSWORDS[data["username"]] = data["generated_password"]
    return data


def create_order_context(client: TestClient):
    customer = unwrap(client.post("/api/customers", json={"name": "测试客户", "phone": "13900000001"}))
    device = unwrap(client.post("/api/devices", json={"customer_id": customer["id"], "brand": "DJI", "model": "Air 3S", "serial_number": "TEST-SN-001"}))
    order = unwrap(client.post("/api/orders", json={"customer_id": customer["id"], "device_id": device["id"], "fault_description": "云台抖动"}))
    return customer, device, order


def test_complete_business_flow(client: TestClient):
    customer, _device, order = create_order_context(client)
    inspection_text = "人工检测：主控通信正常，云台轴存在机械阻力。\n处理建议：更换排线后复测。"
    inspection = unwrap(client.patch(f"/api/orders/{order['id']}/inspection", json={
        "internal_notes": inspection_text,
    }))
    assert inspection["internal_notes"] == inspection_text
    detail_after_inspection = unwrap(client.get(f"/api/orders/{order['id']}"))
    assert detail_after_inspection["order"]["internal_notes"] == inspection_text
    duplicate = client.post("/api/customers", json={"name": "重复客户", "phone": "13900000001"})
    assert duplicate.status_code == 409

    item = unwrap(client.post("/api/inventory/items", json={"sku": "PART-001", "name": "云台排线", "purchase_price": "80.00", "sale_price": "160.00", "stock_quantity": "3", "safety_stock": "1"}))
    quote = unwrap(client.post("/api/quotes", json={"repair_order_id": order["id"], "labor_fee": "120", "discount": "10", "assessment_result": "云台轴存在机械阻力", "assessment_responsibility": "外力损坏", "repair_recommendation": "更换排线并复测", "customer_notice": "维修前请备份数据", "items": [{"inventory_item_id": item["id"], "item_name": "云台排线", "quantity": "1", "unit_price": "160", "cost_price": "80"}]}))
    assert quote["version"] == 1
    assert quote["total_amount"] == "270.00"
    assert quote["assessment_result"] == "云台轴存在机械阻力"
    assert quote["assessment_responsibility"] == "外力损坏"
    assert quote["repair_recommendation"] == "更换排线并复测"
    quote_v2 = unwrap(client.post("/api/quotes", json={"repair_order_id": order["id"], "labor_fee": "100", "items": [{"item_name": "云台排线", "quantity": "1", "unit_price": "150", "cost_price": "80"}]}))
    assert quote_v2["version"] == 2
    assert len(unwrap(client.get(f"/api/quotes?repair_order_id={order['id']}"))) == 2
    unwrap(client.post(f"/api/quotes/{quote_v2['id']}/confirm"))

    stock_payload = {"inventory_item_id": item["id"], "transaction_type": "repair_issue", "quantity": "1", "repair_order_id": order["id"]}
    stock_tx = unwrap(client.post("/api/inventory/transactions", json=stock_payload, headers={"Idempotency-Key": "stock-flow-001"}))
    stock_duplicate = unwrap(client.post("/api/inventory/transactions", json=stock_payload, headers={"Idempotency-Key": "stock-flow-001"}))
    assert stock_duplicate["id"] == stock_tx["id"]
    assert stock_tx["before_quantity"] == 3
    assert stock_tx["after_quantity"] == 2
    finance_payload = {"repair_order_id": order["id"], "customer_id": customer["id"], "transaction_type": "income", "category": "维修收款", "amount": "250", "payment_method": "wechat"}
    finance_tx = unwrap(client.post("/api/finance", json=finance_payload, headers={"Idempotency-Key": "finance-flow-001"}))
    finance_duplicate = unwrap(client.post("/api/finance", json=finance_payload, headers={"Idempotency-Key": "finance-flow-001"}))
    assert finance_duplicate["id"] == finance_tx["id"]
    detail = unwrap(client.get(f"/api/orders/{order['id']}"))
    assert detail["order"]["total_received"] == "250.00"
    assert detail["order"]["total_cost"] == "80.00"
    assert detail["order"]["gross_profit"] == "170.00"

    pdf = unwrap(client.post(f"/api/quotes/{quote_v2['id']}/pdf"))
    downloaded = client.get(pdf["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF")
    repair_report = unwrap(client.post(f"/api/orders/{order['id']}/reports/inspection"))
    repair_report_downloaded = client.get(repair_report["download_url"])
    assert repair_report_downloaded.status_code == 200
    assert repair_report_downloaded.content.startswith(b"%PDF")

    unwrap(client.post(f"/api/orders/{order['id']}/status", json={"status": "completed", "reason": "测试完成"}))
    followups = unwrap(client.get("/api/follow-ups?status=pending"))
    assert len(followups) == 1


def test_customer_and_finance_records_can_be_edited_safely(client: TestClient):
    customer, _device, order = create_order_context(client)
    updated_customer = unwrap(client.patch(f"/api/customers/{customer['id']}", json={
        "name": "测试客户（已更新）",
        "email": "updated@example.com",
    }))
    assert updated_customer["name"] == "测试客户（已更新）"
    assert updated_customer["phone"] == customer["phone"]
    assert updated_customer["email"] == "updated@example.com"

    customer_b = unwrap(client.post("/api/customers", json={"name": "第二位客户", "phone": "13900000002"}))
    duplicate_phone = client.patch(f"/api/customers/{customer_b['id']}", json={"phone": customer["phone"]})
    assert duplicate_phone.status_code == 409
    device_b = unwrap(client.post("/api/devices", json={
        "customer_id": customer_b["id"], "brand": "Apple", "model": "MacBook Pro", "serial_number": "TEST-SN-002",
    }))
    order_b = unwrap(client.post("/api/orders", json={
        "customer_id": customer_b["id"], "device_id": device_b["id"], "fault_description": "无法开机",
    }))

    transaction = unwrap(client.post("/api/finance", json={
        "repair_order_id": order["id"], "customer_id": customer["id"],
        "transaction_type": "income", "category": "维修收款", "amount": "200", "payment_method": "wechat",
    }))
    edited = unwrap(client.patch(f"/api/finance/{transaction['id']}", json={
        "repair_order_id": order_b["id"], "customer_id": customer_b["id"],
        "transaction_type": "expense", "category": "外协支出", "amount": "40", "payment_method": "bank",
        "description": "改关联工单后自动重算",
    }))
    assert edited["transaction_no"] == transaction["transaction_no"]
    assert edited["repair_order_id"] == order_b["id"]
    assert edited["transaction_type"] == "expense"
    assert edited["amount"] == "40.00"

    old_order = unwrap(client.get(f"/api/orders/{order['id']}"))["order"]
    new_order = unwrap(client.get(f"/api/orders/{order_b['id']}"))["order"]
    assert old_order["total_received"] == "0.00"
    assert old_order["gross_profit"] == "0.00"
    assert new_order["total_cost"] == "40.00"
    assert new_order["gross_profit"] == "-40.00"


def test_admin_deletions_are_hidden_and_reversible(client: TestClient):
    customer, _device, order = create_order_context(client)
    ticket = unwrap(client.get("/api/service-tickets?ticket_type=repair"))[0]
    quote = unwrap(client.post("/api/quotes", json={
        "repair_order_id": order["id"],
        "items": [{"item_name": "检测服务", "item_type": "service", "quantity": "1", "unit_price": "80"}],
    }))
    finance = unwrap(client.post("/api/finance", json={
        "repair_order_id": order["id"], "customer_id": customer["id"],
        "transaction_type": "income", "category": "预收款", "amount": "50",
    }))

    deleted_quote = unwrap(client.delete(f"/api/quotes/{quote['id']}"))
    assert deleted_quote["resource_type"] == "quote"
    assert quote["id"] not in {row["id"] for row in unwrap(client.get("/api/quotes"))}
    assert client.get(f"/api/quotes/{quote['id']}").status_code == 404
    unwrap(client.post(f"/api/trash/{deleted_quote['id']}/restore"))
    assert quote["id"] in {row["id"] for row in unwrap(client.get("/api/quotes"))}

    deleted_order = unwrap(client.delete(f"/api/orders/{order['id']}"))
    assert order["id"] not in {row["id"] for row in unwrap(client.get("/api/orders"))}
    assert ticket["id"] not in {row["id"] for row in unwrap(client.get("/api/service-tickets"))}
    assert quote["id"] not in {row["id"] for row in unwrap(client.get("/api/quotes"))}
    assert finance["id"] in {row["id"] for row in unwrap(client.get("/api/finance"))}
    trash = unwrap(client.get("/api/trash"))
    assert any(row["id"] == deleted_order["id"] and row["affected_count"] == 3 for row in trash)

    unwrap(client.post(f"/api/trash/{deleted_order['id']}/restore"))
    assert order["id"] in {row["id"] for row in unwrap(client.get("/api/orders"))}
    assert ticket["id"] in {row["id"] for row in unwrap(client.get("/api/service-tickets"))}
    assert quote["id"] in {row["id"] for row in unwrap(client.get("/api/quotes"))}

    deleted_customer = unwrap(client.delete(f"/api/customers/{customer['id']}"))
    assert customer["id"] not in {row["id"] for row in unwrap(client.get("/api/customers"))}
    assert order["id"] in {row["id"] for row in unwrap(client.get("/api/orders"))}
    unwrap(client.post(f"/api/trash/{deleted_customer['id']}/restore"))
    assert customer["id"] in {row["id"] for row in unwrap(client.get("/api/customers"))}

    app_js = client.get("/static/app.js").text
    assert "if(e.target.id==='modal')closeModal()" not in app_js
    assert "undoAppAction()" in app_js
    assert "redoAppAction()" in app_js
    assert "customerPickerField('customer_id','关联客户',{required:true})" in app_js
    assert "输入姓名、电话、客户编号、邮箱或公司检索" in app_js
    assert "device.customer_id===customer.id" in app_js


def test_repeated_device_serials_keep_independent_customer_ownership(client: TestClient):
    customer_a = unwrap(client.post("/api/customers", json={"name": "原机主", "phone": "13600000001"}))
    customer_b = unwrap(client.post("/api/customers", json={"name": "二手机主", "phone": "13600000002"}))
    device_a = unwrap(client.post("/api/devices", json={
        "customer_id": customer_a["id"], "brand": "Apple", "model": "iPhone 15",
        "serial_number": "TRANSFERRED-SN-001",
    }))
    device_b = unwrap(client.post("/api/devices", json={
        "customer_id": customer_b["id"], "brand": "Apple", "model": "iPhone 15",
        "serial_number": "TRANSFERRED-SN-001",
    }))
    assert device_a["id"] != device_b["id"]
    assert device_a["customer_id"] == customer_a["id"]
    assert device_b["customer_id"] == customer_b["id"]

    quick_payload = {
        "customer_name": "二手机主",
        "phone": "13600000002",
        "brand": "Apple",
        "model": "iPhone 15",
        "serial_number": "TRANSFERRED-SN-001",
        "fault_description": "二手流转后再次收机",
        "generate_pdf": False,
        "send_email": False,
    }
    quick = unwrap(client.post(
        "/api/quick-entry", json=quick_payload, headers={"Idempotency-Key": "transfer-intake-001"},
    ))
    assert quick["customer"]["id"] == customer_b["id"]
    assert quick["device"]["id"] not in {device_a["id"], device_b["id"]}
    assert quick["device"]["serial_number"] == "TRANSFERRED-SN-001"
    assert quick["device"]["customer_id"] == customer_b["id"]


def test_unified_service_ticket_and_specialist_escalation_flow(client: TestClient):
    customer, device, order = create_order_context(client)
    repair_tickets = unwrap(client.get("/api/service-tickets?ticket_type=repair"))
    assert len(repair_tickets) == 1
    assert repair_tickets[0]["repair_order_id"] == order["id"]

    specialist = unwrap(client.post("/api/users", json={
        "username": "specialist1",
        "display_name": "高级专员",
        "role": "technical_support",
        "password": "Specialist123",
        "wecom_userid": "specialist1",
    }))
    assert specialist["role"] == "technical_support"
    assert specialist["wecom_userid"] == "specialist1"
    group = unwrap(client.post("/api/processing-groups", json={
        "name": "高级技术组",
        "group_type": "specialist",
        "member_ids": [specialist["id"]],
    }))
    assert group["member_ids"] == [specialist["id"]]

    ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "complaint",
        "title": "客户投诉处理",
        "description": "客户反馈维修进度沟通不及时",
        "customer_id": customer["id"],
        "device_id": device["id"],
        "priority": "high",
        "current_owner_id": specialist["id"],
        "processing_group_id": group["group"]["id"],
    }))
    assert ticket["status"] == "assigned"

    retail_ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "retail",
        "title": "门店零售接待",
        "description": "客户到店购买数码配件",
        "customer_id": customer["id"],
        "priority": "normal",
    }))
    assert retail_ticket["ticket_type"] == "retail"
    assert any(item["id"] == retail_ticket["id"] for item in unwrap(client.get("/api/service-tickets?ticket_type=retail")))

    unwrap(client.post(f"/api/service-tickets/{ticket['id']}/collaborators", json={
        "user_id": specialist["id"], "collaborator_role": "specialist"
    }))
    internal_note = unwrap(client.post(f"/api/service-tickets/{ticket['id']}/notes", json={
        "visibility": "internal", "content": "内部核对沟通记录"
    }))
    customer_note = unwrap(client.post(f"/api/service-tickets/{ticket['id']}/notes", json={
        "visibility": "customer", "content": "已向客户说明当前进度"
    }))
    assert internal_note["visibility"] == "internal"
    assert customer_note["visibility"] == "customer"

    pdf = unwrap(client.post(f"/api/service-tickets/{ticket['id']}/pdf"))
    downloaded = client.get(pdf["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF")

    preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "technical_support",
        "service_ticket_id": ticket["id"],
    }))
    assert preview["template_name"] == "技术支持"
    assert preview["subject"] == "技术支持"
    assert "测试商标服务中心" in preview["body"]
    assert ticket["description"] in preview["body"]
    queued_email = unwrap(client.post("/api/outbound-emails", json={
        "template_type": "technical_support",
        "service_ticket_id": ticket["id"],
        "recipient": "support-customer@example.com",
        "subject": preview["subject"],
        "body": preview["body"],
        "auto_attach_report": True,
    }))
    assert queued_email["task_id"]
    delivery = unwrap(client.get("/api/outbound-emails"))[0]
    assert len(delivery["attachment_snapshot_json"]) == 1
    assert delivery["attachment_snapshot_json"][0]["filename"].endswith("-service.pdf")

    combined_email = unwrap(client.post("/api/outbound-emails", json={
        "template_type": "technical_support",
        "service_ticket_id": repair_tickets[0]["id"],
        "repair_order_id": order["id"],
        "recipient": "support-customer@example.com",
        "subject": "服务与维修工单资料",
        "body": "请查收服务工单和维修工单资料。",
        "auto_attach_report": False,
        "attach_service_ticket_pdf": True,
        "attach_repair_report_pdf": True,
    }))
    combined_delivery = next(
        item for item in unwrap(client.get("/api/outbound-emails"))
        if item["email_no"] == combined_email["email_no"]
    )
    assert combined_delivery["service_ticket_id"] == repair_tickets[0]["id"]
    assert combined_delivery["repair_order_id"] == order["id"]
    combined_names = [item["filename"] for item in combined_delivery["attachment_snapshot_json"]]
    assert any(name.endswith("-service.pdf") for name in combined_names)
    assert any(name.endswith("-inspection.pdf") for name in combined_names)

    reminded = unwrap(client.post(f"/api/service-tickets/{ticket['id']}/remind", json={
        "reason": "客户要求今日回复"
    }))
    assert reminded["reminder_count"] == 1
    assert reminded["wecom_notification"]["status"] == "mock"
    assert reminded["wecom_notification"]["recipient_userid"] == "specialist1"
    unwrap(client.post(f"/api/service-tickets/{ticket['id']}/status", json={
        "status": "in_progress", "reason": "开始处理投诉"
    }))

    escalation = unwrap(client.post("/api/specialist-escalations", json={
        "service_ticket_id": ticket["id"],
        "reason": "需要高级专员制定处置方案",
        "problem_summary": "客户对处理时效不满",
        "attempted_solutions": "已电话解释并承诺回访",
        "urgency": "urgent",
        "assigned_specialist_id": specialist["id"],
        "specialist_group_id": group["group"]["id"],
    }))
    assert escalation["status"] == "submitted"
    returned = unwrap(client.patch(f"/api/specialist-escalations/{escalation['id']}", json={
        "status": "returned", "return_reason": "补充完整沟通时间线"
    }))
    assert returned["return_reason"] == "补充完整沟通时间线"
    unwrap(client.patch(f"/api/specialist-escalations/{escalation['id']}", json={
        "status": "accepted", "specialist_opinion": "建议当日给出明确完成时间"
    }))
    completed = unwrap(client.patch(f"/api/specialist-escalations/{escalation['id']}", json={
        "status": "completed",
        "specialist_opinion": "确认由负责人持续同步",
        "solution": "当日电话回复并每日更新进度",
        "final_result": "客户接受方案",
    }))
    assert completed["completed_at"]
    detail = unwrap(client.get(f"/api/service-tickets/{ticket['id']}"))
    assert len(detail["notes"]) == 2
    assert len(detail["collaborators"]) == 1
    assert len(detail["escalations"]) == 1
    assert any(event["event_type"] == "escalated" for event in detail["timeline"])
    assert any(event["event_type"] == "pdf_generated" for event in detail["timeline"])


def test_replacement_ticket_fields_workbench_permissions_and_pdf(client: TestClient):
    owner_a = unwrap(client.post("/api/users", json={
        "username": "replacement-owner-a",
        "display_name": "置换负责人甲",
        "role": "engineer",
        "password": "ReplacementA123",
    }))
    owner_b = unwrap(client.post("/api/users", json={
        "username": "replacement-owner-b",
        "display_name": "置换负责人乙",
        "role": "engineer",
        "password": "ReplacementB123",
    }))
    customer = unwrap(client.post("/api/customers", json={
        "name": "置换客户",
        "phone": "13900000661",
        "email": "replacement@example.com",
    }))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"],
        "brand": "DJI",
        "model": "Mini 4 Pro",
        "serial_number": "REPLACEMENT-SN-001",
    }))
    created = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "旧机检测并置换新机",
        "description": "客户申请以旧机抵折新机款项",
        "customer_id": customer["id"],
        "device_id": device["id"],
        "current_owner_id": owner_a["id"],
        "replacement_inspection_result": "  外观边框有划痕；开机、云台与图传功能正常。  ",
        "trade_in_credit": "1688.50",
        "return_reference": "  线下交易：2026-08-13 门店当面交接，凭证 SRV-OFFLINE-001  ",
        "outbound_to_customer_tracking_no": "  SF-OUT-REPLACEMENT-001  ",
    }))
    assert created["ticket_type"] == "replacement"
    assert created["replacement_inspection_result"] == "外观边框有划痕；开机、云台与图传功能正常。"
    assert created["trade_in_credit"] == "1688.50"
    assert created["return_reference"] == "线下交易：2026-08-13 门店当面交接，凭证 SRV-OFFLINE-001"
    assert created["outbound_to_customer_tracking_no"] == "SF-OUT-REPLACEMENT-001"

    listed = unwrap(client.get("/api/service-tickets?ticket_type=replacement"))
    listed_ticket = next(item for item in listed if item["id"] == created["id"])
    assert float(listed_ticket["trade_in_credit"]) == 1688.5
    assert listed_ticket["return_reference"].startswith("线下交易：")
    detail = unwrap(client.get(f"/api/service-tickets/{created['id']}"))
    assert detail["ticket"]["replacement_inspection_result"] == created["replacement_inspection_result"]

    updated = unwrap(client.patch(f"/api/service-tickets/{created['id']}/replacement", json={
        "return_reference": "线下交易：已复核门店交接单 SRV-OFFLINE-001",
        "outbound_to_customer_tracking_no": "SF-OUT-REPLACEMENT-002",
    }))
    assert updated["replacement_inspection_result"] == created["replacement_inspection_result"]
    assert updated["trade_in_credit"] == "1688.50"
    assert updated["return_reference"] == "线下交易：已复核门店交接单 SRV-OFFLINE-001"
    assert updated["outbound_to_customer_tracking_no"] == "SF-OUT-REPLACEMENT-002"
    updated_detail = unwrap(client.get(f"/api/service-tickets/{created['id']}"))
    replacement_events = [
        event for event in updated_detail["timeline"]
        if event["event_type"] == "replacement_updated"
    ]
    assert replacement_events[-1]["details_json"]["trade_in_credit"] == "1688.50"
    assert replacement_events[-1]["details_json"]["outbound_to_customer_tracking_no"] == "SF-OUT-REPLACEMENT-002"

    for invalid_amount in ("-0.01", "1.234", "10000000000.00", "NaN"):
        invalid_update = client.patch(f"/api/service-tickets/{created['id']}/replacement", json={
            "trade_in_credit": invalid_amount,
        })
        assert invalid_update.status_code == 422
    assert unwrap(client.get(f"/api/service-tickets/{created['id']}"))["ticket"]["trade_in_credit"] == "1688.50"
    negative_create = client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "负数抵折不应创建",
        "description": "无效置换金额",
        "customer_id": customer["id"],
        "trade_in_credit": "-1",
    })
    assert negative_create.status_code == 422

    blank_replacement = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "置换资料后补",
        "description": "先建工单，检测与抵折稍后填写",
        "customer_id": customer["id"],
    }))
    assert blank_replacement["trade_in_credit"] is None
    assert blank_replacement["replacement_inspection_result"] is None

    non_replacement_with_fields = client.post("/api/service-tickets", json={
        "ticket_type": "consultation",
        "title": "普通咨询不得写入置换资料",
        "description": "验证类型字段边界",
        "customer_id": customer["id"],
        "return_reference": "线下交易：不应落库",
    })
    assert non_replacement_with_fields.status_code == 422
    consultation = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "consultation",
        "title": "普通咨询",
        "description": "无置换资料",
        "customer_id": customer["id"],
    }))
    wrong_type_update = client.patch(f"/api/service-tickets/{consultation['id']}/replacement", json={
        "replacement_inspection_result": "不应写入",
    })
    assert wrong_type_update.status_code == 409
    assert wrong_type_update.json()["error"]["code"] == "replacement_ticket_required"

    pdf = unwrap(client.post(f"/api/service-tickets/{created['id']}/pdf"))
    pdf_download = client.get(pdf["download_url"])
    assert pdf_download.status_code == 200
    assert pdf_download.content.startswith(b"%PDF")

    owner_b_ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "乙负责人置换工单",
        "description": "仅乙负责人可见",
        "customer_id": customer["id"],
        "current_owner_id": owner_b["id"],
        "trade_in_credit": "500",
    }))
    with TestClient(client.app) as owner_client:
        login = unwrap(owner_client.post("/api/auth/login", json={
            "username": "replacement-owner-a",
            "password": GENERATED_PASSWORDS["replacement-owner-a"],
        }))
        owner_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        visible_ids = {
            item["id"] for item in unwrap(owner_client.get("/api/service-tickets?ticket_type=replacement"))
        }
        assert created["id"] in visible_ids
        assert owner_b_ticket["id"] not in visible_ids
        assert owner_client.get(f"/api/service-tickets/{created['id']}").status_code == 200
        assert owner_client.get(f"/api/service-tickets/{owner_b_ticket['id']}").status_code == 403
        owner_update = unwrap(owner_client.patch(
            f"/api/service-tickets/{created['id']}/replacement",
            json={"outbound_to_customer_tracking_no": "SF-OWNER-A-UPDATED"},
        ))
        assert owner_update["outbound_to_customer_tracking_no"] == "SF-OWNER-A-UPDATED"
        denied_update = owner_client.patch(
            f"/api/service-tickets/{owner_b_ticket['id']}/replacement",
            json={"outbound_to_customer_tracking_no": "SHOULD-NOT-WRITE"},
        )
        assert denied_update.status_code == 403

    convertible = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "可清空后改类型的置换单",
        "description": "验证显式清空规则",
        "customer_id": customer["id"],
        "trade_in_credit": "0.00",
        "return_reference": "线下交易：待撤销",
    }))
    blocked_change = client.patch(f"/api/service-tickets/{convertible['id']}/type", json={
        "ticket_type": "consultation",
        "expected_ticket_type": "replacement",
        "reason": "置换业务取消",
    })
    assert blocked_change.status_code == 409
    assert blocked_change.json()["error"]["code"] == "ticket_type_has_replacement_data"
    cleared = unwrap(client.patch(f"/api/service-tickets/{convertible['id']}/replacement", json={
        "replacement_inspection_result": None,
        "trade_in_credit": None,
        "return_reference": None,
        "outbound_to_customer_tracking_no": None,
    }))
    assert all(cleared[field] is None for field in (
        "replacement_inspection_result",
        "trade_in_credit",
        "return_reference",
        "outbound_to_customer_tracking_no",
    ))
    changed = unwrap(client.patch(f"/api/service-tickets/{convertible['id']}/type", json={
        "ticket_type": "consultation",
        "expected_ticket_type": "replacement",
        "reason": "置换取消，改为普通咨询",
    }))
    assert changed["ticket_type"] == "consultation"
    assert all(changed[field] is None for field in (
        "replacement_inspection_result",
        "trade_in_credit",
        "return_reference",
        "outbound_to_customer_tracking_no",
    ))


def test_replacement_quote_versions_payment_email_context_and_permissions(client: TestClient):
    owner_a = unwrap(client.post("/api/users", json={
        "username": "replacement-quote-owner-a",
        "display_name": "置换报价负责人甲",
        "role": "engineer",
        "password": "ReplacementQuoteA123",
    }))
    owner_b = unwrap(client.post("/api/users", json={
        "username": "replacement-quote-owner-b",
        "display_name": "置换报价负责人乙",
        "role": "engineer",
        "password": "ReplacementQuoteB123",
    }))
    customer = unwrap(client.post("/api/customers", json={
        "name": "置换报价客户",
        "phone": "13900000662",
        "email": "replacement-quote@example.com",
    }))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"],
        "brand": "DJI",
        "model": "Air 3S",
        "serial_number": "REPLACEMENT-QUOTE-SN-001",
    }))
    ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "旧机抵折置换 Air 3S",
        "description": "旧机检测后抵折新机款项",
        "customer_id": customer["id"],
        "device_id": device["id"],
        "current_owner_id": owner_a["id"],
        "replacement_inspection_result": "外观轻微划痕，飞行与图传功能正常",
        "trade_in_credit": "1200.00",
        "return_reference": "线下交易：门店交接 SRV-OFFLINE-QUOTE-001",
        "outbound_to_customer_tracking_no": "SF-REPLACEMENT-OUT-001",
    }))
    payment_url = "https://pay.example.com/replacement/QT-001?channel=email&version=1"
    quote_v1 = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": ticket["id"],
        "items": [{
            "item_name": "Air 3S 置换新机方案",
            "item_type": "part",
            "quantity": "1",
            "unit_price": "5000.00",
            "cost_price": "4000.00",
        }],
        "labor_fee": "200.00",
        "shipping_fee": "50.00",
        "discount": "1200.00",
        "payment_url": payment_url,
    }))
    assert quote_v1["version"] == 1
    assert quote_v1["subtotal"] == "5000.00"
    assert quote_v1["discount"] == "1200.00"
    # The ticket credit becomes the frozen Quote.discount once; the backend
    # must not implicitly subtract ServiceTicket.trade_in_credit a second time.
    assert quote_v1["total_amount"] == "4050.00"
    assert quote_v1["payment_url"] == payment_url

    quote_v2 = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": ticket["id"],
        "items": [{
            "item_name": "Air 3S 置换新机调整方案",
            "item_type": "part",
            "quantity": "1",
            "unit_price": "4800.00",
            "cost_price": "3900.00",
        }],
        "labor_fee": "100.00",
        "shipping_fee": "20.00",
        "discount": "900.00",
        "payment_url": "   ",
    }))
    assert quote_v2["version"] == 2
    assert quote_v2["total_amount"] == "4020.00"
    assert quote_v2["payment_url"] is None
    versions = unwrap(client.get(f"/api/quotes?service_ticket_id={ticket['id']}"))
    assert [(row["version"], row["status"]) for row in versions] == [
        (2, "draft"),
        (1, "superseded"),
    ]
    assert unwrap(client.get(f"/api/quotes/{quote_v1['id']}"))["payment_url"] == payment_url
    assert unwrap(client.get(f"/api/service-tickets/{ticket['id']}"))["ticket"]["trade_in_credit"] == "1200.00"

    pdf = unwrap(client.post(f"/api/quotes/{quote_v1['id']}/pdf"))
    downloaded = client.get(pdf["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF")

    replacement_template = next(
        item for item in unwrap(client.get("/api/email/templates"))
        if item["template_type"] == "replacement_quote"
    )
    assert replacement_template["name"] == "置换服务报价通知"
    linked_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "replacement_quote",
        "quote_id": quote_v1["id"],
    }))
    assert linked_preview["template_name"] == "置换服务报价通知"
    assert "置换服务报价" in linked_preview["subject"]
    assert "旧机检测结果：外观轻微划痕，飞行与图传功能正常" in linked_preview["body"]
    assert "已计入旧机抵折 / 优惠 ¥1200.00" in linked_preview["body"]
    assert "最终应付合计 ¥4050.00" in linked_preview["body"]
    assert "不重复扣减" in linked_preview["body"]
    assert f"付款链接：{payment_url}" in linked_preview["body"]
    assert "线下交易：门店交接 SRV-OFFLINE-QUOTE-001" in linked_preview["body"]
    assert "SF-REPLACEMENT-OUT-001" in linked_preview["body"]

    no_link_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "replacement_quote",
        "quote_id": quote_v2["id"],
    }))
    assert "已计入旧机抵折 / 优惠 ¥900.00" in no_link_preview["body"]
    assert "工单评估抵折参考：¥1200.00" in no_link_preview["body"]
    assert "最终应付合计 ¥4020.00" in no_link_preview["body"]
    assert "付款链接：" not in no_link_preview["body"]

    retail_ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "retail",
        "title": "普通零售方案",
        "description": "用于验证模板上下文",
        "customer_id": customer["id"],
    }))
    retail_quote = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": retail_ticket["id"],
        "items": [{"item_name": "普通零售商品", "quantity": "1", "unit_price": "100"}],
    }))
    wrong_replacement_context = client.post("/api/email/preview", json={
        "template_type": "replacement_quote",
        "quote_id": retail_quote["id"],
    })
    assert wrong_replacement_context.status_code == 409
    assert wrong_replacement_context.json()["error"]["code"] == "replacement_quote_required"
    wrong_retail_context = client.post("/api/email/preview", json={
        "template_type": "retail_quote",
        "quote_id": quote_v1["id"],
    })
    assert wrong_retail_context.status_code == 409
    assert wrong_retail_context.json()["error"]["code"] == "retail_quote_required"
    missing_quote = client.post("/api/email/preview", json={
        "template_type": "replacement_quote",
        "service_ticket_id": ticket["id"],
    })
    assert missing_quote.status_code == 409
    assert missing_quote.json()["error"]["code"] == "quote_required"

    consultation = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "consultation",
        "title": "普通咨询不得报价",
        "description": "验证报价业务类型边界",
        "customer_id": customer["id"],
    }))
    forbidden_quote = client.post("/api/quotes", json={
        "service_ticket_id": consultation["id"],
        "items": [{"item_name": "不应创建", "quantity": "1", "unit_price": "1"}],
    })
    assert forbidden_quote.status_code == 409
    assert forbidden_quote.json()["error"]["code"] == "service_ticket_quote_type_invalid"

    other_ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "乙负责人置换报价",
        "description": "权限隔离",
        "customer_id": customer["id"],
        "current_owner_id": owner_b["id"],
        "trade_in_credit": "200.00",
    }))
    other_quote = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": other_ticket["id"],
        "discount": "200.00",
        "items": [{"item_name": "乙负责人方案", "quantity": "1", "unit_price": "1000"}],
    }))
    with TestClient(client.app) as owner_client:
        login = unwrap(owner_client.post("/api/auth/login", json={
            "username": "replacement-quote-owner-a",
            "password": GENERATED_PASSWORDS["replacement-quote-owner-a"],
        }))
        owner_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        assert owner_client.post("/api/email/preview", json={
            "template_type": "replacement_quote", "quote_id": quote_v1["id"],
        }).status_code == 200
        sent = unwrap(owner_client.post("/api/outbound-emails", json={
            "template_type": "replacement_quote",
            "quote_id": quote_v1["id"],
            "auto_attach_report": True,
        }))
        delivery = next(
            row for row in unwrap(owner_client.get("/api/outbound-emails"))
            if row["id"] == sent["email_id"]
        )
        assert delivery["status"] == "sent"
        assert delivery["template_type"] == "replacement_quote"
        assert delivery["quote_id"] == quote_v1["id"]
        assert delivery["attachment_snapshot_json"][0]["filename"] == f"{quote_v1['quote_no']}.pdf"
        assert f"付款链接：{payment_url}" in delivery["body_snapshot"]

        denied_preview = owner_client.post("/api/email/preview", json={
            "template_type": "replacement_quote", "quote_id": other_quote["id"],
        })
        assert denied_preview.status_code == 403
        denied_send = owner_client.post("/api/outbound-emails", json={
            "template_type": "replacement_quote",
            "quote_id": other_quote["id"],
            "auto_attach_report": True,
        })
        assert denied_send.status_code == 403

    type_change = client.patch(f"/api/service-tickets/{ticket['id']}/type", json={
        "ticket_type": "retail",
        "expected_ticket_type": "replacement",
        "reason": "已有置换报价时不得重新解释为零售业务",
    })
    assert type_change.status_code == 409
    assert type_change.json()["error"]["code"] == "ticket_type_has_active_quotes"


def test_quote_restore_keeps_service_ticket_type_invariant(client: TestClient):
    customer = unwrap(client.post("/api/customers", json={
        "name": "报价恢复边界客户",
        "phone": "13900000663",
    }))

    replacement = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "replacement",
        "title": "待删除恢复的置换报价",
        "description": "验证回收站不能恢复成错型报价",
        "customer_id": customer["id"],
        "trade_in_credit": "300.00",
    }))
    replacement_quote = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": replacement["id"],
        "discount": "300.00",
        "items": [{"item_name": "置换方案", "quantity": "1", "unit_price": "1000"}],
    }))
    deletion = unwrap(client.delete(f"/api/quotes/{replacement_quote['id']}"))
    unwrap(client.patch(f"/api/service-tickets/{replacement['id']}/replacement", json={
        "replacement_inspection_result": None,
        "trade_in_credit": None,
        "return_reference": None,
        "outbound_to_customer_tracking_no": None,
    }))
    changed = unwrap(client.patch(f"/api/service-tickets/{replacement['id']}/type", json={
        "ticket_type": "consultation",
        "expected_ticket_type": "replacement",
        "reason": "报价已在回收站，改成普通咨询",
    }))
    assert changed["ticket_type"] == "consultation"

    invalid_restore = client.post(f"/api/trash/{deletion['id']}/restore")
    assert invalid_restore.status_code == 409
    assert invalid_restore.json()["error"]["code"] == "quote_restore_target_invalid"
    assert client.get(f"/api/quotes/{replacement_quote['id']}").status_code == 404
    pending_trash = {row["id"]: row for row in unwrap(client.get("/api/trash"))}
    assert deletion["id"] in pending_trash

    for index, ticket_type in enumerate(("retail", "replacement"), start=1):
        payload = {
            "ticket_type": ticket_type,
            "title": f"合法恢复 {ticket_type}",
            "description": "有效业务类型上的报价允许恢复",
            "customer_id": customer["id"],
        }
        if ticket_type == "replacement":
            payload["trade_in_credit"] = "100.00"
        valid_ticket = unwrap(client.post("/api/service-tickets", json=payload))
        valid_quote = unwrap(client.post("/api/quotes", json={
            "service_ticket_id": valid_ticket["id"],
            "discount": "100.00" if ticket_type == "replacement" else "0.00",
            "items": [{
                "item_name": f"合法恢复方案 {index}",
                "quantity": "1",
                "unit_price": "500.00",
            }],
        }))
        valid_deletion = unwrap(client.delete(f"/api/quotes/{valid_quote['id']}"))
        restored = unwrap(client.post(f"/api/trash/{valid_deletion['id']}/restore"))
        assert restored["resource_type"] == "quote"
        assert unwrap(client.get(f"/api/quotes/{valid_quote['id']}"))["id"] == valid_quote["id"]


def test_service_ticket_type_can_be_changed_with_history_and_consistency_guards(client: TestClient):
    customer, device, _order = create_order_context(client)
    linked_repair_ticket = unwrap(client.get("/api/service-tickets?ticket_type=repair"))[0]

    ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "complaint",
        "title": "客户沟通工单",
        "description": "先按投诉受理，复核后确认属于技术支持",
        "customer_id": customer["id"],
        "device_id": device["id"],
    }))
    changed = unwrap(client.patch(f"/api/service-tickets/{ticket['id']}/type", json={
        "ticket_type": "technical_support",
        "expected_ticket_type": "complaint",
        "reason": "工程师复核后确认需要技术诊断",
    }))
    assert changed["ticket_type"] == "technical_support"

    detail = unwrap(client.get(f"/api/service-tickets/{ticket['id']}"))
    type_events = [event for event in detail["timeline"] if event["event_type"] == "type_changed"]
    assert len(type_events) == 1
    assert "投诉处理 → 技术支持" in type_events[0]["summary"]
    assert type_events[0]["details_json"] == {
        "from_ticket_type": "complaint",
        "to_ticket_type": "technical_support",
        "reason": "工程师复核后确认需要技术诊断",
    }

    unchanged = unwrap(client.patch(f"/api/service-tickets/{ticket['id']}/type", json={
        "ticket_type": "technical_support",
        "expected_ticket_type": "technical_support",
        "reason": "重复提交不产生新记录",
    }))
    assert unchanged["ticket_type"] == "technical_support"
    unchanged_detail = unwrap(client.get(f"/api/service-tickets/{ticket['id']}"))
    assert len([event for event in unchanged_detail["timeline"] if event["event_type"] == "type_changed"]) == 1

    stale_change = client.patch(f"/api/service-tickets/{ticket['id']}/type", json={
        "ticket_type": "consultation",
        "expected_ticket_type": "complaint",
        "reason": "模拟另一名成员使用过期页面提交",
    })
    assert stale_change.status_code == 409
    assert unwrap(client.get(f"/api/service-tickets/{ticket['id']}"))["ticket"]["ticket_type"] == "technical_support"

    invalid = client.patch(f"/api/service-tickets/{ticket['id']}/type", json={
        "ticket_type": "unknown_type", "reason": "测试非法值",
    })
    assert invalid.status_code == 422
    cannot_become_repair = client.patch(f"/api/service-tickets/{ticket['id']}/type", json={
        "ticket_type": "repair", "reason": "尝试改成维修类型",
    })
    assert cannot_become_repair.status_code == 409
    linked_locked = client.patch(f"/api/service-tickets/{linked_repair_ticket['id']}/type", json={
        "ticket_type": "consultation", "reason": "尝试修改自动维修工单",
    })
    assert linked_locked.status_code == 409

    retail = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "retail",
        "title": "门店零售报价",
        "description": "客户购买配件",
        "customer_id": customer["id"],
    }))
    unwrap(client.post("/api/quotes", json={
        "service_ticket_id": retail["id"],
        "items": [{"item_name": "数码配件", "item_type": "part", "quantity": "1", "unit_price": "99"}],
    }))
    quoted_retail_locked = client.patch(f"/api/service-tickets/{retail['id']}/type", json={
        "ticket_type": "consultation", "reason": "已有报价后尝试改类型",
    })
    assert quoted_retail_locked.status_code == 409

    app_js = client.get("/static/app.js").text
    assert "changeTicketType" in app_js
    assert "维修类型已锁定" in app_js
    assert "/type`" in app_js
    assert "expected_ticket_type" in app_js
    assert "确认更改类型" in app_js
    assert "更多操作" in app_js
    assert "showModalFormError" in app_js
    styles_css = client.get("/static/styles.css").text
    assert ".form-error.hidden { display: none; }" in styles_css


def test_retail_quote_visibility_and_description_history(client: TestClient):
    owner_a = unwrap(client.post("/api/users", json={
        "username": "owner-a", "display_name": "负责人甲", "role": "engineer", "password": "OwnerA123"
    }))
    owner_b = unwrap(client.post("/api/users", json={
        "username": "owner-b", "display_name": "负责人乙", "role": "engineer", "password": "OwnerB123"
    }))
    customer = unwrap(client.post("/api/customers", json={
        "name": "零售客户", "phone": "13900000088", "email": "retail@example.com"
    }))
    device_a = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"], "brand": "Apple", "model": "iPad", "serial_number": "RETAIL-A"
    }))
    device_b = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"], "brand": "Apple", "model": "MacBook", "serial_number": "RETAIL-B"
    }))
    order_a = unwrap(client.post("/api/orders", json={
        "customer_id": customer["id"], "device_id": device_a["id"], "fault_description": "甲负责维修", "engineer_id": owner_a["id"]
    }))
    order_b = unwrap(client.post("/api/orders", json={
        "customer_id": customer["id"], "device_id": device_b["id"], "fault_description": "乙负责维修", "engineer_id": owner_b["id"]
    }))
    unassigned_order = unwrap(client.post("/api/orders", json={
        "customer_id": customer["id"], "device_id": device_a["id"], "fault_description": "未分派维修"
    }))
    retail = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "retail", "title": "门店零售", "description": "客户咨询平板套装",
        "customer_id": customer["id"], "device_id": device_a["id"], "current_owner_id": owner_a["id"]
    }))
    other_retail = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "retail", "title": "其他零售", "description": "乙负责的零售单",
        "customer_id": customer["id"], "current_owner_id": owner_b["id"]
    }))
    unassigned_retail = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "retail", "title": "待分派零售", "description": "尚未分派",
        "customer_id": customer["id"]
    }))

    payment_url = "https://pay.example.com/checkout/QT-RETAIL?channel=email&campaign=service%20quote"
    retail_quote = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": retail["id"],
        "items": [{"item_name": "平板电脑套装", "item_type": "part", "quantity": "1", "unit_price": "3999", "cost_price": "3200"}],
        "discount": "100",
        "payment_url": f"  {payment_url}  ",
    }))
    assert retail_quote["repair_order_id"] is None
    assert retail_quote["service_ticket_id"] == retail["id"]
    assert retail_quote["total_amount"] == "3899.00"
    assert retail_quote["payment_url"] == payment_url
    retail_pdf = unwrap(client.post(f"/api/quotes/{retail_quote['id']}/pdf"))
    assert client.get(retail_pdf["download_url"]).content.startswith(b"%PDF")

    retail_template = next(
        item for item in unwrap(client.get("/api/email/templates"))
        if item["template_type"] == "retail_quote"
    )
    assert retail_template["name"] == "服务报价通知"
    preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "retail_quote", "quote_id": retail_quote["id"]
    }))
    assert preview["template_name"] == "服务报价通知"
    assert preview["service_ticket_id"] == retail["id"]
    assert preview["repair_order_id"] is None
    assert "服务报价" in preview["subject"]
    assert "服务需求" in preview["body"]
    assert "确认服务方案" in preview["body"]
    assert "继续维修" not in preview["body"]
    assert f"付款链接：{payment_url}" in preview["body"]
    queued = unwrap(client.post("/api/outbound-emails", json={
        "template_type": "retail_quote", "quote_id": retail_quote["id"], "auto_attach_report": True
    }))
    delivery = next(item for item in unwrap(client.get("/api/outbound-emails")) if item["email_no"] == queued["email_no"])
    assert delivery["template_type"] == "retail_quote"
    assert delivery["service_ticket_id"] == retail["id"]
    assert delivery["repair_order_id"] is None
    assert delivery["status"] == "sent"
    assert delivery["provider"] == "mock"
    assert "服务报价" in delivery["subject_snapshot"]
    assert f"付款链接：{payment_url}" in delivery["body_snapshot"]
    assert delivery["attachment_snapshot_json"][0]["filename"] == f"{retail_quote['quote_no']}.pdf"

    no_link_quote = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": retail["id"],
        "items": [{"item_name": "平板电脑套装二次方案", "item_type": "part", "quantity": "1", "unit_price": "3799", "cost_price": "3100"}],
        "payment_url": "   ",
    }))
    assert no_link_quote["version"] == retail_quote["version"] + 1
    assert no_link_quote["payment_url"] is None
    assert unwrap(client.get(f"/api/quotes/{retail_quote['id']}"))["payment_url"] == payment_url
    no_link_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "retail_quote", "quote_id": no_link_quote["id"]
    }))
    assert "付款链接：" not in no_link_preview["body"]
    assert payment_url not in no_link_preview["body"]

    quote_ids_before_invalid_urls = {item["id"] for item in unwrap(client.get("/api/quotes"))}
    for unsafe_url in (
        "javascript:alert(1)",
        "https://user:secret@pay.example.com/checkout",
        "https://pay.example.com/has a space",
    ):
        rejected = client.post("/api/quotes", json={
            "service_ticket_id": retail["id"],
            "items": [{"item_name": "危险链接不应落库", "quantity": "1", "unit_price": "1"}],
            "payment_url": unsafe_url,
        })
        assert rejected.status_code == 422, rejected.text
    assert {item["id"] for item in unwrap(client.get("/api/quotes"))} == quote_ids_before_invalid_urls

    repair_quote = unwrap(client.post("/api/quotes", json={
        "repair_order_id": order_a["id"],
        "items": [{"item_name": "维修检测服务", "item_type": "service", "quantity": "1", "unit_price": "80"}],
        "payment_url": "https://pay.example.com/repair/should-not-use-retail-template",
    }))
    wrong_context = client.post("/api/email/preview", json={
        "template_type": "retail_quote", "quote_id": repair_quote["id"]
    })
    assert wrong_context.status_code == 409
    assert wrong_context.json()["error"]["code"] == "retail_quote_required"

    other_retail_quote = unwrap(client.post("/api/quotes", json={
        "service_ticket_id": other_retail["id"],
        "items": [{"item_name": "其他负责人的零售方案", "item_type": "service", "quantity": "1", "unit_price": "50"}],
    }))

    unwrap(client.patch(f"/api/service-tickets/{retail['id']}/description", json={
        "description": "客户确定购买平板套装", "reason": "客户补充购买意向"
    }))
    unwrap(client.patch(f"/api/service-tickets/{retail['id']}/description", json={
        "description": "客户确定购买平板套装并需要送货", "reason": "增加配送要求"
    }))
    retail_detail = unwrap(client.get(f"/api/service-tickets/{retail['id']}"))
    revisions = [row for row in retail_detail["timeline"] if row["event_type"] == "description_updated"]
    assert retail_detail["ticket"]["description"] == "客户确定购买平板套装并需要送货"
    assert revisions[0]["details_json"]["previous_description"] == "客户咨询平板套装"
    assert revisions[1]["details_json"]["previous_description"] == "客户确定购买平板套装"

    with TestClient(client.app) as owner_client:
        login = unwrap(owner_client.post("/api/auth/login", json={"username": "owner-a", "password": GENERATED_PASSWORDS["owner-a"]}))
        owner_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        visible_orders = {row["id"] for row in unwrap(owner_client.get("/api/orders"))}
        assert order_a["id"] in visible_orders
        assert unassigned_order["id"] in visible_orders
        assert order_b["id"] not in visible_orders
        visible_tickets = {row["id"] for row in unwrap(owner_client.get("/api/service-tickets"))}
        assert retail["id"] in visible_tickets
        assert unassigned_retail["id"] in visible_tickets
        assert other_retail["id"] not in visible_tickets
        assert owner_client.get(f"/api/orders/{order_b['id']}").status_code == 403
        assert owner_client.get(f"/api/service-tickets/{other_retail['id']}").status_code == 403
        visible_quotes = {row["id"] for row in unwrap(owner_client.get("/api/quotes"))}
        assert retail_quote["id"] in visible_quotes
        own_retail_preview = owner_client.post("/api/email/preview", json={
            "template_type": "retail_quote", "quote_id": retail_quote["id"]
        })
        assert own_retail_preview.status_code == 200, own_retail_preview.text
        denied_retail_preview = owner_client.post("/api/email/preview", json={
            "template_type": "retail_quote", "quote_id": other_retail_quote["id"]
        })
        assert denied_retail_preview.status_code == 403
        dashboard_ids = {row["id"] for row in unwrap(owner_client.get("/api/dashboard"))["recent_orders"]}
        assert order_b["id"] not in dashboard_ids
        search_ids = {(row["kind"], row["id"]) for row in unwrap(owner_client.get("/api/search?q=乙负责维修"))}
        assert ("repair_order", order_b["id"]) not in search_ids

    assert order_b["id"] in {row["id"] for row in unwrap(client.get("/api/orders"))}
    assert other_retail["id"] in {row["id"] for row in unwrap(client.get("/api/service-tickets"))}


def test_ticket_status_and_assignment_validation(client: TestClient):
    _customer, _device, _order = create_order_context(client)
    ticket = unwrap(client.get("/api/service-tickets?ticket_type=repair"))[0]

    empty_status_reason = client.post(
        f"/api/service-tickets/{ticket['id']}/status",
        json={"status": "in_progress", "reason": "   "},
    )
    assert empty_status_reason.status_code == 422
    assert empty_status_reason.json()["error"]["details"][0]["loc"][-1] == "reason"

    updated = unwrap(client.post(
        f"/api/service-tickets/{ticket['id']}/status",
        json={"status": "in_progress", "reason": "  开始检测设备  "},
    ))
    assert updated["status"] == "in_progress"

    owner = unwrap(client.post("/api/users", json={
        "username": "ticketowner",
        "display_name": "工单负责人",
        "role": "engineer",
        "password": "Owner123",
    }))
    group = unwrap(client.post("/api/processing-groups", json={
        "name": "维修处理组",
        "group_type": "repair",
        "member_ids": [owner["id"]],
    }))

    empty_assignment_reason = client.patch(
        f"/api/service-tickets/{ticket['id']}/assignment",
        json={"current_owner_id": owner["id"], "processing_group_id": group["group"]["id"], "reason": ""},
    )
    assert empty_assignment_reason.status_code == 422
    assert empty_assignment_reason.json()["error"]["details"][0]["loc"][-1] == "reason"

    assigned = unwrap(client.patch(
        f"/api/service-tickets/{ticket['id']}/assignment",
        json={
            "current_owner_id": owner["id"],
            "processing_group_id": group["group"]["id"],
            "reason": "  转交维修组处理  ",
        },
    ))
    assert assigned["current_owner_id"] == owner["id"]
    assert assigned["processing_group_id"] == group["group"]["id"]


def test_global_search_quote_types_and_parallel_orders(client: TestClient):
    customer, _device, order = create_order_context(client)
    quote = unwrap(client.post("/api/quotes", json={
        "repair_order_id": order["id"],
        "items": [
            {"item_name": "检测服务", "item_type": "service", "quantity": "1", "unit_price": "80"},
            {"item_name": "排线", "item_type": "part", "quantity": "1", "unit_price": "120", "cost_price": "50"},
        ],
    }))

    quote_detail = unwrap(client.get(f"/api/quotes/{quote['id']}"))
    assert [item["item_type"] for item in quote_detail["items"]] == ["service", "part"]

    order_detail = unwrap(client.get(f"/api/orders/{order['id']}"))
    assert order_detail["service_ticket"]["repair_order_id"] == order["id"]
    assert order_detail["service_ticket"]["ticket_type"] == "repair"

    order_results = unwrap(client.get("/api/search", params={"q": order["order_no"]}))
    result_kinds = {item["kind"] for item in order_results}
    assert "repair_order" in result_kinds
    assert "service_ticket" in result_kinds

    customer_results = unwrap(client.get("/api/search", params={"q": customer["name"]}))
    assert any(item["kind"] == "customer" and item["id"] == customer["id"] for item in customer_results)

    invalid_type = client.post("/api/quotes", json={
        "repair_order_id": order["id"],
        "items": [{"item_name": "未知项目", "item_type": "invalid", "quantity": "1", "unit_price": "1"}],
    })
    assert invalid_type.status_code == 422


def test_outbound_call_and_snapshot_email_flow(client: TestClient):
    customer, _device, order = create_order_context(client)
    ticket = unwrap(client.get("/api/service-tickets?ticket_type=repair"))[0]
    quote = unwrap(client.post("/api/quotes", json={
        "repair_order_id": order["id"],
        "labor_fee": "99",
        "items": [{"item_name": "人工检测", "quantity": "1", "unit_price": "30", "cost_price": "0"}],
    }))

    planned = unwrap(client.post("/api/outbound-calls", json={
        "customer_id": customer["id"],
        "service_ticket_id": ticket["id"],
        "repair_order_id": order["id"],
        "contact_number": "13900000001",
        "purpose": "确认报价与维修意向",
    }))
    assert planned["provider"] == "manual"
    assert planned["status"] == "planned"
    completed = unwrap(client.post(f"/api/outbound-calls/{planned['id']}/complete", json={
        "result": "connected",
        "duration_seconds": 86,
        "summary": "客户同意查看报价后回复",
        "customer_intent": "考虑维修",
        "next_contact_at": "2030-01-02T09:00:00+08:00",
    }))
    assert completed["status"] == "completed"
    assert completed["duration_seconds"] == 86

    preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "quote",
        "service_ticket_id": ticket["id"],
        "repair_order_id": order["id"],
        "quote_id": quote["id"],
    }))
    assert quote["quote_no"] in preview["body"]
    assert preview["recipient"] is None

    queued = unwrap(client.post("/api/outbound-emails", json={
        "template_type": "quote",
        "service_ticket_id": ticket["id"],
        "repair_order_id": order["id"],
        "quote_id": quote["id"],
        "recipient": "customer@example.com",
        "cc": ["advisor@example.com"],
        "bcc": ["archive@example.com"],
        "subject": "人工修改后的报价主题",
        "body": "人工确认后的正文快照\n第二行",
        "auto_attach_report": True,
    }))
    assert queued["task_id"]
    deliveries = unwrap(client.get("/api/outbound-emails"))
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["status"] == "sent"
    assert delivery["provider"] == "mock"
    assert delivery["attempts"] == 1
    assert delivery["subject_snapshot"] == "人工修改后的报价主题"
    assert delivery["body_snapshot"] == "人工确认后的正文快照\n第二行"
    assert delivery["cc_json"] == ["advisor@example.com"]
    assert delivery["bcc_json"] == ["archive@example.com"]
    assert len(delivery["attachment_snapshot_json"]) == 1
    snapshot = delivery["attachment_snapshot_json"][0]
    assert Path(snapshot["snapshot_path"]).is_file()
    assert len(snapshot["sha256"]) == 64
    detail = unwrap(client.get(f"/api/service-tickets/{ticket['id']}"))
    event_types = {event["event_type"] for event in detail["timeline"]}
    assert {"call_planned", "call_completed", "email_queued", "email_sent"}.issubset(event_types)

    intake_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "intake",
        "service_ticket_id": ticket["id"],
        "repair_order_id": order["id"],
    }))
    assert intake_preview["template_name"] == "维修中心收件通知"
    assert intake_preview["status_presentation"]["index"] == 1
    assert intake_preview["includes_responsibility_notice"] is True
    assert "已收到您的" in intake_preview["body"]

    status_queued = unwrap(client.post("/api/outbound-emails", json={
        "template_type": "intake",
        "service_ticket_id": ticket["id"],
        "repair_order_id": order["id"],
        "recipient": "customer@example.com",
        "subject": intake_preview["subject"],
        "body": intake_preview["body"],
        "auto_attach_report": False,
    }))
    assert status_queued["email_id"]
    status_delivery = next(
        item for item in unwrap(client.get("/api/outbound-emails"))
        if item["id"] == status_queued["email_id"]
    )
    assert "—— 服务责任与重要说明 ——" in status_delivery["body_snapshot"]
    assert "不排除或限制您依据适用法律享有的权利" in status_delivery["body_snapshot"]

    quote_status_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "quote_status",
        "service_ticket_id": ticket["id"],
        "repair_order_id": order["id"],
        "quote_id": quote["id"],
    }))
    assert quote_status_preview["status_presentation"]["index"] == 3
    quote_status_queued = unwrap(client.post("/api/outbound-emails", json={
        "template_type": "quote_status",
        "service_ticket_id": ticket["id"],
        "repair_order_id": order["id"],
        "quote_id": quote["id"],
        "recipient": "customer@example.com",
        "auto_attach_report": True,
    }))
    quote_status_delivery = next(
        item for item in unwrap(client.get("/api/outbound-emails"))
        if item["id"] == quote_status_queued["email_id"]
    )
    assert len(quote_status_delivery["attachment_snapshot_json"]) == 1
    assert "服务责任与重要说明" in quote_status_delivery["body_snapshot"]


def test_service_ticket_status_email_uses_non_repair_responsibility_notice(client: TestClient):
    customer = unwrap(client.post("/api/customers", json={
        "name": "服务声明客户",
        "phone": "13900000088",
        "email": "service-notice@example.com",
    }))
    ticket = unwrap(client.post("/api/service-tickets", json={
        "ticket_type": "consultation",
        "title": "购买方案咨询",
        "description": "咨询不同产品的适用场景",
        "customer_id": customer["id"],
    }))

    preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "intake",
        "service_ticket_id": ticket["id"],
    }))
    assert preview["responsibility_notice"]["kind"] == "service"
    assert preview["responsibility_notice"]["title"] == "服务范围与权益说明"
    assert any(
        item["title"] == "服务范围与确认"
        for item in preview["responsibility_notice"]["items"]
    )

    queued = unwrap(client.post("/api/outbound-emails", json={
        "template_type": "intake",
        "service_ticket_id": ticket["id"],
        "auto_attach_report": False,
    }))
    delivery = next(
        item for item in unwrap(client.get("/api/outbound-emails"))
        if item["id"] == queued["email_id"]
    )
    assert "—— 服务范围与权益说明 ——" in delivery["body_snapshot"]
    assert "咨询、报价、零售、置换、投诉处理、技术支持" in delivery["body_snapshot"]
    assert "检测与维修授权" not in delivery["body_snapshot"]
    assert "维修、换件" not in delivery["body_snapshot"]

    _customer, _device, order = create_order_context(client)
    repair_ticket = next(
        item for item in unwrap(client.get("/api/service-tickets?ticket_type=repair"))
        if item["repair_order_id"] == order["id"]
    )
    repair_preview = unwrap(client.post("/api/email/preview", json={
        "template_type": "intake",
        "service_ticket_id": repair_ticket["id"],
        "repair_order_id": order["id"],
    }))
    assert repair_preview["responsibility_notice"]["kind"] == "repair"
    assert repair_preview["responsibility_notice"]["title"] == "服务责任与重要说明"


def test_logistics_and_followup_closed_loop(client: TestClient):
    customer, _device, order = create_order_context(client)
    shipment = unwrap(client.post("/api/shipments", json={
        "repair_order_id": order["id"],
        "receiver_info_json": {"name": customer["name"], "phone": customer["phone"]},
    }))
    assert shipment["provider"] == "sf_express"
    assert shipment["logistics_status"] == "pending_submit"
    events = unwrap(client.get(f"/api/shipments/{shipment['id']}/events"))
    assert events[0]["source"] == "offline_queue"
    updated = unwrap(client.patch(f"/api/shipments/{shipment['id']}", json={
        "logistics_status": "in_transit",
        "tracking_no": "SF-TEST-001",
        "location": "深圳转运中心",
        "description": "人工登记运输中",
    }))
    assert updated["tracking_no"] == "SF-TEST-001"
    assert updated["logistics_status"] == "in_transit"
    unwrap(client.patch(f"/api/shipments/{shipment['id']}", json={
        "logistics_status": "delivered",
        "description": "客户确认签收",
    }))
    assert len(unwrap(client.get(f"/api/shipments/{shipment['id']}/events"))) == 3

    followup = unwrap(client.post("/api/follow-ups", json={
        "repair_order_id": order["id"],
        "customer_id": customer["id"],
        "follow_up_type": "shipping_confirmation",
        "scheduled_at": "2030-01-02T09:00:00+08:00",
        "content": "确认签收和设备状态",
    }))
    unwrap(client.patch(f"/api/follow-ups/{followup['id']}", json={
        "status": "completed",
        "result": "已签收，三日后再次确认使用情况",
        "next_follow_up_at": "2030-01-05T09:00:00+08:00",
    }))
    pending = unwrap(client.get("/api/follow-ups?status=pending"))
    assert len(pending) == 1
    assert pending[0]["scheduled_at"].startswith("2030-01-05")
    ticket = unwrap(client.get("/api/service-tickets?ticket_type=repair"))[0]
    timeline = unwrap(client.get(f"/api/service-tickets/{ticket['id']}"))["timeline"]
    event_types = {event["event_type"] for event in timeline}
    assert {"shipment_created", "shipment_updated", "followup_planned", "followup_updated"}.issubset(event_types)


def test_followup_admin_soft_delete_is_hidden_and_reversible(client: TestClient):
    customer, _device, order = create_order_context(client)
    followup = unwrap(client.post("/api/follow-ups", json={
        "repair_order_id": order["id"],
        "customer_id": customer["id"],
        "follow_up_type": "repair_satisfaction",
        "scheduled_at": "2030-02-03T10:30:00+08:00",
        "content": "需要保留并可恢复的回访记录",
    }))
    worker = unwrap(client.post("/api/users", json={
        "username": "followup-delete-worker",
        "display_name": "回访删除权限测试",
        "role": "engineer",
        "password": "WorkerPass123",
    }))

    with TestClient(client.app) as worker_client:
        login = unwrap(worker_client.post("/api/auth/login", json={
            "username": worker["username"],
            "password": GENERATED_PASSWORDS[worker["username"]],
        }))
        worker_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        denied = worker_client.delete(f"/api/follow-ups/{followup['id']}")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "permission_denied"

    assert followup["id"] in {row["id"] for row in unwrap(client.get("/api/follow-ups"))}
    deletion = unwrap(client.delete(f"/api/follow-ups/{followup['id']}"))
    assert deletion["resource_type"] == "follow_up_task"
    assert deletion["resource_id"] == followup["id"]
    assert followup["id"] not in {row["id"] for row in unwrap(client.get("/api/follow-ups"))}
    hidden_update = client.patch(f"/api/follow-ups/{followup['id']}", json={
        "status": "completed",
        "result": "回收站中的记录不得继续更新",
    })
    assert hidden_update.status_code == 404
    assert hidden_update.json()["error"]["code"] == "followup_not_found"

    trash = unwrap(client.get("/api/trash"))
    trash_row = next(row for row in trash if row["id"] == deletion["id"])
    assert trash_row["resource_type"] == "follow_up_task"
    assert trash_row["resource_id"] == followup["id"]
    assert trash_row["affected_count"] == 1

    from app.core.database import SessionLocal
    from app.models.entities import FollowUpTask

    with SessionLocal() as db:
        deleted_row = db.get(FollowUpTask, followup["id"])
        assert deleted_row is not None
        assert deleted_row.deleted_at is not None
        assert deleted_row.deletion_batch_id is not None

    restored_deletion = unwrap(client.post(f"/api/trash/{deletion['id']}/restore"))
    assert restored_deletion["resource_type"] == "follow_up_task"
    assert restored_deletion["restored_at"] is not None
    restored = next(
        row for row in unwrap(client.get("/api/follow-ups")) if row["id"] == followup["id"]
    )
    assert restored["content"] == "需要保留并可恢复的回访记录"
    assert restored["scheduled_at"].startswith("2030-02-03")
    assert deletion["id"] not in {row["id"] for row in unwrap(client.get("/api/trash"))}

    with SessionLocal() as db:
        restored_row = db.get(FollowUpTask, followup["id"])
        assert restored_row.deleted_at is None
        assert restored_row.deleted_by is None
        assert restored_row.deletion_batch_id is None


def test_upload_and_parse_structured_log(client: TestClient):
    _customer, _device, order = create_order_context(client)
    csv_content = (
        b"time,gps_satellites,cell_delta,motor1_rpm,motor2_rpm,esc_error,attitude_error,compass_error,event\n"
        b"0,5,0.10,1000,500,0,2,0,normal\n"
        b"1,6,0.25,1100,500,1,15,1,signal lost\n"
        b"2,7,0.24,1000,480,0,3,0,return to home\n"
    )
    queued = unwrap(client.post(f"/api/flight-logs?repair_order_id={order['id']}", files={"file": ("flight.csv", csv_content, "text/csv")}))
    task = unwrap(client.get(f"/api/tasks/{queued['task_id']}"))
    assert task["status"] == "completed"
    logs = unwrap(client.get(f"/api/flight-logs?repair_order_id={order['id']}"))
    assert logs[0]["parse_status"] == "parsed"
    diagnoses = unwrap(client.get(f"/api/diagnoses?repair_order_id={order['id']}"))
    assert {item["diagnosis_type"] for item in diagnoses} == {"battery", "motor", "esc", "imu", "gps", "compass", "flight_event"}
    recommended = unwrap(client.post(f"/api/orders/{order['id']}/recommended-quote"))
    assert recommended["auto_generated"] is True
    assert recommended["quote"]["status"] == "draft"


def test_dji_binary_log_is_not_misread_as_csv(client: TestClient):
    _customer, _device, order = create_order_context(client)
    queued = unwrap(client.post(
        f"/api/flight-logs?repair_order_id={order['id']}",
        files={"file": ("DJIFlightRecord.txt", b"\x00DJI\x10\x81encrypted-record", "application/octet-stream")},
    ))
    task = unwrap(client.get(f"/api/tasks/{queued['task_id']}"))
    assert task["status"] == "unsupported"
    assert "FlightRecordParsingLib" in task["message"]
    logs = unwrap(client.get(f"/api/flight-logs?repair_order_id={order['id']}"))
    assert logs[0]["parser_name"] == "dji_official_flight_record_v13"
    assert logs[0]["parse_status"] == "unsupported"


def test_dji_gimbal_calibration_capability_and_record(client: TestClient):
    _customer, device, order = create_order_context(client)
    capability = unwrap(client.get(f"/api/calibration/capabilities?device_id={device['id']}"))
    assert capability["recommended_method"] == "official_app_auto_calibration"
    assert capability["desktop_direct_calibration_supported"] is False
    assert capability["research_only"]["private_protocol_tools_enabled"] is False

    record = unwrap(client.post("/api/calibrations", json={
        "repair_order_id": order["id"],
        "device_id": device["id"],
        "calibration_type": "DJI 云台自动标定",
        "tool_name": "DJI Fly",
        "tool_version": "test",
        "status": "completed",
        "result_json": {"after": {"recenter": "passed"}},
        "remarks": "水平与回中复测通过",
    }))
    assert record["result_json"]["mode"] == "official_tool_record"
    assert record["result_json"]["automatic_dji_protocol_used"] is False
    assert record["result_json"]["after"]["recenter"] == "passed"


def test_point_map_reference_library_import_is_idempotent(client: TestClient):
    root = Path(os.environ["POINT_MAP_REFERENCE_ROOT"])
    root.mkdir(parents=True)
    source = root / "DJI Mini 4_WM999_核心板_位号图.pdf"
    pdf = BytesIO()
    source_canvas = canvas.Canvas(pdf, pagesize=(500, 350))
    for page in ("A", "B"):
        source_canvas.setFillGray(0.88)
        source_canvas.rect(100, 60, 300, 230, fill=1)
        source_canvas.setFillGray(0)
        source_canvas.drawString(180, 180, f"POINT MAP {page}")
        source_canvas.showPage()
    source_canvas.save()
    source.write_bytes(pdf.getvalue())

    first = unwrap(client.post("/api/point-maps/import-reference-library"))
    first_task = unwrap(client.get(f"/api/tasks/{first['task_id']}"))
    assert first_task["status"] == "completed"
    assert "新增 2 张" in first_task["message"]
    imported = unwrap(client.get("/api/point-maps?q=WM999"))
    assert len(imported) == 2
    assert {item["map"]["source_page"] for item in imported} == {1, 2}

    second = unwrap(client.post("/api/point-maps/import-reference-library"))
    second_task = unwrap(client.get(f"/api/tasks/{second['task_id']}"))
    assert second_task["status"] == "completed"
    assert "跳过重复 2 张" in second_task["message"]
    assert len(unwrap(client.get("/api/point-maps?q=WM999"))) == 2


def test_damage_sop_point_map_and_assessment_flow(client: TestClient):
    _customer, device, order = create_order_context(client)

    point_map = unwrap(client.post("/api/point-maps", json={
        "brand": "通用",
        "product_category": "数码产品",
        "model_pattern": "*",
        "module_name": "电源输入板",
        "board_code": "GEN-PWR-01",
        "title": "电源输入通用检测点位图",
        "version": "1.0",
        "source_reference": "门店自建示意图",
    }))
    assert point_map["map"]["status"] == "draft"
    source_pdf = BytesIO()
    source_canvas = canvas.Canvas(source_pdf, pagesize=(500, 350))
    source_canvas.setFillGray(0.88)
    source_canvas.rect(180, 70, 260, 180, fill=1)
    source_canvas.setFillGray(0)
    source_canvas.drawString(210, 200, "POINT MAP TEST")
    source_canvas.showPage()
    source_canvas.save()
    image = unwrap(client.post(
        f"/api/point-maps/{point_map['map']['id']}/image",
        files={"file": ("generic-power.pdf", source_pdf.getvalue(), "application/pdf")},
        data={"page_number": "1", "auto_crop": "true"},
    ))
    assert image["image_url"].startswith("/api/files/attachment/")
    assert image["source_file_url"].startswith("/api/files/attachment/")
    assert image["map"]["source_page"] == 1
    marker = unwrap(client.post(f"/api/point-maps/{point_map['map']['id']}/markers", json={
        "marker_code": "TP-VIN",
        "x_percent": "23.500",
        "y_percent": "41.250",
        "label": "主电源输入",
        "component_ref": "J1",
        "function_description": "为整板提供主输入电源并进入保护电路",
        "voltage_spec": "11.5-12.5 V",
        "current_spec": "待机 0.1-0.3 A",
        "marker_type": "measurement",
        "measurement_kind": "直流电压",
        "expected_value": "12",
        "tolerance": "±0.5",
        "unit": "V",
        "probe_hint": "黑表笔接地，红表笔接 TP-VIN",
        "risk_note": "防止探针短接相邻焊盘",
    }))
    unwrap(client.post(f"/api/point-maps/{point_map['map']['id']}/publish"))
    post_publish_marker = unwrap(client.post(f"/api/point-maps/{point_map['map']['id']}/markers", json={
        "marker_code": "TP-GND",
        "x_percent": "30.000",
        "y_percent": "42.000",
        "label": "参考地",
        "function_description": "测量参考地",
        "voltage_spec": "0 V",
        "current_spec": "不适用",
    }))
    assert post_publish_marker["marker_code"] == "TP-GND"
    changed_marker = unwrap(client.patch(
        f"/api/point-maps/{point_map['map']['id']}/markers/{marker['id']}",
        json={
            "function_description": "整板主输入及过流保护前级",
            "voltage_spec": "11.5-12.6 V",
            "current_spec": "待机 0.1-0.3 A，峰值 1.5 A",
        },
    ))
    assert changed_marker["voltage_spec"] == "11.5-12.6 V"
    assert "峰值" in changed_marker["current_spec"]
    marker_search = unwrap(client.get("/api/point-maps?q=峰值"))
    assert [item["map"]["id"] for item in marker_search] == [point_map["map"]["id"]]
    board_search = unwrap(client.get("/api/point-maps?q=GEN-PWR-01"))
    assert [item["map"]["id"] for item in board_search] == [point_map["map"]["id"]]

    template = unwrap(client.post("/api/damage-sop/templates", json={
        "brand": "通用",
        "product_category": "数码产品",
        "model_pattern": "*",
        "title": "数码设备基础定损 SOP",
        "version": "1.0",
        "status": "published",
        "description": "从外观、供电到功能测试的通用流程",
        "source_reference": "门店自建流程",
        "steps": [
            {
                "step_code": "VIS-001", "sort_order": 10, "section": "收机外观",
                "title": "检查外观和进液痕迹", "instruction": "记录磕碰、变形、拆修和进液情况",
                "check_type": "visual", "expected_result": "无异常", "fail_conclusion": "存在外力或进液风险",
            },
            {
                "step_code": "PWR-001", "sort_order": 20, "section": "供电检测",
                "title": "测量主电源输入", "instruction": "按点位图测量主电源输入电压",
                "check_type": "measurement", "expected_result": "12±0.5V",
                "point_map_id": point_map["map"]["id"], "point_marker_id": marker["id"],
                "risk_level": "caution",
            },
        ],
    }))
    assert len(template["steps"]) == 2

    started = unwrap(client.post("/api/damage-assessments", json={
        "repair_order_id": order["id"],
        "template_id": template["template"]["id"],
    }))
    assert started["assessment"]["device_id"] == device["id"]
    assert started["progress"]["percent"] == 0
    assert len(started["results"]) == 2
    assert started["results"][1]["step_snapshot_json"]["point_marker_id"] == marker["id"]

    incomplete = client.post(f"/api/damage-assessments/{started['assessment']['id']}/complete", json={
        "conclusion": "待完成检查",
    })
    assert incomplete.status_code == 409
    for index, result in enumerate(started["results"]):
        updated = unwrap(client.patch(
            f"/api/damage-assessments/{started['assessment']['id']}/results/{result['id']}",
            json={
                "result": "pass" if index == 0 else "fail",
                "measured_value": None if index == 0 else "10.8",
                "unit": None if index == 0 else "V",
                "notes": "已拍照留档" if index == 0 else "输入电压偏低",
            },
        ))
        assert updated["result"] in {"pass", "fail"}
    completed = unwrap(client.post(f"/api/damage-assessments/{started['assessment']['id']}/complete", json={
        "conclusion": "主电源输入偏低，需进一步检查供电路径",
        "responsibility": "待进一步确认",
        "repair_recommendation": "检查连接器、保险和电源管理电路",
        "estimated_cost": "280.00",
    }))
    assert completed["assessment"]["status"] == "completed"
    assert completed["progress"]["failed"] == 1
    immutable = client.post(f"/api/damage-sop/templates/{template['template']['id']}/steps", json={
        "step_code": "EXTRA", "title": "新增步骤", "instruction": "不应允许修改已发布模板",
    })
    assert immutable.status_code == 409


def test_service_gimbal_calibration_lab_is_simulation_only(client: TestClient):
    lab = unwrap(client.get("/api/calibration/lab/profiles"))
    assert lab["engine"] == "service_gimbal_calibration_lab"
    assert lab["stage"] == "simulation_only"
    assert lab["live_execution_available"] is False
    assert {profile["profile_id"] for profile in lab["profiles"]} == {"wm100", "wm230", "wm240"}
    assert all(profile["live_enabled"] is False for profile in lab["profiles"])

    ports = unwrap(client.get("/api/calibration/lab/ports"))
    assert ports["discovery_only"] is True
    assert ports["ports_opened"] is False

    devices = unwrap(client.get("/api/calibration/lab/devices"))
    assert devices["safety"]["discovery_only"] is True
    assert devices["safety"]["hardware_handles_opened"] is False
    assert devices["safety"]["device_commands_sent"] is False
    assert devices["safety"]["usb_control_transfers_sent"] is False
    assert devices["safety"]["network_probes_sent"] is False
    assert devices["safety"]["write_operations_available"] is False

    simulated = unwrap(client.post("/api/calibration/lab/simulate", json={
        "profile_id": "wm100",
        "calibration_kind": "joint_coarse",
    }))
    assert simulated["request_hex"] == "55 0E 04 66 0A 04 00 00 40 04 08 01 BE B2"
    assert simulated["hardware_io_performed"] is False
    assert simulated["events"][-1]["progress"] == 100

    unsupported = client.post("/api/calibration/lab/simulate", json={
        "profile_id": "air3s",
        "calibration_kind": "joint_coarse",
    })
    assert unsupported.status_code == 422


def test_inventory_rejects_negative_stock(client: TestClient):
    item = unwrap(client.post("/api/inventory/items", json={"sku": "EMPTY-001", "name": "空库存", "stock_quantity": "0"}))
    response = client.post("/api/inventory/transactions", json={"inventory_item_id": item["id"], "transaction_type": "stock_out", "quantity": "1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_stock"


def test_quick_entry_pdf_email_and_idempotency(client: TestClient):
    email_config = unwrap(client.put("/api/email/config", json={
        "mode": "mock",
        "sender": "service@example.com",
        "smtp_host": "smtp.feishu.cn",
        "smtp_port": 465,
        "password": "LocalAuthCode123",
        "from_name": "服务中心",
        "use_starttls": True,
        "timeout_seconds": 12,
    }))
    assert email_config["password_configured"] is True
    assert email_config["source"] == "database"
    assert "password" not in email_config
    assert unwrap(client.get("/api/email/config"))["password_configured"] is True

    payload = {
        "customer_name": "快捷客户",
        "phone": "13700000002",
        "email": "quick@example.com",
        "brand": "DJI",
        "model": "Mini 4 Pro",
        "serial_number": "QUICK-SN-001",
        "fault_description": "云台无法回中",
        "intake_accessories": "机身、电池",
        "labor_fee": "120",
        "discount": "20",
        "payment_url": "https://pay.example.com/quick-entry/QUICK-001?source=counter",
        "items": [{"item_name": "云台排线", "quantity": "1", "unit_price": "160", "cost_price": "80"}],
        "generate_pdf": True,
        "send_email": True,
    }
    first = unwrap(client.post("/api/quick-entry", json=payload, headers={"Idempotency-Key": "quick-entry-001"}))
    assert re.fullmatch(r"RO-\d{10}-[0-9A-Z]{4}", first["order"]["order_no"])
    assert len(first["order"]["order_no"]) == 18
    assert first["order"]["order_no"].startswith("RO-")
    assert any(character.isdigit() for character in first["order"]["order_no"][-4:])
    assert any(character.isalpha() for character in first["order"]["order_no"][-4:])
    assert first["quote"]["total_amount"] == "260.00"
    assert first["quote"]["payment_url"] == payload["payment_url"]
    assert first["pdf"]["download_url"].endswith(".pdf")
    assert first["email"]["task_id"]
    deliveries = unwrap(client.get("/api/email/deliveries"))
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "sent"
    assert deliveries[0]["provider"] == "mock"
    repeated = unwrap(client.post("/api/quick-entry", json=payload, headers={"Idempotency-Key": "quick-entry-001"}))
    assert repeated["repeated"] is True
    assert repeated["order"]["id"] == first["order"]["id"]
    assert repeated["quote"]["id"] == first["quote"]["id"]
    assert repeated["quote"]["payment_url"] == payload["payment_url"]
    assert repeated["email"] is None
    assert len(unwrap(client.get("/api/email/deliveries"))) == 1

    mismatched_payload = {**payload, "payment_url": "https://pay.example.com/quick-entry/DIFFERENT"}
    mismatch = client.post(
        "/api/quick-entry",
        json=mismatched_payload,
        headers={"Idempotency-Key": "quick-entry-001"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "idempotency_payload_mismatch"
    assert len(unwrap(client.get("/api/email/deliveries"))) == 1


def test_auth_roles_csrf_and_audit(client: TestClient):
    backup = unwrap(client.post("/api/backups", json={"notes": "自动化验收"}))
    assert backup["status"] == "verified"
    assert len(backup["sha256"]) == 64
    downloaded = client.get(f"/api/backups/{backup['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"SQLite format 3")
    verified = unwrap(client.post(f"/api/backups/{backup['id']}/verify"))
    assert verified["integrity_result"] == "ok"

    viewer = unwrap(client.post("/api/users", json={
        "username": "viewer1", "display_name": "只读同事", "role": "viewer", "password": "ViewerPass123"
    }))
    assert viewer["role"] == "viewer"

    with TestClient(client.app) as viewer_client:
        login = unwrap(viewer_client.post("/api/auth/login", json={"username": "viewer1", "password": GENERATED_PASSWORDS["viewer1"]}))
        assert login["user"]["display_name"] == "只读同事"
        assert viewer_client.get("/api/customers").status_code == 200
        denied = viewer_client.post("/api/customers", json={"name": "不能新增"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "csrf_failed"
        viewer_client.headers.update({"X-CSRF-Token": login["csrf_token"]})
        denied = viewer_client.post("/api/customers", json={"name": "不能新增"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "permission_denied"
        assert viewer_client.get("/api/finance").status_code == 403
        assert viewer_client.get("/api/audit-logs").status_code == 403
        assert viewer_client.get("/api/backups").status_code == 403

    audit_rows = unwrap(client.get("/api/audit-logs"))
    assert any(row["action"] == "POST /api/users" and row["success"] for row in audit_rows)


def test_host_lan_status_and_member_client(client: TestClient):
    status = unwrap(client.get("/api/host/status"))
    assert status["service_status"] == "running"
    assert status["mode"] == "standalone"
    assert status["database_health"] == "ok"
    assert status["access_urls"][0].startswith("http://127.0.0.1:")
    assert any(member["username"] == "admin" for member in status["online_members"])

    changed = unwrap(client.put("/api/host/network", json={"allow_lan": True}))
    assert changed["allow_lan"] is True
    assert changed["restart_required"] is True
    status = unwrap(client.get("/api/host/status"))
    assert status["mode"] == "lan_host"
    assert status["external_tasks_policy"] == "queue_when_offline"

    member_client = client.get("/api/host/member-client/download")
    assert member_client.status_code == 200
    assert "连接管理员主机" in member_client.text
    assert "SQLite" in member_client.text


def test_offline_sync_host_merge_and_conflict_isolation(client: TestClient):
    import uuid

    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.entities import SyncCanonicalRecord
    from app.services.sync import collect_local_changes, payload_hash

    object.__setattr__(settings, "sync_role", "host")
    object.__setattr__(settings, "sync_shared_secret", "test-sync-secret-at-least-24-characters")
    customer = unwrap(client.post("/api/customers", json={
        "name": "离线同步客户",
        "phone": "13500000009",
    }))
    device = unwrap(client.post("/api/devices", json={
        "customer_id": customer["id"],
        "brand": "Apple",
        "model": "iPhone 16",
        "serial_number": "SYNC-DEVICE-001",
    }))
    order = unwrap(client.post("/api/orders", json={
        "customer_id": customer["id"],
        "device_id": device["id"],
        "fault_description": "离线终端收机",
    }))
    quote = unwrap(client.post("/api/quotes", json={
        "repair_order_id": order["id"],
        "items": [{"item_name": "检测服务", "item_type": "service", "unit_price": "50"}],
    }))

    with SessionLocal() as db:
        collected = collect_local_changes(db)
        assert collected["created"] >= 5
        canonical_types = set(db.scalars(select(SyncCanonicalRecord.entity_type)))
        assert {"customer", "device", "repair_order", "service_ticket", "quote"} <= canonical_types
        canonical = db.scalar(select(SyncCanonicalRecord).where(
            SyncCanonicalRecord.entity_type == "customer",
            SyncCanonicalRecord.record_key == customer["customer_no"],
        ))
        assert canonical is not None
        base_revision = canonical.revision
        incoming = dict(canonical.payload_json)
        assert db.scalar(select(SyncCanonicalRecord).where(
            SyncCanonicalRecord.entity_type == "quote",
            SyncCanonicalRecord.record_key == quote["quote_no"],
        )) is not None

    incoming["name"] = "终端修改后的客户"
    accepted = unwrap(client.post(
        "/api/sync/push",
        headers={"X-Sync-Secret": settings.sync_shared_secret},
        json={
            "node_id": str(uuid.uuid4()),
            "events": [{
                "event_id": str(uuid.uuid4()),
                "entity_type": "customer",
                "record_key": customer["customer_no"],
                "operation": "upsert",
                "base_revision": base_revision,
                "base_payload_json": {**incoming, "name": "离线同步客户"},
                "payload_json": incoming,
                "payload_hash": payload_hash(incoming),
            }],
        },
    ))
    assert len(accepted["acknowledgements"]) == 1
    assert accepted["conflicts"] == []
    assert unwrap(client.get(f"/api/customers/{customer['id']}"))["customer"]["name"] == "终端修改后的客户"

    stale = dict(incoming)
    stale["name"] = "另一终端的过期修改"
    rejected = unwrap(client.post(
        "/api/sync/push",
        headers={"X-Sync-Secret": settings.sync_shared_secret},
        json={
            "node_id": str(uuid.uuid4()),
            "events": [{
                "event_id": str(uuid.uuid4()),
                "entity_type": "customer",
                "record_key": customer["customer_no"],
                "operation": "upsert",
                "base_revision": base_revision,
                "base_payload_json": {**incoming, "name": "离线同步客户"},
                "payload_json": stale,
                "payload_hash": payload_hash(stale),
            }],
        },
    ))
    assert rejected["acknowledgements"] == []
    assert len(rejected["conflicts"]) == 1
    assert unwrap(client.get(f"/api/customers/{customer['id']}"))["customer"]["name"] == "终端修改后的客户"
    sync_status = unwrap(client.get("/api/sync/status"))
    assert sync_status["open_conflicts"] == 1
    assert {"inventory_item", "inventory_transaction", "finance_transaction"} <= set(
        sync_status["supported_entities"]
    )
    conflict_id = rejected["conflicts"][0]["conflict_id"]
    resolved = unwrap(client.post(
        f"/api/sync/conflicts/{conflict_id}/resolve",
        json={"resolution": "keep_host"},
    ))
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "keep_host"
    assert unwrap(client.get("/api/sync/status"))["open_conflicts"] == 0
    forced = unwrap(client.get(
        "/api/sync/pull?after=0",
        headers={"X-Sync-Secret": settings.sync_shared_secret},
    ))["changes"]
    assert any(
        change["event_id"] == rejected["conflicts"][0]["event_id"]
        and change["operation"] == "force_host"
        for change in forced
    )


def test_offline_sync_inventory_and_finance_are_recomputed_on_host(client: TestClient):
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.entities import FinanceTransaction, InventoryTransaction, SyncCanonicalRecord
    from app.services.sync import collect_local_changes, payload_hash

    object.__setattr__(settings, "sync_role", "host")
    object.__setattr__(settings, "sync_shared_secret", "test-sync-secret-at-least-24-characters")
    customer, _device, order = create_order_context(client)
    item = unwrap(client.post("/api/inventory/items", json={
        "sku": "SYNC-PART-001",
        "name": "同步测试物料",
        "purchase_price": "10",
        "sale_price": "30",
        "stock_quantity": "5",
    }))
    with SessionLocal() as db:
        collect_local_changes(db)

    node_id = str(uuid.uuid4())
    inventory_event_id = str(uuid.uuid4())
    inventory_payload = {
        "transaction_no": "ST-SYNC-0001",
        "idempotency_key": "terminal-stock-sync-0001",
        "sku": item["sku"],
        "transaction_type": "repair_issue",
        "quantity": "2",
        "before_quantity": "999",
        "after_quantity": "997",
        "unit_cost": "10",
        "order_no": order["order_no"],
        "operator_username": "admin",
        "remarks": "终端离线领料",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    inventory_result = unwrap(client.post(
        "/api/sync/push",
        headers={"X-Sync-Secret": settings.sync_shared_secret},
        json={
            "node_id": node_id,
            "events": [{
                "event_id": inventory_event_id,
                "entity_type": "inventory_transaction",
                "record_key": inventory_payload["transaction_no"],
                "operation": "upsert",
                "base_revision": 0,
                "base_payload_json": None,
                "payload_json": inventory_payload,
                "payload_hash": payload_hash(inventory_payload),
            }],
        },
    ))
    assert len(inventory_result["acknowledgements"]) == 1
    stock_rows = unwrap(client.get("/api/inventory/transactions"))
    stock_tx = next(row for row in stock_rows if row["transaction_no"] == "ST-SYNC-0001")
    assert stock_tx["before_quantity"] == 5
    assert stock_tx["after_quantity"] == 3
    current_item = next(row for row in unwrap(client.get("/api/inventory/items")) if row["id"] == item["id"])
    assert current_item["stock_quantity"] == 3

    finance_event_id = str(uuid.uuid4())
    finance_payload = {
        "transaction_no": "FN-SYNC-0001",
        "idempotency_key": "terminal-finance-sync-0001",
        "order_no": order["order_no"],
        "customer_no": customer["customer_no"],
        "transaction_type": "income",
        "category": "终端维修收款",
        "amount": "100",
        "payment_method": "wechat",
        "paid_at": datetime.now(timezone.utc).isoformat(),
        "description": "终端离线收款",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    finance_result = unwrap(client.post(
        "/api/sync/push",
        headers={"X-Sync-Secret": settings.sync_shared_secret},
        json={
            "node_id": node_id,
            "events": [{
                "event_id": finance_event_id,
                "entity_type": "finance_transaction",
                "record_key": finance_payload["transaction_no"],
                "operation": "upsert",
                "base_revision": 0,
                "base_payload_json": None,
                "payload_json": finance_payload,
                "payload_hash": payload_hash(finance_payload),
            }],
        },
    ))
    assert len(finance_result["acknowledgements"]) == 1
    detail = unwrap(client.get(f"/api/orders/{order['id']}"))["order"]
    assert detail["total_received"] == "100.00"
    assert detail["total_cost"] == "20.00"
    assert detail["gross_profit"] == "80.00"

    duplicate = unwrap(client.post(
        "/api/sync/push",
        headers={"X-Sync-Secret": settings.sync_shared_secret},
        json={
            "node_id": node_id,
            "events": [{
                "event_id": inventory_event_id,
                "entity_type": "inventory_transaction",
                "record_key": inventory_payload["transaction_no"],
                "operation": "upsert",
                "base_revision": 0,
                "base_payload_json": None,
                "payload_json": inventory_payload,
                "payload_hash": payload_hash(inventory_payload),
            }],
        },
    ))
    assert duplicate["acknowledgements"][0]["duplicate"] is True
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 1
        assert db.scalar(select(func.count()).select_from(FinanceTransaction)) == 1
        item_record = db.scalar(select(SyncCanonicalRecord).where(
            SyncCanonicalRecord.entity_type == "inventory_item",
            SyncCanonicalRecord.record_key == item["sku"],
        ))
        assert item_record.payload_json["stock_quantity"] == "3"


def test_operations_center_procurement_stocktake_and_reports(client: TestClient):
    customer, _device, order = create_order_context(client)
    supplier = unwrap(client.post("/api/suppliers", json={
        "name": "测试供应商", "contact": "采购联系人", "phone": "13800000000", "enabled": True,
    }))
    item = unwrap(client.post("/api/inventory/items", json={
        "sku": "OPS-PART-001", "name": "测试维修物料", "unit": "件",
        "purchase_price": "10", "sale_price": "25", "stock_quantity": "0", "safety_stock": "1",
    }))
    purchase = unwrap(client.post("/api/purchase-orders", json={
        "supplier_id": supplier["id"],
        "items": [{"inventory_item_id": item["id"], "quantity": "5", "unit_cost": "12.50"}],
        "notes": "采购闭环测试",
    }))
    assert purchase["status"] == "ordered"
    assert float(purchase["payable_amount"]) == 62.50

    received = unwrap(client.post(f"/api/purchase-orders/{purchase['id']}/receive", json={
        "lines": [{
            "purchase_order_item_id": purchase["items"][0]["id"],
            "quantity": "2", "lot_no": "OPS-LOT-001", "serial_numbers": ["OPS-SN-1", "OPS-SN-2"],
        }],
    }))
    assert received["order"]["status"] == "partially_received"
    paid = unwrap(client.post(f"/api/purchase-orders/{purchase['id']}/pay", json={
        "amount": "20", "payment_method": "bank", "description": "采购付款测试",
    }))
    assert float(paid["order"]["paid_amount"]) == 20.00
    assert float(paid["order"]["payable_amount"]) == 42.50

    stocktake = unwrap(client.post("/api/stocktakes", json={
        "notes": "全库盘点测试",
        "items": [{"inventory_item_id": item["id"], "counted_quantity": "3"}],
    }))
    committed = unwrap(client.post(f"/api/stocktakes/{stocktake['id']}/commit"))
    assert committed["stocktake"]["status"] == "committed"
    inventory = next(row for row in unwrap(client.get("/api/inventory/items")) if row["id"] == item["id"])
    assert inventory["stock_quantity"] == 3
    transaction_types = {row["transaction_type"] for row in unwrap(client.get("/api/inventory/transactions"))}
    assert {"purchase_in", "stocktake_adjustment"}.issubset(transaction_types)

    work = unwrap(client.get("/api/work-center?view=all"))
    assert any(row["kind"] == "repair_order" and row["id"] == order["id"] for row in work["items"])
    timeline = unwrap(client.get(f"/api/customers/{customer['id']}/timeline"))
    assert any(row["event_type"] == "order_status" for row in timeline["events"])
    report = unwrap(client.get("/api/analytics/operations?days=30"))
    assert float(report["totals"]["expense"]) == 20.00
    assert report["totals"]["orders"] == 1

    schedule = unwrap(client.put("/api/backups/schedule", json={
        "enabled": True, "time": "03:10", "retention_days": 14, "keep_count": 7,
    }))
    assert schedule["enabled"] is True
    assert schedule["time"] == "03:10"
