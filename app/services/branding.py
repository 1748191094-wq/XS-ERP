from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models.entities import SystemSetting


BRAND_NAME_KEY = "brand.name"
DEFAULT_BRAND_NAME = "服务品牌"


def normalize_brand_name(value: str) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name:
        raise BusinessError("请填写商标名称", code="brand_name_required", status_code=422)
    if len(name) > 60:
        raise BusinessError("商标名称不能超过 60 个字符", code="brand_name_too_long", status_code=422)
    if any(ord(character) < 32 or character in "<>" for character in name):
        raise BusinessError("商标名称包含不支持的字符", code="brand_name_invalid", status_code=422)
    return name


def load_brand_name(db: Session) -> str:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == BRAND_NAME_KEY))
    if not row or not row.value.strip():
        return DEFAULT_BRAND_NAME
    try:
        return normalize_brand_name(row.value)
    except BusinessError:
        return DEFAULT_BRAND_NAME


def brand_is_configured(db: Session) -> bool:
    row = db.scalar(select(SystemSetting.id).where(
        SystemSetting.key == BRAND_NAME_KEY,
        SystemSetting.value != "",
    ))
    return row is not None


def save_initial_brand_name(db: Session, value: str) -> str:
    name = normalize_brand_name(value)
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == BRAND_NAME_KEY))
    if row and row.value.strip():
        raise BusinessError("商标已经完成初始化", code="brand_already_initialized", status_code=409)
    if row:
        row.value = name
        row.description = "首次管理员初始化时设置的商标名称"
    else:
        db.add(SystemSetting(
            key=BRAND_NAME_KEY,
            value=name,
            description="首次管理员初始化时设置的商标名称",
            is_secret=False,
        ))
    return name


def branding_payload(db: Session) -> dict[str, object]:
    return {
        "brand_name": load_brand_name(db),
        "configured": brand_is_configured(db),
    }
