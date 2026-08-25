from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest
from reportlab.platypus import Paragraph, Table

from app.reports.pdf import PdfReportService


def _story_text(value) -> str:
    if isinstance(value, Paragraph):
        return value.getPlainText()
    if isinstance(value, Table):
        return " ".join(_story_text(cell) for row in value._cellvalues for cell in row)
    if isinstance(value, (list, tuple)):
        return " ".join(_story_text(item) for item in value)
    return ""


def _order() -> NS:
    customer = NS(name="客户 <测试> & 合作方", phone="13800000000", email="qa@example.com")
    device = NS(
        brand="DJI",
        model="Mavic 3 Pro 超长设备型号用于测试自动换行",
        serial_number="SN-LONG-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    )
    return NS(
        order_no="RO-20260719-VERY-LONG-NUMBER-0123456789",
        customer=customer,
        device=device,
        fault_description=("云台无法回中 <b>这不是标签</b> & 开机抖动。\n" * 20),
        intake_condition=("机身有使用痕迹。" * 20),
        intake_accessories=("机身、电池、遥控器、数据线。" * 20),
        internal_notes=("人工检测与复测结果。\n" * 40),
        customer_notes=("客户可见备注，特殊字符 <>& 必须安全显示。" * 20),
        status="inspecting",
        priority="high",
        received_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        total_quote_amount=Decimal("12345.67"),
        total_received=Decimal("2345.67"),
        total_cost=Decimal("1000.00"),
        gross_profit=Decimal("1345.67"),
    )


def test_pdf_reports_support_long_text_and_multipage_tables(tmp_path):
    order = _order()
    items = [
        NS(
            item_name=f"{index:02d} 超长维修项目 <{index}> & 输入转义验证",
            item_type="part",
            quantity=Decimal("1"),
            unit_price=Decimal("123.45"),
            amount=Decimal("123.45"),
        )
        for index in range(1, 81)
    ]
    quote = NS(
        quote_no="QT-20260719-VERY-LONG-0123456789",
        version=7,
        status="draft",
        created_at=datetime.now(timezone.utc),
        repair_order=order,
        items=items,
        subtotal=Decimal("9876.00"),
        labor_fee=Decimal("500.00"),
        shipping_fee=Decimal("20.00"),
        discount=Decimal("27.00"),
        total_amount=Decimal("10369.00"),
    )
    ticket = NS(
        ticket_no="TKT-20260719-VERY-LONG-0123456789",
        ticket_type="technical_support",
        title="技术支持反馈 <请勿解析为标签> & 长标题" * 5,
        description=("客户反馈设备连接异常，已完成人工检查。\n" * 35),
        status="in_progress",
        priority="high",
        customer=order.customer,
        device=order.device,
        current_owner=NS(display_name="技术支持专员"),
        processing_group=NS(name="技术支持组"),
        created_at=datetime.now(timezone.utc),
        due_at=datetime.now(timezone.utc),
        first_response_at=datetime.now(timezone.utc),
        resolved_at=None,
        closed_at=None,
    )
    customer_notes = [
        NS(created_at=datetime.now(timezone.utc), content=f"客户可见进展 {index}：<>& 已安全转义。" * 8)
        for index in range(1, 16)
    ]
    service = PdfReportService(tmp_path)

    paths = [
        service.quote(quote),
        service.repair_report(order),
        service.repair_report(order, completed=True),
        service.service_ticket(ticket, customer_notes=customer_notes),
    ]

    for path in paths:
        assert path.read_bytes().startswith(b"%PDF")
        assert path.stat().st_size > 1000


def test_pdf_failure_does_not_replace_existing_file(tmp_path):
    service = PdfReportService(tmp_path)
    destination = tmp_path / "existing.pdf"
    destination.write_bytes(b"existing-pdf-must-survive")

    with pytest.raises(Exception):
        service._atomic_build(
            destination,
            [object()],
            "invalid story",
            report_kind="QA",
            document_no="QA-1",
        )

    assert destination.read_bytes() == b"existing-pdf-must-survive"
    assert not list(tmp_path.glob(".existing-*.pdf.tmp"))


def test_customer_facing_quote_and_reports_never_include_cost_or_profit(tmp_path):
    order = _order()
    quote = NS(
        quote_no="QT-PRIVACY-001",
        version=1,
        status="draft",
        created_at=datetime.now(timezone.utc),
        repair_order=order,
        service_ticket=None,
        items=[NS(
            item_name="主板维修服务",
            item_type="service",
            quantity=Decimal("1"),
            unit_price=Decimal("888.00"),
            cost_price=Decimal("777.77"),
            amount=Decimal("888.00"),
        )],
        subtotal=Decimal("888.00"),
        labor_fee=Decimal("100.00"),
        shipping_fee=Decimal("0.00"),
        discount=Decimal("0.00"),
        total_amount=Decimal("988.00"),
        assessment_result="主摄像头模组外力损坏，无法正常成像。",
        assessment_responsibility="外力损坏",
        repair_recommendation="更换主摄像头模组并完成云台复测。",
        customer_notice="维修前请确认已完成数据备份。",
    )
    captured: list[list] = []
    service = PdfReportService(tmp_path)

    def capture(_destination, story, _title, **_kwargs):
        captured.append(story)
        return tmp_path / "captured.pdf"

    service._atomic_build = capture
    service.quote(quote)
    service.repair_report(order)
    service.repair_report(order, completed=True)

    assert len(captured) == 3
    for story in captured:
        text = _story_text(story)
        assert "成本" not in text
        assert "利润" not in text
        assert "777.77" not in text
        assert "1,000.00" not in text
        assert "1,345.67" not in text

    repair_text = _story_text(captured[1])
    assert "报价金额" in repair_text
    assert "已收款" in repair_text
    assert "待付金额" in repair_text
    assert "维修项目与费用" in repair_text
    assert "消耗物料 / 服务项目" in repair_text
    quote_text = _story_text(captured[0])
    assert "定损与维修说明" in quote_text
    assert "主摄像头模组外力损坏" in quote_text
    assert "更换主摄像头模组" in quote_text


def test_quote_uses_service_ticket_title_as_service_theme(tmp_path):
    order = _order()
    ticket = NS(
        ticket_no="TKT-TITLE-001",
        ticket_type="retail",
        title="标题应出现在服务主题",
        description="这里是详细问题描述，不应冒充服务主题",
        customer=order.customer,
        device=order.device,
    )
    quote = NS(
        quote_no="QT-TITLE-001",
        version=1,
        status="draft",
        created_at=datetime.now(timezone.utc),
        repair_order=None,
        service_ticket=ticket,
        items=[],
        subtotal=Decimal("0.00"),
        labor_fee=Decimal("0.00"),
        shipping_fee=Decimal("0.00"),
        discount=Decimal("0.00"),
        total_amount=Decimal("0.00"),
    )
    captured: list[list] = []
    service = PdfReportService(tmp_path)
    service._atomic_build = lambda _destination, story, _title, **_kwargs: captured.append(story) or tmp_path / "captured.pdf"

    service.quote(quote)

    text = _story_text(captured[0])
    assert "标题应出现在服务主题" in text
    assert "这里是详细问题描述" not in text


def test_replacement_quote_pdf_freezes_discount_once_and_shows_business_context(tmp_path):
    order = _order()
    ticket = NS(
        ticket_no="TKT-REPLACEMENT-QUOTE-001",
        ticket_type="replacement",
        title="旧机抵折置换新机",
        description="内部详细描述不应替代服务主题",
        customer=order.customer,
        device=order.device,
        replacement_inspection_result="外观轻微划痕，飞行与图传正常",
        trade_in_credit=Decimal("1688.50"),
        return_reference="线下交易：门店交接 SRV-OFFLINE-001",
        outbound_to_customer_tracking_no="SF-REPLACEMENT-OUT-001",
    )
    quote = NS(
        quote_no="QT-REPLACEMENT-001",
        version=2,
        status="draft",
        created_at=datetime.now(timezone.utc),
        repair_order=None,
        service_ticket=ticket,
        items=[NS(
            item_name="置换新机方案",
            item_type="part",
            quantity=Decimal("1"),
            unit_price=Decimal("5000.00"),
            amount=Decimal("5000.00"),
        )],
        subtotal=Decimal("5000.00"),
        labor_fee=Decimal("200.00"),
        shipping_fee=Decimal("50.00"),
        discount=Decimal("1688.50"),
        total_amount=Decimal("3561.50"),
        payment_url="https://pay.example.com/replacement/QT-REPLACEMENT-001",
    )
    captured: list[list] = []
    service = PdfReportService(tmp_path)
    service._atomic_build = lambda _destination, story, _title, **_kwargs: captured.append(story) or tmp_path / "captured.pdf"

    service.quote(quote)

    text = _story_text(captured[0])
    for expected in (
        "置换服务报价单",
        "置换业务信息",
        "外观轻微划痕，飞行与图传正常",
        "线下交易：门店交接 SRV-OFFLINE-001",
        "SF-REPLACEMENT-OUT-001",
        "旧机抵折 / 优惠",
        "1,688.50",
        "3,561.50",
        "置换方案、旧机抵折或其他费用",
    ):
        assert expected in text
