from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.models.entities import (
    Attachment,
    Customer,
    OutboundCall,
    OutboundEmail,
    Quote,
    RepairOrder,
    ServiceTicket,
    ServiceTicketNote,
    ServiceTicketTimeline,
    TaskRecord,
    User,
    utcnow,
)
from app.reports.pdf import PdfReportService
from app.services.branding import load_brand_name
from app.services.email_templates import (
    STATUS_EMAIL_STAGES,
    render_email_template_text,
    responsibility_kind_for_context,
    responsibility_notice,
    with_responsibility_snapshot,
)
from app.services.email_template_library import resolve_email_template
from app.services.numbering import make_no


@dataclass(slots=True)
class EmailContext:
    customer: Customer
    ticket: ServiceTicket | None
    order: RepairOrder | None
    quote: Quote | None


def _timeline(db: Session, ticket_id: int | None, event_type: str, summary: str, actor_id: int | None, details: dict | None = None) -> None:
    if ticket_id:
        db.add(ServiceTicketTimeline(
            ticket_id=ticket_id,
            event_type=event_type,
            summary=summary[:300],
            actor_id=actor_id,
            details_json=details,
        ))


def _load_order(db: Session, order_id: int) -> RepairOrder | None:
    return db.scalar(
        select(RepairOrder).where(
            RepairOrder.id == order_id,
            RepairOrder.deleted_at.is_(None),
        ).options(
            selectinload(RepairOrder.customer),
            selectinload(RepairOrder.device),
        )
    )


def resolve_email_context(
    db: Session,
    *,
    service_ticket_id: int | None,
    repair_order_id: int | None,
    quote_id: int | None,
) -> EmailContext:
    ticket = db.scalar(select(ServiceTicket).where(
        ServiceTicket.id == service_ticket_id,
        ServiceTicket.deleted_at.is_(None),
    )) if service_ticket_id else None
    if service_ticket_id and not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)

    quote = None
    if quote_id:
        quote = db.scalar(
            select(Quote).where(
                Quote.id == quote_id,
                Quote.deleted_at.is_(None),
            ).options(
                selectinload(Quote.items),
                selectinload(Quote.repair_order).selectinload(RepairOrder.customer),
                selectinload(Quote.repair_order).selectinload(RepairOrder.device),
                selectinload(Quote.service_ticket).selectinload(ServiceTicket.customer),
                selectinload(Quote.service_ticket).selectinload(ServiceTicket.device),
            )
        )
        if not quote:
            raise BusinessError("报价不存在", code="quote_not_found", status_code=404)
        if ticket:
            quote_matches_ticket = (
                quote.service_ticket_id == ticket.id
                or (
                    quote.repair_order_id is not None
                    and ticket.repair_order_id == quote.repair_order_id
                )
            )
            if not quote_matches_ticket:
                raise BusinessError(
                    "报价与服务工单不匹配",
                    code="communication_context_mismatch",
                    status_code=409,
                )
        if not ticket and quote.service_ticket:
            if quote.service_ticket.deleted_at is not None:
                raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
            ticket = quote.service_ticket

    resolved_order_id = repair_order_id or (quote.repair_order_id if quote else None) or (ticket.repair_order_id if ticket else None)
    order = quote.repair_order if quote and quote.repair_order_id == resolved_order_id else (_load_order(db, resolved_order_id) if resolved_order_id else None)
    if order and order.deleted_at is not None:
        order = None
    if resolved_order_id and not order:
        raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
    if ticket and order and ticket.repair_order_id != order.id:
        raise BusinessError(
            "服务工单与维修工单不匹配",
            code="communication_context_mismatch",
            status_code=409,
        )
    if quote and order and quote.repair_order_id != order.id:
        raise BusinessError(
            "报价与维修工单不匹配",
            code="communication_context_mismatch",
            status_code=409,
        )

    customer_id = (order.customer_id if order else None) or (ticket.customer_id if ticket else None)
    if not customer_id:
        raise BusinessError("邮件必须关联客户", code="customer_required")
    customer = order.customer if order else db.scalar(select(Customer).where(
        Customer.id == customer_id,
        Customer.deleted_at.is_(None),
    ))
    if not customer or customer.deleted_at is not None:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    if ticket and ticket.customer_id and ticket.customer_id != customer.id:
        raise BusinessError("服务工单与客户不匹配", code="communication_context_mismatch")
    return EmailContext(customer=customer, ticket=ticket, order=order, quote=quote)


def render_email_preview(
    template_type: str,
    context: EmailContext,
    *,
    brand: str = "服务中心",
    sender_name: str | None = None,
    db: Session | None = None,
) -> dict:
    template = resolve_email_template(db, template_type)
    order, quote = context.order, context.quote
    if template_type == "retail_quote":
        if not quote:
            raise BusinessError("服务报价通知必须选择报价版本", code="quote_required", status_code=409)
        if (
            not context.ticket
            or context.ticket.ticket_type != "retail"
            or quote.service_ticket_id != context.ticket.id
            or quote.repair_order_id is not None
        ):
            raise BusinessError(
                "服务报价通知仅适用于零售服务工单报价",
                code="retail_quote_required",
                status_code=409,
            )
    if template_type == "replacement_quote":
        if not quote:
            raise BusinessError("置换服务报价通知必须选择报价版本", code="quote_required", status_code=409)
        if (
            not context.ticket
            or context.ticket.ticket_type != "replacement"
            or quote.service_ticket_id != context.ticket.id
            or quote.repair_order_id is not None
        ):
            raise BusinessError(
                "置换服务报价通知仅适用于置换服务工单报价",
                code="replacement_quote_required",
                status_code=409,
            )
    device = order.device if order else (context.ticket.device if context.ticket else None)
    agent_name = (sender_name or "技术支持").strip() or "技术支持"
    support_agent = agent_name if agent_name.startswith(brand) else f"{brand} {agent_name}"
    feedback = (
        context.ticket.description if context.ticket
        else order.fault_description if order
        else "请查收本次技术支持反馈。"
    )
    payment_url = getattr(quote, "payment_url", None) if quote else None
    payment_notice = f"\n\n付款链接：{payment_url}" if payment_url else ""
    values = {
        "brand": brand,
        "customer": context.customer.name,
        "device": f"{device.brand} {device.model}".strip() if device else "关联设备",
        "serial_no": device.serial_number if device else "-",
        "order_no": order.order_no if order else (context.ticket.ticket_no if context.ticket else "-"),
        "quote_no": quote.quote_no if quote else "-",
        "amount": f"{quote.total_amount:.2f}" if quote else "0.00",
        "service_title": context.ticket.title if context.ticket else "服务方案",
        "payment_notice": payment_notice,
        "replacement_inspection_result": (
            context.ticket.replacement_inspection_result
            if context.ticket and context.ticket.replacement_inspection_result
            else "待确认"
        ),
        "quote_discount": f"{quote.discount:.2f}" if quote else "0.00",
        "evaluated_trade_in_credit": (
            f"¥{context.ticket.trade_in_credit:.2f}"
            if context.ticket and context.ticket.trade_in_credit is not None
            else "待确认"
        ),
        "return_reference": (
            context.ticket.return_reference
            if context.ticket and context.ticket.return_reference
            else "待登记"
        ),
        "outbound_to_customer_tracking_no": (
            context.ticket.outbound_to_customer_tracking_no
            if context.ticket and context.ticket.outbound_to_customer_tracking_no
            else "待登记"
        ),
        "support_agent": support_agent,
        "feedback": feedback,
    }
    try:
        rendered_subject, rendered_body = render_email_template_text(
            template["subject"],
            template["body"],
            values,
        )
    except ValueError as exc:
        raise BusinessError(
            "邮件模板无法安全渲染，请联系管理员检查模板内容",
            code="email_template_render_failed",
            status_code=409,
        ) from exc
    preview = {
        "template_type": template_type,
        "template_name": template["name"],
        "template_category": template["category"],
        "template_category_label": template["category_label"],
        "is_system_template": template["is_system"],
        "recipient": context.customer.email,
        "subject": rendered_subject,
        "body": rendered_body,
        "customer_id": context.customer.id,
        "service_ticket_id": context.ticket.id if context.ticket else None,
        "repair_order_id": order.id if order else None,
        "quote_id": quote.id if quote else None,
    }
    if template_type in STATUS_EMAIL_STAGES:
        notice_kind = responsibility_kind_for_context(
            ticket_type=context.ticket.ticket_type if context.ticket else None,
            has_repair_order=bool(context.order),
        )
        notice = responsibility_notice(notice_kind)
        preview["status_presentation"] = STATUS_EMAIL_STAGES[template_type]
        preview["includes_responsibility_notice"] = True
        preview["responsibility_notice"] = {
            "kind": notice["kind"],
            "title": notice["title"],
            "summary": notice["summary"],
            "items": [
                {"title": title, "body": body}
                for title, body in notice["items"]
            ],
        }
    return preview


def _copy_snapshot(source: Path, snapshot_dir: Path, index: int) -> dict:
    if not source.is_file():
        raise BusinessError(f"附件不存在：{source.name}", code="attachment_missing")
    safe_name = source.name.replace("\x00", "")[:220] or f"attachment-{index}"
    destination = snapshot_dir / f"{index:02d}-{safe_name}"
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}-", suffix=".tmp", dir=snapshot_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    content = destination.read_bytes()
    return {
        "filename": safe_name,
        "snapshot_path": str(destination),
        "file_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _attachment_sources(
    db: Session,
    context: EmailContext,
    attachment_ids: Iterable[int],
    auto_report: bool,
    template_type: str,
    *,
    attach_service_ticket_pdf: bool = False,
    attach_repair_report_pdf: bool = False,
) -> list[Path]:
    sources: list[Path] = []
    service_ticket_pdf_added = False
    repair_report_pdf_added = False
    reporter = PdfReportService(brand_name=load_brand_name(db))
    ids = list(dict.fromkeys(attachment_ids))
    if ids:
        attachments = list(db.scalars(select(Attachment).where(Attachment.id.in_(ids))))
        if len(attachments) != len(ids):
            raise BusinessError("部分附件不存在", code="attachment_not_found", status_code=404)
        for attachment in attachments:
            same_order = context.order and attachment.repair_order_id == context.order.id
            same_customer = attachment.customer_id == context.customer.id
            if attachment.repair_order_id:
                allowed = bool(same_order)
            else:
                allowed = bool(same_customer)
            if not allowed:
                raise BusinessError("附件不属于当前客户或工单", code="attachment_context_mismatch", status_code=403)
            sources.append(Path(attachment.storage_path))

    if auto_report:
        if template_type in {"quote", "retail_quote", "replacement_quote", "quote_status"}:
            if not context.quote:
                raise BusinessError("报价邮件必须选择报价版本", code="quote_required")
            sources.append(reporter.quote(context.quote))
        elif template_type in {"inspection", "completion"}:
            if not context.order:
                raise BusinessError("报告邮件必须关联维修工单", code="order_required")
            sources.append(reporter.repair_report(context.order, completed=template_type == "completion"))
            repair_report_pdf_added = True
        elif template_type == "technical_support":
            if not context.ticket:
                raise BusinessError("技术支持邮件必须关联服务工单", code="ticket_required")
            customer_notes = list(db.scalars(
                select(ServiceTicketNote)
                .where(
                    ServiceTicketNote.ticket_id == context.ticket.id,
                    ServiceTicketNote.visibility == "customer",
                )
                .order_by(ServiceTicketNote.created_at)
            ))
            sources.append(reporter.service_ticket(context.ticket, customer_notes=customer_notes))
            service_ticket_pdf_added = True

    if attach_service_ticket_pdf and not service_ticket_pdf_added:
        if not context.ticket:
            raise BusinessError("附加服务工单 PDF 时必须选择服务工单", code="ticket_required")
        customer_notes = list(db.scalars(
            select(ServiceTicketNote)
            .where(
                ServiceTicketNote.ticket_id == context.ticket.id,
                ServiceTicketNote.visibility == "customer",
            )
            .order_by(ServiceTicketNote.created_at)
        ))
        sources.append(reporter.service_ticket(context.ticket, customer_notes=customer_notes))

    if attach_repair_report_pdf and not repair_report_pdf_added:
        if not context.order:
            raise BusinessError("附加维修工单 PDF 时必须选择维修工单", code="order_required")
        sources.append(reporter.repair_report(context.order, completed=context.order.status == "completed"))
    return list(dict.fromkeys(path.resolve() for path in sources))


def queue_outbound_email(db: Session, payload, actor: User, *, from_name: str) -> tuple[OutboundEmail, TaskRecord]:
    context = resolve_email_context(
        db,
        service_ticket_id=payload.service_ticket_id,
        repair_order_id=payload.repair_order_id,
        quote_id=payload.quote_id,
    )
    preview = render_email_preview(
        payload.template_type,
        context,
        brand=from_name,
        sender_name=actor.display_name,
        db=db,
    )
    recipient = payload.recipient or preview["recipient"]
    if not recipient:
        raise BusinessError("客户未填写邮箱，请在发送前填写收件人", code="email_required")
    email_no = make_no("MAIL")
    snapshot_dir = (settings.email_snapshot_dir / email_no).resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    try:
        sources = _attachment_sources(
            db,
            context,
            payload.attachment_ids,
            payload.auto_attach_report,
            payload.template_type,
            attach_service_ticket_pdf=payload.attach_service_ticket_pdf,
            attach_repair_report_pdf=payload.attach_repair_report_pdf,
        )
        snapshots = [_copy_snapshot(source, snapshot_dir, index + 1) for index, source in enumerate(sources)]
    except Exception:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise

    task = TaskRecord(
        task_no=make_no("TASK"),
        task_type="outbound_email",
        related_type="service_ticket" if context.ticket else "repair_order",
        related_id=context.ticket.id if context.ticket else (context.order.id if context.order else None),
        status="queued",
        progress=0,
    )
    db.add(task)
    db.flush()
    delivery = OutboundEmail(
        email_no=email_no,
        template_type=payload.template_type,
        customer_id=context.customer.id,
        service_ticket_id=context.ticket.id if context.ticket else None,
        repair_order_id=context.order.id if context.order else None,
        quote_id=context.quote.id if context.quote else None,
        task_record_id=task.id,
        created_by=actor.id,
        recipient=recipient,
        cc_json=payload.cc,
        bcc_json=payload.bcc,
        subject_snapshot=payload.subject or preview["subject"],
        body_snapshot=with_responsibility_snapshot(
            payload.body or preview["body"],
            payload.template_type,
            preview.get("responsibility_notice", {}).get("kind", "repair"),
        ),
        attachment_snapshot_json=snapshots,
        status="queued",
    )
    db.add(delivery)
    db.flush()
    _timeline(
        db,
        delivery.service_ticket_id,
        "email_queued",
        f"外发邮件 {delivery.email_no} 已进入发送队列",
        actor.id,
        {"template_type": delivery.template_type, "recipient": delivery.recipient},
    )
    return delivery, task


def create_call(db: Session, payload, actor: User) -> OutboundCall:
    customer = db.get(Customer, payload.customer_id)
    if not customer:
        raise BusinessError("客户不存在", code="customer_not_found", status_code=404)
    ticket = db.get(ServiceTicket, payload.service_ticket_id) if payload.service_ticket_id else None
    if payload.service_ticket_id and not ticket:
        raise BusinessError("服务工单不存在", code="ticket_not_found", status_code=404)
    order = db.get(RepairOrder, payload.repair_order_id) if payload.repair_order_id else None
    if payload.repair_order_id and not order:
        raise BusinessError("维修工单不存在", code="order_not_found", status_code=404)
    if ticket and ticket.customer_id and ticket.customer_id != customer.id:
        raise BusinessError("服务工单与客户不匹配", code="communication_context_mismatch")
    if order and order.customer_id != customer.id:
        raise BusinessError("维修工单与客户不匹配", code="communication_context_mismatch")
    if ticket and order and ticket.repair_order_id and ticket.repair_order_id != order.id:
        raise BusinessError("服务工单与维修工单不匹配", code="communication_context_mismatch", status_code=409)
    if payload.assigned_to and not db.get(User, payload.assigned_to):
        raise BusinessError("负责人不存在", code="user_not_found", status_code=404)
    call = OutboundCall(
        call_no=make_no("CALL"),
        customer_id=customer.id,
        service_ticket_id=ticket.id if ticket else None,
        repair_order_id=order.id if order else None,
        assigned_to=payload.assigned_to or actor.id,
        created_by=actor.id,
        contact_number=payload.contact_number,
        purpose=payload.purpose,
        planned_at=payload.planned_at,
        status="planned",
        provider="manual",
    )
    db.add(call)
    db.flush()
    _timeline(db, call.service_ticket_id, "call_planned", f"已建立外呼任务 {call.call_no}", actor.id, {"purpose": call.purpose})
    return call


def complete_call(db: Session, call_id: int, payload, actor: User) -> OutboundCall:
    call = db.get(OutboundCall, call_id)
    if not call:
        raise BusinessError("外呼任务不存在", code="call_not_found", status_code=404)
    if call.status == "completed":
        raise BusinessError("外呼任务已完成", code="call_already_completed", status_code=409)
    if payload.recording_attachment_id:
        attachment = db.get(Attachment, payload.recording_attachment_id)
        if not attachment:
            raise BusinessError("录音附件不存在", code="attachment_not_found", status_code=404)
        if attachment.repair_order_id:
            allowed = attachment.repair_order_id == call.repair_order_id
        else:
            allowed = attachment.customer_id == call.customer_id
        if not allowed:
            raise BusinessError("录音附件不属于当前客户或工单", code="attachment_context_mismatch", status_code=403)
    call.status = "completed"
    call.result = payload.result
    call.actual_at = payload.actual_at or utcnow()
    call.duration_seconds = payload.duration_seconds
    call.summary = payload.summary
    call.customer_intent = payload.customer_intent
    call.next_contact_at = payload.next_contact_at
    call.recording_attachment_id = payload.recording_attachment_id
    _timeline(
        db,
        call.service_ticket_id,
        "call_completed",
        f"外呼 {call.call_no} 已登记：{call.result}",
        actor.id,
        {"duration_seconds": call.duration_seconds, "next_contact_at": str(call.next_contact_at or "")},
    )
    return call
