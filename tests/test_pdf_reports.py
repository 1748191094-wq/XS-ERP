from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest
from reportlab.platypus import Paragraph, Table

from app.reports.pdf import PdfReportService


def test_default_pdf_embeds_chinese_without_host_fonts(tmp_path, monkeypatch):
    """A font-less PDF reader must receive glyph outlines, not just CID names."""
    from pathlib import Path
    from types import SimpleNamespace

    import pypdfium2 as pdfium
    from reportlab.pdfbase import pdfmetrics
    from reportlab.lib.styles import ParagraphStyle
    from app.reports import pdf as reports

    monkeypatch.setattr(reports, "settings", SimpleNamespace(pdf_font_path=""))
    name, source = reports._discover_font()
    assert Path(source) == reports.RESOURCE_DIR / "app/reports/fonts/wqy-microhei.ttc"
    sample = "中文检测报告维修电压异常Ω℃μ¥"
    face = pdfmetrics.getFont(name).face
    assert all(face.charToGlyph.get(ord(char), 0) != 0 for char in sample)

    from reportlab.platypus import SimpleDocTemplate
    path = tmp_path / "embedded-chinese.pdf"
    SimpleDocTemplate(str(path)).build([
        Paragraph(sample, ParagraphStyle("Chinese", fontName=name, fontSize=18, leading=24))
    ])
    assert b"/FontFile2" in path.read_bytes()
    document = pdfium.PdfDocument(path)
    try:
        page = document[0]
        text = page.get_textpage()
        try:
            assert sample in text.get_text_range()
        finally:
            text.close()
        bitmap = page.render(scale=1)
        try:
            # Chinese-only ink must be present even with no CJK fonts installed.
            rendered = bitmap.to_pil().convert("L")
            assert sum(count for shade, count in enumerate(rendered.histogram()) if shade < 128) > 900
        finally:
            bitmap.close()
            page.close()
    finally:
        document.close()


def test_invalid_custom_pdf_font_falls_back_to_bundled_font(tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace
    from app.reports import pdf as reports

    invalid = tmp_path / "invalid.ttf"
    invalid.write_bytes(b"not a font")
    monkeypatch.setattr(reports, "settings", SimpleNamespace(pdf_font_path=str(invalid)))
    _name, source = reports._discover_font()
    assert source.endswith("app/reports/fonts/wqy-microhei.ttc")
    assert "Cannot embed PDF font" in caplog.text


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


@pytest.mark.parametrize("size", [(900, 600), (300, 1200), (1200, 100)])
def test_assessment_evidence_keeps_identity_on_its_page(tmp_path, monkeypatch, size):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    from PIL import Image
    from reportlab.lib.units import mm
    from app.reports import pdf as reports
    from app.storage.local import LocalStorageService

    image_path = tmp_path / "evidence.png"
    Image.new("RGB", size, "#b54949").save(image_path)
    monkeypatch.setattr(reports, "LocalStorageService", lambda: LocalStorageService(tmp_path))
    order = _order()
    order.order_no = "RO-EVIDENCE-QA"
    order.fault_description = "开机异常，需检测。"
    order.intake_condition = "外壳有划痕"
    order.intake_accessories = "机身、电池"
    order.customer_notes = "请确认后安排维修。"
    assessment = NS(
        assessment_no="DA-QA", conclusion="输入电压异常", responsibility="待确认",
        repair_recommendation="排查供电模块",
        results=[NS(
            step_code="PWR-040", step_snapshot_json={"title": "输入电压 <复测>"},
            result="fail", measured_value="10.8", unit="V",
            notes="客户可见复测记录，供电异常。\n" * 50,
            evidence_attachment_id=1,
        )],
    )
    path = PdfReportService(tmp_path).repair_report(
        order, assessment=assessment,
        evidence={1: NS(id=1, storage_path=image_path.name)},
    )
    document = pdfium.PdfDocument(path)
    try:
        assert len(document) >= 3  # Deliberately force notes across page boundaries.
        image_count = 0
        for index in range(len(document)):
            page = document[index]
            text = page.get_textpage()
            try:
                content = text.get_text_bounded()
                images = list(page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]))
                if images:
                    assert "异常证据：输入电压 <复测>（PWR-040）" in content
                    assert len(images) == 1
                    matrix = images[0].get_matrix()
                    assert abs(matrix.a) <= 90 * mm + 0.01
                    assert abs(matrix.d) <= 70 * mm + 0.01
                    assert abs(matrix.a / matrix.d - size[0] / size[1]) < 0.03
                    image_count += 1
                elif "异常证据：" in content:
                    pytest.fail("Evidence caption was stranded on a page without its image")
            finally:
                text.close()
                page.close()
        assert image_count == 1
    finally:
        document.close()


@pytest.mark.parametrize("line_count", [1, 100])
def test_quote_theme_starts_on_first_page_and_can_span_pages(tmp_path, line_count):
    import pypdfium2 as pdfium

    order = _order()
    order.fault_description = "\n".join(
        f"问题记录 {index:03d}：云台无法回中 <复测> & 待确认。"
        for index in range(line_count)
    )
    order.customer_notes = "请确认维修范围。"
    quote = NS(
        quote_no="QT-THEME-QA", version=1, status="draft",
        created_at=datetime.now(timezone.utc), repair_order=order,
        items=[], subtotal=Decimal("0"), labor_fee=Decimal("0"),
        shipping_fee=Decimal("0"), discount=Decimal("0"), total_amount=Decimal("0"),
    )
    path = PdfReportService(tmp_path).quote(quote)
    document = pdfium.PdfDocument(path)
    try:
        texts = []
        for index in range(len(document)):
            page = document[index]
            text = page.get_textpage()
            try:
                texts.append(text.get_text_bounded())
            finally:
                text.close()
                page.close()
        assert "服务主题" in texts[0]
        assert "问题记录 000" in texts[0]
        combined = "\n".join(texts)
        for index in range(line_count):
            assert combined.count(f"问题记录 {index:03d}：") == 1
        assert combined.count("<复测> & 待确认。") == line_count
        assert "qa@example.com" in texts[0]
        assert "定损与维修说明" in combined
        assert "总计费用" in combined
        if line_count == 100:
            assert len(document) >= 3
    finally:
        document.close()


@pytest.mark.parametrize('item_count', list(range(36)) + [80])
def test_quote_summary_rows_stay_on_same_page(tmp_path, item_count):
    import pypdfium2 as pdfium

    order = _order()
    order.fault_description = '供电检查'
    order.customer_notes = ''
    quote = NS(
        quote_no=f'QT-SUMMARY-{item_count}', version=1, status='draft',
        created_at=datetime.now(timezone.utc), repair_order=order,
        items=[NS(item_name=f'检查项目 {i:03d}', item_type='part',
                  quantity=1, unit_price=Decimal('100'), amount=Decimal('100'))
               for i in range(item_count)],
        subtotal=Decimal(item_count * 100), labor_fee=Decimal('50'),
        shipping_fee=Decimal('20'), discount=Decimal('10'),
        total_amount=Decimal(item_count * 100 + 60),
    )
    path = PdfReportService(tmp_path).quote(quote)
    texts = []
    document = pdfium.PdfDocument(path)
    try:
        for page in document:
            text = page.get_textpage()
            try:
                texts.append(text.get_text_bounded())
            finally:
                text.close()
                page.close()
    finally:
        document.close()
    summary_pages = [i for i, text in enumerate(texts) if '配件 / 项目小计' in text]
    total_pages = [i for i, text in enumerate(texts) if '总计费用' in text]
    assert summary_pages == total_pages and len(summary_pages) == 1
    summary = texts[summary_pages[0]]
    assert '人工服务费' in summary and '运费' in summary and '优惠' in summary
    assert f'{quote.total_amount:,.2f}' in summary
    for i in range(item_count):
        assert '\n'.join(texts).count(f'检查项目 {i:03d}') == 1


@pytest.mark.parametrize('repair_status,repair_label,quote_status,quote_label', [
    ('pending_inspection', '待检测', 'draft', '草稿'),
    ('inspecting', '检测中', 'sent', '已发送'),
    ('pending_quote', '待报价', 'confirmed', '已确认'),
    ('quoted', '已报价', 'rejected', '已拒绝'),
    ('customer_confirmed', '客户已确认', 'superseded', '历史版本'),
    ('repairing', '维修中', 'draft', '草稿'),
    ('pending_test', '待测试', 'sent', '已发送'),
    ('pending_shipping', '待发货', 'confirmed', '已确认'),
    ('completed', '已完成', 'rejected', '已拒绝'),
    ('cancelled', '已取消', 'superseded', '历史版本'),
    ('legacy_<state>&', 'legacy_<state>&', 'legacy_<quote>&', 'legacy_<quote>&'),
])
def test_customer_pdf_status_labels(tmp_path, repair_status, repair_label, quote_status, quote_label):
    import pypdfium2 as pdfium
    order = _order()
    order.status = repair_status
    order.fault_description = '检测供电'
    order.customer_notes = ''
    quote = NS(quote_no='QT-STATUS', version=1, status=quote_status,
               created_at=datetime.now(timezone.utc), repair_order=order,
               items=[], subtotal=0, labor_fee=0, shipping_fee=0, discount=0, total_amount=0)
    order.quotes = [quote]
    service = PdfReportService(tmp_path)
    for path in (service.quote(quote), service.repair_report(order),
                 service.repair_report(order, completed=True)):
        doc = pdfium.PdfDocument(path)
        page = doc[0]
        text = page.get_textpage()
        try:
            content = text.get_text_bounded()
            assert quote_label in content
            if '当前状态' in content:
                assert repair_label in content
                if repair_label != repair_status:
                    assert repair_status not in content
            if quote_label != quote_status:
                assert quote_status not in content
        finally:
            text.close()
            page.close()
            doc.close()


def test_pdf_status_mappings_cover_domain_enums():
    from app.core.enums import QuoteStatus, RepairOrderStatus
    from app.reports.pdf import QUOTE_STATUS_LABELS, REPAIR_STATUS_LABELS, _status_label
    assert set(QUOTE_STATUS_LABELS) == set(QuoteStatus)
    assert set(REPAIR_STATUS_LABELS) == set(RepairOrderStatus)
    assert _status_label(None, QUOTE_STATUS_LABELS) == '未记录'


@pytest.mark.parametrize('note_lines', [0, 5, 10, 15, 20, 25, 30, 35, 40])
def test_service_report_notice_stays_with_final_status(tmp_path, note_lines):
    import pypdfium2 as pdfium
    now = datetime.now(timezone.utc)
    order = _order()
    ticket = NS(ticket_no='TKT-NOTICE', ticket_type='technical_support',
                title='连接检查', description='检查连接状态', status='closed',
                priority='normal', customer=order.customer, device=order.device,
                current_owner=None, processing_group=None, created_at=now,
                due_at=now, first_response_at=now, resolved_at=now, closed_at=now)
    notes = [NS(created_at=now, content='\n'.join(
        f'记录 {i:03d}：已复查连接。' for i in range(note_lines)))] if note_lines else []
    path = PdfReportService(tmp_path).service_ticket(ticket, customer_notes=notes)
    doc = pdfium.PdfDocument(path)
    texts = []
    try:
        for page in doc:
            text = page.get_textpage()
            try:
                texts.append(text.get_text_bounded())
            finally:
                text.close()
                page.close()
    finally:
        doc.close()
    assert '本报告仅包含客户可见的服务信息' in texts[-1]
    assert '处理状态' in texts[-1] and '关闭时间' in texts[-1]
    assert sum(t.count('本报告仅包含客户可见的服务信息') for t in texts) == 1
    for i in range(note_lines):
        assert '\n'.join(texts).count(f'记录 {i:03d}：') == 1
