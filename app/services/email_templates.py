from __future__ import annotations

from string import Formatter


RESPONSIBILITY_MARKER = "—— 服务责任与重要说明 ——"

RESPONSIBILITY_ITEMS = (
    (
        "检测与维修授权",
        "我们仅在已说明的检测范围内操作设备；如需实施超出已确认报价或范围的维修、换件及收费项目，将再次联系您确认。",
    ),
    (
        "设备数据与隐私",
        "请在送修前自行备份重要数据并退出个人账号。服务过程中仅为履行维修所必需而处理相关信息；如确需重置、升级或清除数据，我们将事先说明并取得确认，法律法规另有规定的除外。",
    ),
    (
        "故障复现与检测结论",
        "间歇性故障、受环境影响的异常或送修时无法复现的问题，将按实际检测结果记录。检测结论不替代品牌方的官方质量认定或法定鉴定。",
    ),
    (
        "物流与第三方服务",
        "如需通过承运商或具备相应能力的第三方协助交付或维修，我们仅提供完成该服务所必需的信息，并向您说明相关安排。",
    ),
    (
        "消费者法定权利",
        "本说明用于明确服务流程和双方配合事项，不排除或限制您依据适用法律享有的权利，也不免除服务方依法应承担的责任；如与适用法律冲突，以适用法律为准。",
    ),
)

RESPONSIBILITY_TEXT = RESPONSIBILITY_MARKER + "\n" + "\n".join(
    f"{index}. {title}：{body}"
    for index, (title, body) in enumerate(RESPONSIBILITY_ITEMS, start=1)
)

SERVICE_RESPONSIBILITY_MARKER = "—— 服务范围与权益说明 ——"

SERVICE_RESPONSIBILITY_ITEMS = (
    (
        "服务范围与确认",
        "我们将按照工单记录及双方已确认的需求提供咨询、报价、零售、置换、投诉处理、技术支持或其他约定服务；如服务范围、方案、费用或交付安排发生变化，将在执行前再次向您说明并确认。",
    ),
    (
        "信息与隐私",
        "我们仅为受理、跟进和完成本次服务处理必要的客户、订单及设备信息，并采取合理措施保护相关信息；除依法办理或完成已说明的协作外，不会向无关第三方提供。",
    ),
    (
        "沟通结果与时效",
        "服务建议、评估结果和预计时效基于客户提供的信息及当时可确认的情况；如前提或外部条件变化，我们会及时更新记录并与您沟通，不将进度通知表述为法定鉴定或第三方结果保证。",
    ),
    (
        "付款、交付与第三方协作",
        "如服务涉及付款平台、物流承运、品牌方或其他具备相应能力的协作方，我们会说明相关安排，并仅提供完成对应环节所必需的信息。",
    ),
    (
        "消费者法定权利",
        "本说明用于明确服务流程和双方配合事项，不排除或限制您依据适用法律享有的权利，也不免除服务方依法应承担的责任；如与适用法律冲突，以适用法律为准。",
    ),
)

SERVICE_RESPONSIBILITY_TEXT = SERVICE_RESPONSIBILITY_MARKER + "\n" + "\n".join(
    f"{index}. {title}：{body}"
    for index, (title, body) in enumerate(SERVICE_RESPONSIBILITY_ITEMS, start=1)
)

RESPONSIBILITY_NOTICES = {
    "repair": {
        "kind": "repair",
        "marker": RESPONSIBILITY_MARKER,
        "title": "服务责任与重要说明",
        "summary": "请在继续维修服务前了解以下授权、设备数据与权益说明。",
        "items": RESPONSIBILITY_ITEMS,
        "text": RESPONSIBILITY_TEXT,
    },
    "service": {
        "kind": "service",
        "marker": SERVICE_RESPONSIBILITY_MARKER,
        "title": "服务范围与权益说明",
        "summary": "适用于咨询、报价、零售、置换、投诉处理、技术支持及其他非维修服务。",
        "items": SERVICE_RESPONSIBILITY_ITEMS,
        "text": SERVICE_RESPONSIBILITY_TEXT,
    },
}


def responsibility_kind_for_context(*, ticket_type: str | None, has_repair_order: bool) -> str:
    """Choose the customer notice from the business context, not the email template name."""
    if ticket_type:
        return "repair" if ticket_type == "repair" else "service"
    return "repair" if has_repair_order else "service"


def responsibility_notice(kind: str) -> dict:
    return RESPONSIBILITY_NOTICES["service" if kind == "service" else "repair"]


def responsibility_kind_from_snapshot(body: str) -> str | None:
    value = body or ""
    if SERVICE_RESPONSIBILITY_MARKER in value:
        return "service"
    if RESPONSIBILITY_MARKER in value:
        return "repair"
    return None


def strip_responsibility_snapshot(body: str) -> str:
    """Remove either canonical notice so edited snapshots cannot contain two profiles."""
    value = body or ""
    marker_positions = [
        position
        for marker in (RESPONSIBILITY_MARKER, SERVICE_RESPONSIBILITY_MARKER)
        if (position := value.find(marker)) >= 0
    ]
    return value[: min(marker_positions)].rstrip() if marker_positions else value.rstrip()


STATUS_EMAIL_STAGES = {
    "intake": {
        "index": 1,
        "total": 7,
        "label": "维修中心已收件",
        "short_label": "已收件",
        "summary": "设备已到达维修中心，等待登记与人工检测。",
    },
    "inspection": {
        "index": 2,
        "total": 7,
        "label": "人工检测已完成",
        "short_label": "已检测",
        "summary": "检测结果已记录，后续将根据结论确认维修方案。",
    },
    "quote_status": {
        "index": 3,
        "total": 7,
        "label": "维修报价待确认",
        "short_label": "待确认报价",
        "summary": "报价方案已生成，请核对项目与金额后确认是否继续。",
    },
    "repairing": {
        "index": 4,
        "total": 7,
        "label": "设备维修处理中",
        "short_label": "维修中",
        "summary": "已按确认方案进入维修处理阶段。",
    },
    "quality_check": {
        "index": 5,
        "total": 7,
        "label": "维修后质检复测",
        "short_label": "质检复测",
        "summary": "维修操作已完成，正在进行功能检查与人工复测。",
    },
    "completion": {
        "index": 6,
        "total": 7,
        "label": "维修服务已完成",
        "short_label": "维修完成",
        "summary": "设备已完成维修与复测，等待取机或安排交付。",
    },
    "shipping": {
        "index": 7,
        "total": 7,
        "label": "设备已安排交付",
        "short_label": "交付中",
        "summary": "设备已进入交付环节，请留意物流或取机通知。",
    },
}


EMAIL_TEMPLATES = {
    "quote": {
        "name": "报价通知",
        "subject": "{brand}维修报价 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n您的设备 {device} 已完成检测并生成维修报价，报价编号为 {quote_no}，合计 ¥{amount}。请查看附件并确认是否继续维修。{payment_notice}\n\n如有疑问，请直接回复本邮件联系我们。\n\n此致\n{brand}",
    },
    "retail_quote": {
        "name": "服务报价通知",
        "subject": "{brand}服务报价 - {service_title}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n针对您的“{service_title}”服务需求，我们已生成服务报价，报价编号为 {quote_no}，合计 ¥{amount}。请查看附件并确认服务方案。{payment_notice}\n\n如有疑问，请直接回复本邮件联系我们。\n\n此致\n{brand}",
    },
    "replacement_quote": {
        "name": "置换服务报价通知",
        "subject": "{brand}置换服务报价 - {service_title}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n针对您的“{service_title}”置换需求，我们已生成置换服务报价，报价编号为 {quote_no}。旧机检测结果：{replacement_inspection_result}；本报价版本已计入旧机抵折 / 优惠 ¥{quote_discount}，最终应付合计 ¥{amount}。请查看附件并确认置换方案。{payment_notice}\n\n工单评估抵折参考：{evaluated_trade_in_credit}（最终以本报价版本计入的抵折 / 优惠为准，不重复扣减）\n寄回门店单号 / 线下交易备注：{return_reference}\n寄出客户单号：{outbound_to_customer_tracking_no}\n\n如有疑问，请直接回复本邮件联系我们。\n\n此致\n{brand}",
    },
    "quote_status": {
        "name": "报价进度通知",
        "subject": "维修报价待确认 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n您的设备 {device} 已完成检测并生成维修报价，报价编号为 {quote_no}，合计 ¥{amount}。请查看附件并确认是否继续维修。{payment_notice}\n\n如有疑问，请直接回复本邮件联系我们。\n\n此致\n{brand}",
    },
    "intake": {
        "name": "维修中心收件通知",
        "subject": "维修中心收件通知 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n{brand}已收到您的 {device} 设备（工单号：{order_no}，序列号：{serial_no}）。\n\n我们会在检测、报价、维修、质检及交付等关键服务节点，通过邮件、短信或电话与您联系，请留意相关通知。感谢您的配合。\n\n此致\n{brand}",
    },
    "inspection": {
        "name": "检测完成通知",
        "subject": "检测完成通知 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n您的设备 {device} 已完成人工检测，工单号为 {order_no}。请查看随邮件提供的检测信息；如需维修报价，我们会继续与您确认。\n\n此致\n{brand}",
    },
    "repairing": {
        "name": "维修进度通知",
        "subject": "维修进度通知 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n您的设备 {device} 已按确认方案进入维修处理阶段，工单号为 {order_no}。如维修范围、配件或预计时间发生变化，我们会再次联系您确认。\n\n此致\n{brand}",
    },
    "quality_check": {
        "name": "质检复测通知",
        "subject": "质检复测通知 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n您的设备 {device} 已完成当前维修操作，正在进行功能检查与人工复测，工单号为 {order_no}。复测完成后我们会继续通知您。\n\n此致\n{brand}",
    },
    "completion": {
        "name": "维修完成通知",
        "subject": "维修完成通知 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n您的设备 {device} 已完成维修与人工复测，工单号为 {order_no}。请查看维修完成信息，我们将与您确认取机或交付安排。\n\n此致\n{brand}",
    },
    "shipping": {
        "name": "交付发货通知",
        "subject": "设备交付通知 - {device}（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n工单 {order_no} 对应的 {device} 已进入交付环节。物流单号、取机方式及最新进度请以服务顾问后续同步为准。\n\n此致\n{brand}",
    },
    "followup": {
        "name": "售后回访",
        "subject": "{brand}维修服务回访（{order_no}）",
        "body": "尊敬的{customer}：\n您好！\n\n我们正在对工单 {order_no} 的维修服务进行回访，设备为 {device}。欢迎回复使用情况和改进建议。\n\n此致\n{brand}",
    },
    "technical_support": {
        "name": "技术支持",
        "subject": "技术支持",
        "body": "您好：\n\n我是{support_agent}，本次为您反馈信息如下：\n{feedback}\n\n如需补充信息，欢迎直接回复本邮件。",
    },
}


EMAIL_TEMPLATE_CATEGORIES = {
    "quotation": {"label": "报价通知", "order": 10},
    "repair_progress": {"label": "维修进度", "order": 20},
    "customer_service": {"label": "客户服务", "order": 30},
    "general": {"label": "通用通知", "order": 40},
}

SYSTEM_EMAIL_TEMPLATE_CATEGORIES = {
    "quote": "quotation",
    "retail_quote": "quotation",
    "replacement_quote": "quotation",
    "quote_status": "repair_progress",
    "intake": "repair_progress",
    "inspection": "repair_progress",
    "repairing": "repair_progress",
    "quality_check": "repair_progress",
    "completion": "repair_progress",
    "shipping": "repair_progress",
    "followup": "customer_service",
    "technical_support": "customer_service",
}

EMAIL_TEMPLATE_PLACEHOLDERS = {
    "amount": "报价合计",
    "brand": "服务品牌",
    "customer": "客户姓名",
    "device": "设备名称",
    "evaluated_trade_in_credit": "工单评估抵折",
    "feedback": "技术支持反馈",
    "order_no": "工单号",
    "outbound_to_customer_tracking_no": "寄出客户单号",
    "payment_notice": "付款链接说明",
    "quote_discount": "报价抵折或优惠",
    "quote_no": "报价编号",
    "replacement_inspection_result": "旧机检测结果",
    "return_reference": "寄回门店单号或线下交易备注",
    "serial_no": "设备序列号",
    "service_title": "服务工单标题",
    "support_agent": "服务人员",
}


def validate_email_template_text(subject: str, body: str) -> list[str]:
    """Validate placeholders without allowing attribute/index/format execution."""

    if "\r" in subject or "\n" in subject:
        raise ValueError("邮件主题不能包含换行符。")
    if "\x00" in subject or "\x00" in body:
        raise ValueError("邮件模板不能包含空字符。")
    used: set[str] = set()
    for label, source in (("主题", subject), ("正文", body)):
        try:
            parsed = Formatter().parse(source)
            for _literal, field_name, format_spec, conversion in parsed:
                if field_name is None:
                    continue
                if (
                    not field_name
                    or field_name not in EMAIL_TEMPLATE_PLACEHOLDERS
                    or format_spec
                    or conversion
                ):
                    raise ValueError(
                        f"{label}包含不受支持的占位符 {{{field_name}}}；"
                        "仅支持列表中的简单占位符，不支持属性、索引、转换或格式化。"
                    )
                used.add(field_name)
        except ValueError as exc:
            if "占位符" in str(exc):
                raise
            raise ValueError(f"{label}中的花括号不完整，请使用 {{name}} 或成对的 {{{{ }}}}。") from exc
    return sorted(used)


def render_email_template_text(subject: str, body: str, values: dict[str, object]) -> tuple[str, str]:
    """Render only templates that passed the controlled-placeholder contract."""

    validate_email_template_text(subject, body)
    missing = sorted(
        field
        for field in validate_email_template_text(subject, body)
        if field not in values
    )
    if missing:
        raise ValueError(f"模板上下文缺少字段：{', '.join(missing)}")
    return subject.format_map(values), body.format_map(values)


def with_responsibility_snapshot(
    body: str,
    template_type: str,
    responsibility_kind: str = "repair",
) -> str:
    """Freeze the context-appropriate notice into status-email snapshots exactly once."""
    if template_type not in STATUS_EMAIL_STAGES:
        return (body or "").rstrip()
    notice = responsibility_notice(responsibility_kind)
    clean_body = strip_responsibility_snapshot(body)
    return f"{clean_body}\n\n{notice['text']}"
