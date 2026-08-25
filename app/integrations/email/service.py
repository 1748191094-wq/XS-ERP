from __future__ import annotations

import html
import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from app.services.email_config import EmailRuntimeConfig, load_email_config, safe_email_config
from app.services.email_templates import (
    STATUS_EMAIL_STAGES,
    responsibility_kind_from_snapshot,
    responsibility_notice,
    strip_responsibility_snapshot,
)


@dataclass(slots=True)
class EmailPayload:
    recipient: str
    subject: str
    customer_name: str
    order_no: str
    quote_no: str
    device_name: str
    serial_number: str
    total_amount: str
    attachment_path: Path
    message: str | None = None


@dataclass(slots=True)
class MessagePayload:
    recipients: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body_text: str
    attachments: list[Path]
    template_type: str = "general"


@dataclass(slots=True)
class EmailResult:
    success: bool
    provider: str
    message: str


class EmailService(Protocol):
    def send_quote(self, payload: EmailPayload) -> EmailResult: ...
    def send_message(self, payload: MessagePayload) -> EmailResult: ...


def _text_to_html(value: str) -> str:
    """Turn an editable plain-text snapshot into safe email paragraphs."""
    escaped = html.escape(value or "", quote=True).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = escaped.split("\n\n")
    return "".join(
        f'<p style="margin:0 0 18px;line-height:1.75;color:#1d1d1f;word-break:break-word;overflow-wrap:anywhere">{part.replace(chr(10), "<br>") or "&nbsp;"}</p>'
        for part in paragraphs
    )


def _status_progress_html(template_type: str) -> str:
    stage = STATUS_EMAIL_STAGES.get(template_type)
    if not stage:
        return ""
    progress = max(0, min(100, round(stage["index"] / stage["total"] * 100)))
    return f"""<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 22px;border:1px solid #cfe5ff;border-radius:16px;background:#f5f9ff;overflow:hidden">
<tr><td style="padding:16px 18px 10px;color:#0066cc;font-size:12px;font-weight:700;letter-spacing:.2px">服务进度 {stage['index']} / {stage['total']}</td></tr>
<tr><td style="padding:0 18px 5px;color:#1d1d1f;font-size:18px;font-weight:700;line-height:1.35">{html.escape(stage['label'])}</td></tr>
<tr><td style="padding:0 18px 14px;color:#6e6e73;font-size:13px;line-height:1.65">{html.escape(stage['summary'])}</td></tr>
<tr><td style="padding:0 18px 18px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
<td style="height:6px;border-radius:999px;background:#dcecff;font-size:0;line-height:0"><div style="width:{progress}%;height:6px;border-radius:999px;background:#007aff">&nbsp;</div></td>
</tr></table></td></tr></table>"""


def _responsibility_html(kind: str) -> str:
    notice = responsibility_notice(kind)
    items = notice["items"]
    rows = "".join(
        f"""<tr><td style="padding:{'12px' if index == 1 else '10px'} 14px;border-bottom:{'1px solid #e5e5ea' if index < len(items) else '0'};vertical-align:top">
<strong style="display:block;margin:0 0 4px;color:#1d1d1f;font-size:13px">{index}. {html.escape(title)}</strong>
<span style="display:block;color:#6e6e73;font-size:12px;line-height:1.7">{html.escape(body)}</span>
</td></tr>"""
        for index, (title, body) in enumerate(items, start=1)
    )
    return f"""<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:24px 0 8px;border:1px solid #e5e5ea;border-radius:16px;background:#fbfbfd;overflow:hidden">
<tr><td style="padding:15px 14px 11px;border-bottom:1px solid #e5e5ea">
<strong style="display:block;color:#1d1d1f;font-size:15px">{html.escape(notice['title'])}</strong>
<span style="display:block;margin-top:4px;color:#8e8e93;font-size:11px;line-height:1.55">{html.escape(notice['summary'])}</span>
</td></tr>{rows}</table>"""


def _email_shell(*, config: EmailRuntimeConfig, subject: str, label: str, content: str, attachment_count: int = 0) -> str:
    esc = lambda value: html.escape(str(value or ""), quote=True)
    attachment = ""
    if attachment_count:
        attachment = f"""<tr><td style="padding:0 34px 24px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e5e5ea;border-radius:14px;background:#f5f5f7"><tr>
<td style="width:34px;padding:13px 0 13px 15px;color:#007aff;font-size:20px">&#x2318;</td>
<td style="padding:13px 15px;color:#1d1d1f;font-size:13px"><strong>邮件附件</strong><br><span style="color:#6e6e73;font-size:12px">本邮件包含 {attachment_count} 个已冻结的业务附件</span></td>
</tr></table></td></tr>"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f7;color:#1d1d1f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;-webkit-font-smoothing:antialiased">
<div style="display:none;max-height:0;overflow:hidden;color:transparent">{esc(subject)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f5f7"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:660px;table-layout:fixed;border:1px solid #e5e5ea;border-radius:22px;background:#ffffff;overflow:hidden">
<tr><td style="height:5px;background:#007aff;font-size:0;line-height:0">&nbsp;</td></tr>
<tr><td style="padding:26px 34px 18px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
<td style="color:#1d1d1f;font-size:17px;font-weight:700;letter-spacing:-.2px">{esc(config.from_name)}</td>
<td align="right"><span style="display:inline-block;padding:5px 10px;border-radius:999px;background:#eef6ff;color:#0066cc;font-size:11px;font-weight:700">{esc(label)}</span></td>
</tr></table></td></tr>
<tr><td style="padding:4px 34px 24px"><h1 style="margin:0;color:#1d1d1f;font-size:26px;line-height:1.28;letter-spacing:-.6px;font-weight:700">{esc(subject)}</h1></td></tr>
<tr><td style="padding:0 34px 8px"><div style="height:1px;background:#e5e5ea"></div></td></tr>
<tr><td style="padding:24px 34px 12px;font-size:15px;word-break:break-word;overflow-wrap:anywhere">{content}</td></tr>
{attachment}
<tr><td style="padding:20px 34px 26px;border-top:1px solid #e5e5ea;color:#8e8e93;font-size:11px;line-height:1.65">
此邮件由 {esc(config.from_name)} 的服务系统生成。您可以直接回复本邮件联系服务人员。<br>{esc(config.from_name)} · Digital Service
</td></tr></table>
</td></tr></table></body></html>"""


def _generic_html_body(payload: MessagePayload, config: EmailRuntimeConfig) -> str:
    labels = {
        "quote": "报价通知", "retail_quote": "服务报价通知", "quote_status": "报价进度", "intake": "收机通知", "inspection": "检测报告",
        "completion": "维修完成", "shipping": "发货通知", "followup": "售后回访",
        "repairing": "维修进度", "quality_check": "质检复测",
        "technical_support": "技术支持",
    }
    notice_kind = responsibility_kind_from_snapshot(payload.body_text)
    message_body = strip_responsibility_snapshot(payload.body_text)
    status_content = _status_progress_html(payload.template_type)
    responsibility = (
        _responsibility_html(notice_kind or "repair")
        if payload.template_type in STATUS_EMAIL_STAGES
        else ""
    )
    return _email_shell(
        config=config,
        subject=payload.subject,
        label=labels.get(payload.template_type, "服务通知"),
        content=status_content + _text_to_html(message_body) + responsibility,
        attachment_count=len(payload.attachments),
    )


def _html_body(payload: EmailPayload, config: EmailRuntimeConfig) -> str:
    esc = lambda value: html.escape(str(value or ""), quote=True)
    extra = f'<div style="margin:18px 0;padding:14px 16px;border-radius:12px;background:#f5f5f7;color:#3a3a3c">{esc(payload.message).replace(chr(10), "<br>")}</div>' if payload.message else ""
    content = f"""<p style="margin:0 0 12px;line-height:1.7">尊敬的 {esc(payload.customer_name)}，您好：</p>
<p style="margin:0 0 18px;line-height:1.7;color:#3a3a3c">您的设备维修报价已生成，请查看本邮件附带的 PDF 报价单。</p>{extra}
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e5e5ea;border-radius:14px;background:#f5f5f7;overflow:hidden">
<tr><td style="padding:10px 14px;color:#6e6e73;font-size:12px;border-bottom:1px solid #e5e5ea">工单号</td><td style="padding:10px 14px;text-align:right;border-bottom:1px solid #e5e5ea;font-weight:600">{esc(payload.order_no)}</td></tr>
<tr><td style="padding:10px 14px;color:#6e6e73;font-size:12px;border-bottom:1px solid #e5e5ea">报价号</td><td style="padding:10px 14px;text-align:right;border-bottom:1px solid #e5e5ea">{esc(payload.quote_no)}</td></tr>
<tr><td style="padding:10px 14px;color:#6e6e73;font-size:12px;border-bottom:1px solid #e5e5ea">设备</td><td style="padding:10px 14px;text-align:right;border-bottom:1px solid #e5e5ea">{esc(payload.device_name)}</td></tr>
<tr><td style="padding:10px 14px;color:#6e6e73;font-size:12px;border-bottom:1px solid #e5e5ea">序列号</td><td style="padding:10px 14px;text-align:right;border-bottom:1px solid #e5e5ea">{esc(payload.serial_number)}</td></tr>
<tr><td style="padding:13px 14px;color:#1d1d1f;font-weight:600">报价合计</td><td style="padding:13px 14px;text-align:right;color:#007aff;font-size:20px;font-weight:700">¥ {esc(payload.total_amount)}</td></tr></table>
<p style="margin:18px 0 0;color:#8e8e93;font-size:12px;line-height:1.6">报价金额不代表已收款；如需确认维修，请联系您的服务顾问。</p>"""
    return _email_shell(
        config=config,
        subject=payload.subject,
        label="报价通知",
        content=content,
        attachment_count=1,
    )


class MockEmailService:
    def __init__(self, config: EmailRuntimeConfig):
        self.config = config

    def send_quote(self, payload: EmailPayload) -> EmailResult:
        if not payload.attachment_path.is_file():
            return EmailResult(False, "mock", "PDF 附件不存在")
        return EmailResult(True, "mock", "Mock 模式已完成邮件构建，未向外部邮箱投递")

    def send_message(self, payload: MessagePayload) -> EmailResult:
        missing = [path.name for path in payload.attachments if not path.is_file()]
        if missing:
            return EmailResult(False, "mock", f"邮件快照附件不存在：{', '.join(missing)}")
        return EmailResult(True, "mock", "Mock 模式已完成邮件与附件快照校验，未向外部邮箱投递")


class SMTPEmailService:
    def __init__(self, config: EmailRuntimeConfig):
        self.config = config

    def send_quote(self, payload: EmailPayload) -> EmailResult:
        if not payload.attachment_path.is_file():
            return EmailResult(False, "smtp", "PDF 附件不存在")
        config = self.config
        if not config.smtp_host or not config.sender or not config.password:
            return EmailResult(False, "smtp", "SMTP 配置不完整")
        message = EmailMessage()
        message["From"] = formataddr((config.from_name, config.sender))
        message["To"] = payload.recipient
        message["Subject"] = payload.subject
        if config.reply_to:
            message["Reply-To"] = config.reply_to
        message.set_content(f"您好，报价单 {payload.quote_no} 已生成，请查看 PDF 附件。")
        message.add_alternative(_html_body(payload, config), subtype="html")
        attachment = payload.attachment_path.read_bytes()
        message.add_attachment(attachment, maintype="application", subtype="pdf", filename=payload.attachment_path.name)
        context = ssl.create_default_context()
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=config.timeout_seconds, context=context) as server:
                server.login(config.sender, config.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.timeout_seconds) as server:
                server.ehlo()
                if config.use_starttls and server.has_extn("starttls"):
                    server.starttls(context=context)
                    server.ehlo()
                server.login(config.sender, config.password)
                server.send_message(message)
        return EmailResult(True, "smtp", "SMTP 邮件已投递")

    def send_message(self, payload: MessagePayload) -> EmailResult:
        config = self.config
        if not config.smtp_host or not config.sender or not config.password:
            return EmailResult(False, "smtp", "SMTP 配置不完整")
        if not payload.recipients:
            return EmailResult(False, "smtp", "没有收件人")
        missing = [path.name for path in payload.attachments if not path.is_file()]
        if missing:
            return EmailResult(False, "smtp", f"邮件快照附件不存在：{', '.join(missing)}")
        message = EmailMessage()
        message["From"] = formataddr((config.from_name, config.sender))
        message["To"] = ", ".join(payload.recipients)
        if payload.cc:
            message["Cc"] = ", ".join(payload.cc)
        message["Subject"] = payload.subject
        if config.reply_to:
            message["Reply-To"] = config.reply_to
        message.set_content(payload.body_text)
        message.add_alternative(_generic_html_body(payload, config), subtype="html")
        for path in payload.attachments:
            mime_type, _ = mimetypes.guess_type(path.name)
            maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
            message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        recipients = [*payload.recipients, *payload.cc, *payload.bcc]
        context = ssl.create_default_context()
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=config.timeout_seconds, context=context) as server:
                server.login(config.sender, config.password)
                server.send_message(message, to_addrs=recipients)
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.timeout_seconds) as server:
                server.ehlo()
                if config.use_starttls and server.has_extn("starttls"):
                    server.starttls(context=context)
                    server.ehlo()
                server.login(config.sender, config.password)
                server.send_message(message, to_addrs=recipients)
        return EmailResult(True, "smtp", "SMTP 邮件已投递")


def email_config_status(db: Session) -> dict:
    data = safe_email_config(load_email_config(db))
    data["host"] = data["smtp_host"] if data["mode"] == "smtp" else None
    data["port"] = data["smtp_port"] if data["mode"] == "smtp" else None
    return data


def get_email_service(db: Session) -> EmailService:
    config = load_email_config(db)
    return SMTPEmailService(config) if config.mode == "smtp" else MockEmailService(config)
