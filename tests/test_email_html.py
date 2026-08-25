from pathlib import Path

from app.integrations.email.service import (
    EmailPayload,
    MessagePayload,
    _generic_html_body,
    _html_body,
)
from app.services.email_config import EmailRuntimeConfig
from app.services.email_templates import (
    SERVICE_RESPONSIBILITY_MARKER,
    responsibility_kind_for_context,
    with_responsibility_snapshot,
)


def _config() -> EmailRuntimeConfig:
    return EmailRuntimeConfig(
        mode="mock",
        sender="service@example.com",
        smtp_host="",
        smtp_port=465,
        password="",
        from_name="服务中心",
        reply_to="",
        use_starttls=True,
        timeout_seconds=12,
        source="test",
    )


def test_technical_support_html_is_apple_styled_and_escaped(tmp_path: Path):
    long_feedback = "技术支持反馈" * 800
    payload = MessagePayload(
        recipients=["customer@example.com"],
        cc=[],
        bcc=[],
        subject="技术支持 <通知>",
        body_text=f"您好：\n\n反馈 <script>alert(1)</script> & 已处理。\n\n{long_feedback}",
        attachments=[tmp_path / "service.pdf"],
        template_type="technical_support",
    )

    rendered = _generic_html_body(payload, _config())

    assert "技术支持" in rendered
    assert "#007aff" in rendered
    assert "#f5f5f7" in rendered
    assert "已冻结的业务附件" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert long_feedback in rendered
    assert "overflow-wrap:anywhere" in rendered
    assert "table-layout:fixed" in rendered
    assert "SF Pro" not in rendered


def test_quote_html_uses_same_visual_shell_and_escapes_values(tmp_path: Path):
    payload = EmailPayload(
        recipient="customer@example.com",
        subject="报价 <确认>",
        customer_name="客户 & 合作方",
        order_no="RO-1",
        quote_no="QT-1",
        device_name="设备 <测试>",
        serial_number="SN-1",
        total_amount="123.45",
        attachment_path=tmp_path / "quote.pdf",
        message="人工备注 <不可作为标签>",
    )

    rendered = _html_body(payload, _config())

    assert "报价通知" in rendered
    assert "¥ 123.45" in rendered
    assert "#007aff" in rendered
    assert "客户 &amp; 合作方" in rendered
    assert "设备 &lt;测试&gt;" in rendered
    assert "人工备注 &lt;不可作为标签&gt;" in rendered


def test_status_email_html_has_progress_and_canonical_responsibility_notice():
    payload = MessagePayload(
        recipients=["customer@example.com"],
        cc=[],
        bcc=[],
        subject="维修中心收件通知",
        body_text=with_responsibility_snapshot(
            "尊敬的客户：\n您好！\n\n<script>不应作为标签</script>",
            "intake",
        ),
        attachments=[],
        template_type="intake",
    )

    rendered = _generic_html_body(payload, _config())

    assert "服务进度 1 / 7" in rendered
    assert "维修中心已收件" in rendered
    assert "服务责任与重要说明" in rendered
    assert "不排除或限制您依据适用法律享有的权利" in rendered
    assert "<script>不应作为标签</script>" not in rendered
    assert "&lt;script&gt;不应作为标签&lt;/script&gt;" in rendered


def test_non_repair_service_email_uses_general_service_notice_without_repair_wording():
    snapshot = with_responsibility_snapshot(
        "尊敬的客户：\n您好！",
        "intake",
        responsibility_kind="service",
    )
    payload = MessagePayload(
        recipients=["customer@example.com"],
        cc=[],
        bcc=[],
        subject="客户咨询进度通知",
        body_text=snapshot,
        attachments=[],
        template_type="intake",
    )

    rendered = _generic_html_body(payload, _config())

    assert SERVICE_RESPONSIBILITY_MARKER in snapshot
    assert "服务范围与权益说明" in rendered
    assert "咨询、报价、零售、置换、投诉处理、技术支持" in rendered
    assert "检测与维修授权" not in snapshot
    assert "维修、换件" not in snapshot


def test_switching_responsibility_profile_cannot_leave_duplicate_notice():
    repair_snapshot = with_responsibility_snapshot("正文", "intake")
    service_snapshot = with_responsibility_snapshot(
        repair_snapshot,
        "intake",
        responsibility_kind="service",
    )

    assert service_snapshot.count(SERVICE_RESPONSIBILITY_MARKER) == 1
    assert "—— 服务责任与重要说明 ——" not in service_snapshot


def test_every_non_repair_ticket_type_selects_general_service_notice():
    non_repair_types = (
        "consultation",
        "quote_followup",
        "after_sales_followup",
        "complaint",
        "logistics_exception",
        "technical_support",
        "specialist_assistance",
        "retail",
        "replacement",
    )

    assert all(
        responsibility_kind_for_context(ticket_type=ticket_type, has_repair_order=True) == "service"
        for ticket_type in non_repair_types
    )
    assert responsibility_kind_for_context(ticket_type="repair", has_repair_order=True) == "repair"
