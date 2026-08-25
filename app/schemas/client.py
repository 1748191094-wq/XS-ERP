from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PHONE_RE = re.compile(r"^[0-9+()\- ]{6,32}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")
SLUG_RE = re.compile(r"^[a-z0-9-]{2,100}$")


class ClientSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _validate_phone(value: str) -> str:
    if not PHONE_RE.fullmatch(value):
        raise ValueError("手机号格式不正确")
    return value


def _validate_password(value: str) -> str:
    if len(value) < 8 or len(value) > 128:
        raise ValueError("密码长度必须为 8 至 128 位")
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        raise ValueError("密码必须同时包含字母和数字")
    return value


class ClientRegister(ClientSchema):
    username: str = Field(min_length=3, max_length=80)
    phone: str = Field(min_length=6, max_length=32)
    nickname: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def username_format(cls, value: str) -> str:
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("识别码只能包含字母、数字、点、短横线和下划线")
        return value.lower()

    _phone_format = field_validator("phone")(_validate_phone)
    _password_strength = field_validator("password")(_validate_password)


class ClientLogin(ClientSchema):
    login: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("login")
    @classmethod
    def normalize_identifier_login(cls, value: str) -> str:
        return value[1:] if value.startswith("@") else value


class ClientProfileUpdate(ClientSchema):
    nickname: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, min_length=6, max_length=32)
    email: str | None = Field(default=None, max_length=254)

    _phone_format = field_validator("phone")(
        lambda value: _validate_phone(value) if value is not None else value
    )


class ClientIdentifierUpdate(ClientSchema):
    identifier: str = Field(min_length=3, max_length=81)

    @field_validator("identifier")
    @classmethod
    def identifier_format(cls, value: str) -> str:
        normalized = value[1:] if value.startswith("@") else value
        if not USERNAME_RE.fullmatch(normalized):
            raise ValueError("识别码只能包含字母、数字、点、短横线和下划线")
        return normalized.lower()


class ClientPasswordChange(ClientSchema):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    _password_strength = field_validator("new_password")(_validate_password)


class AddressWrite(ClientSchema):
    recipient_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=32)
    province: str = Field(min_length=1, max_length=60)
    city: str = Field(min_length=1, max_length=60)
    district: str | None = Field(default=None, max_length=80)
    detail: str = Field(min_length=3, max_length=500)
    postal_code: str | None = Field(default=None, max_length=20)
    is_default: bool = False
    _phone_format = field_validator("phone")(_validate_phone)


class ProductCategoryWrite(ClientSchema):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=2, max_length=100)
    sort_order: int = Field(default=0, ge=0, le=10000)
    enabled: bool = True

    @field_validator("slug")
    @classmethod
    def slug_format(cls, value: str) -> str:
        if not SLUG_RE.fullmatch(value):
            raise ValueError("slug 只能使用小写字母、数字和短横线")
        return value


class ProductWrite(ClientSchema):
    category_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=220)
    summary: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    after_sales: str | None = Field(default=None, max_length=10000)
    status: Literal["draft", "published", "hidden", "sold_out"] = "draft"
    featured: bool = False


class ProductSKUWrite(ClientSchema):
    inventory_item_id: int | None = None
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    attributes: dict[str, str] = Field(default_factory=dict)
    price: Decimal = Field(ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    original_price: Decimal | None = Field(
        default=None, ge=Decimal("0.00"), max_digits=12, decimal_places=2
    )
    stock_quantity: int = Field(default=0, ge=0, le=1000000)
    enabled: bool = True


class ProductPublishWrite(ClientSchema):
    product: ProductWrite
    sku: ProductSKUWrite


class CartItemAdd(ClientSchema):
    sku_id: int
    quantity: int = Field(default=1, ge=1, le=999)


class CartItemUpdate(ClientSchema):
    quantity: int | None = Field(default=None, ge=1, le=999)
    selected: bool | None = None


class OrderCreate(ClientSchema):
    address_id: int
    delivery_method: Literal["shipping", "store_pickup"] = "shipping"
    cart_item_ids: list[int] = Field(min_length=1, max_length=100)


class RetailOrderStatusUpdate(ClientSchema):
    status: Literal[
        "pending_payment",
        "paid",
        "processing",
        "shipped",
        "completed",
        "cancelled",
        "refunding",
        "refunded",
    ]
    tracking_no: str | None = Field(default=None, max_length=120)
    payment_reference: str | None = Field(default=None, max_length=160)


class RecycleEstimateInput(ClientSchema):
    catalog_item_id: int
    condition_codes: list[str] = Field(default_factory=list, max_length=40)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("condition_codes")
    @classmethod
    def unique_codes(cls, value: list[str]) -> list[str]:
        normalized = [item.strip()[:80] for item in value if item.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("估价条件不能重复")
        return normalized


class RecycleSave(ClientSchema):
    request_id: int | None = None
    catalog_item_id: int
    condition_codes: list[str] = Field(default_factory=list, max_length=40)
    details: dict[str, Any] = Field(default_factory=dict)
    contact_name: str = Field(min_length=1, max_length=120)
    contact_phone: str = Field(min_length=6, max_length=32)
    contact_wechat: str | None = Field(default=None, max_length=120)
    device_condition: str = Field(min_length=3, max_length=3000)
    notes: str | None = Field(default=None, max_length=2000)
    submit: bool = False

    _phone_format = field_validator("contact_phone")(_validate_phone)


class RecycleDecision(ClientSchema):
    decision: Literal["accepted", "rejected"]


class ClientReplacementCreate(ClientSchema):
    old_model: str = Field(min_length=1, max_length=200)
    desired_model: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=120)
    contact_phone: str = Field(min_length=6, max_length=32)
    address_id: int
    notes: str | None = Field(default=None, max_length=2000)
    _phone_format = field_validator("contact_phone")(_validate_phone)


class RecycleQuoteUpdate(ClientSchema):
    staff_quote: Decimal = Field(ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    status: Literal["quoted", "pending_customer_confirmation"] = "pending_customer_confirmation"


class RecycleCatalogWrite(ClientSchema):
    brand: str = Field(default="DJI", min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    variant: str | None = Field(default=None, max_length=160)
    reference_price: Decimal = Field(ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)


class RecycleRuleWrite(ClientSchema):
    code: str = Field(min_length=2, max_length=80)
    rule_group: str = Field(min_length=2, max_length=60)
    label: str = Field(min_length=1, max_length=160)
    factor: Decimal | None = Field(default=None, ge=Decimal("0.0000"), le=Decimal("2.0000"))
    adjustment: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)


class ClientRepairCreate(ClientSchema):
    device_id: int | None = None
    brand: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=160)
    serial_number: str | None = Field(default=None, max_length=160)
    fault_type: str = Field(min_length=1, max_length=80)
    fault_description: str = Field(min_length=5, max_length=5000)
    service_mode: Literal["shipping", "store"]
    has_water_damage: bool = False
    has_crash_damage: bool = False
    was_disassembled: bool = False
    current_state: str | None = Field(default=None, max_length=200)
    accessories: list[str] = Field(default_factory=list, max_length=50)
    contact_name: str = Field(min_length=1, max_length=120)
    contact_phone: str = Field(min_length=6, max_length=32)
    address_id: int | None = None
    notes: str | None = Field(default=None, max_length=2000)
    _phone_format = field_validator("contact_phone")(_validate_phone)

    @model_validator(mode="after")
    def device_source(self):
        if self.device_id is None and not all((self.brand, self.model, self.serial_number)):
            raise ValueError("请选择已有设备，或完整填写品牌、型号和序列号")
        if self.service_mode == "shipping" and self.address_id is None:
            raise ValueError("寄修必须选择收货地址")
        return self


class QuoteDecision(ClientSchema):
    decision: Literal["accepted", "rejected"]


class ForumPostCreate(ClientSchema):
    category_id: int
    title: str = Field(min_length=2, max_length=180)
    content: str = Field(min_length=2, max_length=20000)


class ForumPostUpdate(ClientSchema):
    category_id: int | None = None
    title: str | None = Field(default=None, min_length=2, max_length=180)
    content: str | None = Field(default=None, min_length=2, max_length=20000)


class ForumCommentCreate(ClientSchema):
    content: str = Field(min_length=1, max_length=3000)
    parent_id: int | None = None


class ForumReportCreate(ClientSchema):
    post_id: int | None = None
    comment_id: int | None = None
    reason: str = Field(min_length=2, max_length=500)

    @model_validator(mode="after")
    def exactly_one_target(self):
        if (self.post_id is None) == (self.comment_id is None):
            raise ValueError("帖子和评论必须且只能选择一个举报目标")
        return self


class ForumSignalItem(ClientSchema):
    post_id: int = Field(gt=0)
    impression: bool = False
    dwell_time_ms: int = Field(default=0, ge=0, le=300_000)
    not_interested: bool | None = None

    @model_validator(mode="after")
    def has_signal(self):
        if not self.impression and not self.dwell_time_ms and self.not_interested is None:
            raise ValueError("至少提交一种论坛推荐信号")
        return self


class ForumSignalBatch(ClientSchema):
    items: list[ForumSignalItem] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def unique_posts(cls, value: list[ForumSignalItem]) -> list[ForumSignalItem]:
        post_ids = [item.post_id for item in value]
        if len(post_ids) != len(set(post_ids)):
            raise ValueError("同一次提交中的帖子不能重复")
        return value


class ForumModeration(ClientSchema):
    status: Literal["published", "hidden", "rejected"] | None = None
    is_pinned: bool | None = None
    is_featured: bool | None = None


class ForumCategoryUpdate(ClientSchema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    sort_order: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class ForumCategoryCreate(ClientSchema):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    sort_order: int = Field(default=0, ge=0, le=10000)
    enabled: bool = True


class ForumReportModeration(ClientSchema):
    status: Literal["pending", "resolved", "dismissed"]


class ClientAccountStatusUpdate(ClientSchema):
    status: Literal["active", "disabled"]


class NotificationReadUpdate(ClientSchema):
    is_read: bool = True
