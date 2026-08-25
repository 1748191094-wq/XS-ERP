from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models.entities import CustomEmailTemplate, User, utcnow
from app.services.email_templates import (
    EMAIL_TEMPLATES,
    EMAIL_TEMPLATE_CATEGORIES,
    EMAIL_TEMPLATE_PLACEHOLDERS,
    STATUS_EMAIL_STAGES,
    SYSTEM_EMAIL_TEMPLATE_CATEGORIES,
    validate_email_template_text,
)


def _placeholder_catalog() -> list[dict[str, str]]:
    return [
        {"key": key, "token": "{" + key + "}", "label": label}
        for key, label in sorted(EMAIL_TEMPLATE_PLACEHOLDERS.items())
    ]


def _used_placeholders(subject: str, body: str) -> tuple[list[str], bool]:
    try:
        return validate_email_template_text(subject, body), True
    except ValueError:
        return [], False


def _category_payload(category: str) -> dict:
    metadata = EMAIL_TEMPLATE_CATEGORIES[category]
    return {
        "category": category,
        "category_label": metadata["label"],
        "category_order": metadata["order"],
    }


def email_template_library_metadata() -> dict:
    return {
        "categories": [
            {"key": key, "label": value["label"], "order": value["order"]}
            for key, value in sorted(
                EMAIL_TEMPLATE_CATEGORIES.items(),
                key=lambda item: item[1]["order"],
            )
        ],
        "allowed_placeholders": _placeholder_catalog(),
    }


def system_template_payload(template_type: str) -> dict | None:
    template = EMAIL_TEMPLATES.get(template_type)
    if not template:
        return None
    category = SYSTEM_EMAIL_TEMPLATE_CATEGORIES[template_type]
    used, valid = _used_placeholders(template["subject"], template["body"])
    return {
        "template_type": template_type,
        "name": template["name"],
        **_category_payload(category),
        "subject": template["subject"],
        "body": template["body"],
        "enabled": True,
        "is_system": True,
        "can_edit": False,
        "can_delete": False,
        "deleted": False,
        "deleted_at": None,
        "created_at": None,
        "updated_at": None,
        "created_by": None,
        "updated_by": None,
        "status_stage": STATUS_EMAIL_STAGES.get(template_type),
        "used_placeholders": used,
        "template_valid": valid,
        "allowed_placeholders": _placeholder_catalog(),
    }


def custom_template_payload(template: CustomEmailTemplate) -> dict:
    used, valid = _used_placeholders(template.subject, template.body)
    return {
        "template_type": template.template_type,
        "name": template.name,
        **_category_payload(template.category),
        "subject": template.subject,
        "body": template.body,
        "enabled": template.enabled,
        "is_system": False,
        "can_edit": template.deleted_at is None,
        "can_delete": template.deleted_at is None,
        "deleted": template.deleted_at is not None,
        "deleted_at": template.deleted_at,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
        "created_by": template.created_by,
        "updated_by": template.updated_by,
        "status_stage": None,
        "used_placeholders": used,
        "template_valid": valid,
        "allowed_placeholders": _placeholder_catalog(),
    }


def list_email_template_library(
    db: Session,
    *,
    include_disabled: bool = False,
    include_deleted: bool = False,
) -> list[dict]:
    items = [system_template_payload(key) for key in EMAIL_TEMPLATES]
    stmt = select(CustomEmailTemplate)
    if not include_deleted:
        stmt = stmt.where(CustomEmailTemplate.deleted_at.is_(None))
    if not include_disabled:
        stmt = stmt.where(CustomEmailTemplate.enabled.is_(True))
    items.extend(custom_template_payload(row) for row in db.scalars(stmt))
    return sorted(
        (item for item in items if item is not None),
        key=lambda item: (
            item["category_order"],
            0 if item["is_system"] else 1,
            item["name"].casefold(),
            item["template_type"],
        ),
    )


def resolve_email_template(db: Session | None, template_type: str) -> dict:
    system = system_template_payload(template_type)
    if system:
        return system
    if db is None:
        raise BusinessError("未知邮件模板", code="email_template_invalid")
    template = db.scalar(
        select(CustomEmailTemplate).where(
            CustomEmailTemplate.template_type == template_type,
            CustomEmailTemplate.deleted_at.is_(None),
        )
    )
    if not template:
        raise BusinessError("未知邮件模板", code="email_template_invalid")
    if not template.enabled:
        raise BusinessError("该邮件模板已停用", code="email_template_disabled", status_code=409)
    payload = custom_template_payload(template)
    if not payload["template_valid"]:
        raise BusinessError("邮件模板内容无效，请由管理员修正", code="email_template_content_invalid", status_code=409)
    return payload


def _validate_content(subject: str, body: str) -> None:
    try:
        validate_email_template_text(subject, body)
    except ValueError as exc:
        raise BusinessError(str(exc), code="email_template_content_invalid", status_code=422) from exc


def _validate_category(category: str) -> None:
    if category not in EMAIL_TEMPLATE_CATEGORIES:
        raise BusinessError("未知邮件模板分类", code="email_template_category_invalid", status_code=422)


def create_custom_email_template(db: Session, payload, actor: User) -> CustomEmailTemplate:
    _validate_category(payload.category)
    _validate_content(payload.subject, payload.body)
    for _attempt in range(8):
        template_type = f"custom_{secrets.token_hex(8)}"
        if not db.scalar(select(CustomEmailTemplate.id).where(CustomEmailTemplate.template_type == template_type)):
            break
    else:
        raise BusinessError("无法生成模板编号，请重试", code="email_template_key_unavailable", status_code=503)
    template = CustomEmailTemplate(
        template_type=template_type,
        name=payload.name,
        category=payload.category,
        subject=payload.subject,
        body=payload.body,
        enabled=payload.enabled,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(template)
    db.flush()
    return template


def _custom_for_mutation(db: Session, template_type: str, *, include_deleted: bool = False) -> CustomEmailTemplate:
    if template_type in EMAIL_TEMPLATES:
        raise BusinessError("系统模板不可修改或删除", code="system_email_template_immutable", status_code=409)
    stmt = select(CustomEmailTemplate).where(CustomEmailTemplate.template_type == template_type)
    if not include_deleted:
        stmt = stmt.where(CustomEmailTemplate.deleted_at.is_(None))
    template = db.scalar(stmt)
    if not template:
        raise BusinessError("自定义邮件模板不存在", code="email_template_not_found", status_code=404)
    return template


def update_custom_email_template(db: Session, template_type: str, payload, actor: User) -> CustomEmailTemplate:
    template = _custom_for_mutation(db, template_type)
    changes = payload.model_dump(exclude_unset=True)
    category = changes.get("category", template.category)
    subject = changes.get("subject", template.subject)
    body = changes.get("body", template.body)
    _validate_category(category)
    _validate_content(subject, body)
    for field, value in changes.items():
        setattr(template, field, value)
    template.updated_by = actor.id
    db.flush()
    return template


def delete_custom_email_template(db: Session, template_type: str, actor: User) -> CustomEmailTemplate:
    template = _custom_for_mutation(db, template_type)
    template.deleted_at = utcnow()
    template.deleted_by = actor.id
    template.deletion_batch_id = str(uuid.uuid4())
    template.updated_by = actor.id
    db.flush()
    return template


def restore_custom_email_template(db: Session, template_type: str, actor: User) -> CustomEmailTemplate:
    template = _custom_for_mutation(db, template_type, include_deleted=True)
    if template.deleted_at is None:
        raise BusinessError("自定义邮件模板未被删除", code="email_template_not_deleted", status_code=409)
    _validate_category(template.category)
    _validate_content(template.subject, template.body)
    template.deleted_at = None
    template.deleted_by = None
    template.deletion_batch_id = None
    template.updated_by = actor.id
    db.flush()
    return template
