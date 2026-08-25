from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.services.email_templates import EMAIL_TEMPLATE_CATEGORIES


VALID_ROLES = {
    "admin", "manager", "receptionist", "engineer", "technical_support",
    "finance", "warehouse", "viewer", "call_operator",
}
VALID_TICKET_TYPES = {
    "repair", "consultation", "quote_followup", "after_sales_followup",
    "complaint", "logistics_exception", "technical_support", "specialist_assistance", "retail", "replacement",
}
VALID_TICKET_STATUSES = {"open", "assigned", "in_progress", "waiting_customer", "waiting_internal", "resolved", "closed", "cancelled"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_ESCALATION_STATUSES = {"submitted", "accepted", "returned", "in_progress", "completed", "cancelled"}
VALID_CALL_RESULTS = {"connected", "no_answer", "rejected", "busy", "invalid_number", "callback", "other"}
VALID_SHIPMENT_STATUSES = {"draft", "pending_submit", "created", "picked_up", "in_transit", "delivered", "exception", "cancelled"}
VALID_QUOTE_ITEM_TYPES = {"part", "service", "labor", "material", "shipping", "other"}
VALID_SOP_STATUSES = {"draft", "published", "archived"}
VALID_SOP_CHECK_TYPES = {"visual", "measurement", "functional", "decision", "photo", "other"}
VALID_SOP_RISK_LEVELS = {"normal", "caution", "danger"}
VALID_POINT_MARKER_TYPES = {"measurement", "connector", "component", "risk", "reference"}
VALID_KNOWLEDGE_ACCESS_LEVELS = {"internal", "restricted"}
VALID_ASSESSMENT_RESULTS = {"pending", "pass", "fail", "na"}
VALID_ASSESSMENT_STATUSES = {"in_progress", "completed", "cancelled"}
RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EmailTemplateType = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$"),
]


def normalize_payment_url(value: str | None) -> str | None:
    """Validate a customer-facing payment URL without rewriting its query string."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("付款链接必须是文本")
    text = value.strip()
    if not text:
        return None
    if len(text) > 2048:
        raise ValueError("付款链接不能超过 2048 个字符")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in text) or "\\" in text:
        raise ValueError("付款链接不能包含空白符、控制字符或反斜杠")
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("付款链接格式不正确") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("付款链接必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("付款链接不能包含用户名或密码")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("付款链接端口不正确")
    return text


def _validate_password(value: str) -> str:
    if len(value) < 5 or not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise ValueError("密码至少 5 位，且必须同时包含字母和数字")
    return value


class AuthSetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    brand_name: str = Field(min_length=1, max_length=60)

    @field_validator("brand_name")
    @classmethod
    def valid_brand_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("请填写商标名称")
        if any(ord(character) < 32 or character in "<>" for character in value):
            raise ValueError("商标名称包含不支持的字符")
        return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=5, max_length=128)

    @field_validator("new_password")
    @classmethod
    def secure_password(cls, value: str) -> str:
        return _validate_password(value)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    role: str = "engineer"
    wecom_userid: str | None = Field(default=None, max_length=128)
    employee_no: str | None = Field(default=None, min_length=3, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        if value not in VALID_ROLES:
            raise ValueError("未知角色")
        return value

    @field_validator("wecom_userid")
    @classmethod
    def clean_wecom_userid(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value else ""
        return cleaned or None


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = None
    enabled: bool | None = None
    wecom_userid: str | None = Field(default=None, max_length=128)
    employee_no: str | None = Field(default=None, min_length=3, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str | None) -> str | None:
        if value is not None and value not in VALID_ROLES:
            raise ValueError("未知角色")
        return value

    @field_validator("wecom_userid")
    @classmethod
    def clean_wecom_userid(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value else ""
        return cleaned or None


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(ORMModel):
    id: int
    username: str
    employee_no: str
    display_name: str
    role: str
    enabled: bool
    wecom_userid: str | None
    last_login_at: datetime | None
    created_at: datetime


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    wechat: str | None = None
    customer_type: str = "individual"
    company_name: str | None = None
    province: str | None = None
    city: str | None = None
    address: str | None = None
    notes: str | None = None

    @field_validator("phone", "email", "wechat", "company_name", "province", "city", "address", "notes", mode="before")
    @classmethod
    def empty_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def valid_customer_email(cls, value: str | None) -> str | None:
        if value and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("邮箱格式不正确")
        return value


class CustomerUpdate(BaseModel):
    """Editable customer fields; the customer number and timestamps stay immutable."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    wechat: str | None = None
    customer_type: str | None = None
    company_name: str | None = None
    province: str | None = None
    city: str | None = None
    address: str | None = None
    notes: str | None = None

    @field_validator("name", "phone", "email", "wechat", "customer_type", "company_name", "province", "city", "address", "notes", mode="before")
    @classmethod
    def trim_customer_update(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def valid_customer_email(cls, value: str | None) -> str | None:
        if value and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("邮箱格式不正确")
        return value

    @model_validator(mode="after")
    def validate_customer_update(self):
        if not self.model_fields_set:
            raise ValueError("至少需要修改一个客户字段")
        if "name" in self.model_fields_set and not self.name:
            raise ValueError("客户姓名不能为空")
        return self


class CustomerRead(ORMModel):
    id: int
    customer_no: str
    name: str
    phone: str | None
    email: str | None
    wechat: str | None
    customer_type: str
    company_name: str | None
    province: str | None
    city: str | None
    address: str | None
    notes: str | None
    created_at: datetime


class DeviceCreate(BaseModel):
    customer_id: int
    brand: str = "DJI"
    model: str = Field(min_length=1, max_length=160)
    serial_number: str = Field(min_length=1, max_length=160)
    activation_date: date | None = None
    purchase_date: date | None = None
    warranty_status: str | None = None
    is_temporary: bool = False
    remarks: str | None = None


class DeviceRead(ORMModel):
    id: int
    customer_id: int
    brand: str
    model: str
    serial_number: str
    warranty_status: str | None
    is_temporary: bool
    remarks: str | None


class RepairOrderCreate(BaseModel):
    customer_id: int
    device_id: int
    fault_description: str = Field(min_length=1)
    intake_condition: str | None = None
    intake_accessories: str | None = None
    engineer_id: int | None = None
    processing_group_id: int | None = None
    priority: str = "normal"
    expected_finish_at: datetime | None = None
    internal_notes: str | None = None
    customer_notes: str | None = None

    @field_validator("priority")
    @classmethod
    def valid_repair_priority(cls, value: str) -> str:
        if value not in VALID_PRIORITIES:
            raise ValueError("未知优先级")
        return value


class RepairOrderRead(ORMModel):
    id: int
    order_no: str
    customer_id: int
    device_id: int
    device_serial_number: str | None = None
    engineer_id: int | None
    processing_group_id: int | None
    fault_description: str
    intake_condition: str | None
    intake_accessories: str | None
    internal_notes: str | None
    customer_notes: str | None
    status: str
    priority: str
    received_at: datetime
    total_quote_amount: Decimal
    total_cost: Decimal
    total_received: Decimal
    gross_profit: Decimal
    created_at: datetime


class RepairInspectionUpdate(BaseModel):
    internal_notes: str = Field(min_length=1, max_length=20000)

    @field_validator("internal_notes")
    @classmethod
    def clean_internal_notes(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("人工检测结果不能为空")
        return value


class StatusChange(BaseModel):
    status: str
    reason: str | None = None
    changed_by: int | None = None


class ProcessingGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    group_type: str = Field(default="service", max_length=40)
    description: str | None = None
    member_ids: list[int] = Field(default_factory=list)


class ProcessingGroupRead(ORMModel):
    id: int
    name: str
    group_type: str
    enabled: bool
    description: str | None
    created_at: datetime


class ServiceTicketCreate(BaseModel):
    ticket_type: str
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    customer_id: int | None = None
    device_id: int | None = None
    repair_order_id: int | None = None
    current_owner_id: int | None = None
    processing_group_id: int | None = None
    collaborator_ids: list[int] = Field(default_factory=list)
    priority: str = "normal"
    due_at: datetime | None = None
    replacement_inspection_result: str | None = Field(default=None, max_length=12000)
    trade_in_credit: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    return_reference: str | None = Field(default=None, max_length=200)
    outbound_to_customer_tracking_no: str | None = Field(default=None, max_length=120)

    @field_validator("ticket_type")
    @classmethod
    def valid_ticket_type(cls, value: str) -> str:
        if value not in VALID_TICKET_TYPES:
            raise ValueError("未知的服务工单类型")
        return value

    @field_validator("priority")
    @classmethod
    def valid_ticket_priority(cls, value: str) -> str:
        if value not in VALID_PRIORITIES:
            raise ValueError("未知优先级")
        return value

    @field_validator("replacement_inspection_result", "return_reference", "outbound_to_customer_tracking_no", mode="before")
    @classmethod
    def normalize_replacement_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def replacement_fields_match_type(self):
        values = (
            self.replacement_inspection_result,
            self.trade_in_credit,
            self.return_reference,
            self.outbound_to_customer_tracking_no,
        )
        if self.ticket_type != "replacement" and any(value is not None for value in values):
            raise ValueError("置换业务字段仅适用于置换工单")
        return self


class ServiceTicketRead(ORMModel):
    id: int
    ticket_no: str
    ticket_type: str
    title: str
    description: str
    status: str
    priority: str
    customer_id: int | None
    device_id: int | None
    repair_order_id: int | None
    current_owner_id: int | None
    processing_group_id: int | None
    due_at: datetime | None
    first_response_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    reminder_count: int
    replacement_inspection_result: str | None
    trade_in_credit: Decimal | None
    return_reference: str | None
    outbound_to_customer_tracking_no: str | None
    created_at: datetime
    updated_at: datetime


class TicketAssignmentUpdate(BaseModel):
    current_owner_id: int | None = None
    processing_group_id: int | None = None
    reason: RequiredText


class ReplacementTicketUpdate(BaseModel):
    replacement_inspection_result: str | None = Field(default=None, max_length=12000)
    trade_in_credit: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    return_reference: str | None = Field(default=None, max_length=200)
    outbound_to_customer_tracking_no: str | None = Field(default=None, max_length=120)

    @field_validator("replacement_inspection_result", "return_reference", "outbound_to_customer_tracking_no", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class TicketCollaboratorAdd(BaseModel):
    user_id: int
    collaborator_role: str = Field(default="assistant", max_length=30)


class TicketStatusChange(BaseModel):
    status: str
    reason: RequiredText

    @field_validator("status")
    @classmethod
    def valid_ticket_status(cls, value: str) -> str:
        if value not in VALID_TICKET_STATUSES:
            raise ValueError("未知的服务工单状态")
        return value


class TicketTypeChange(BaseModel):
    ticket_type: str
    reason: RequiredText
    expected_ticket_type: str | None = None

    @field_validator("ticket_type", "expected_ticket_type")
    @classmethod
    def valid_ticket_type(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in VALID_TICKET_TYPES:
            raise ValueError("未知的服务工单类型")
        return value


class TicketNoteCreate(BaseModel):
    visibility: str = "internal"
    content: RequiredText

    @field_validator("visibility")
    @classmethod
    def valid_note_visibility(cls, value: str) -> str:
        if value not in {"internal", "customer"}:
            raise ValueError("备注可见性必须是 internal 或 customer")
        return value


class TicketReminder(BaseModel):
    reason: RequiredText


class TicketDescriptionUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=20000)
    reason: RequiredText


class SpecialistEscalationCreate(BaseModel):
    service_ticket_id: int
    reason: RequiredText
    problem_summary: RequiredText
    attempted_solutions: RequiredText
    urgency: str = "normal"
    assigned_specialist_id: int | None = None
    specialist_group_id: int | None = None

    @field_validator("urgency")
    @classmethod
    def valid_escalation_urgency(cls, value: str) -> str:
        if value not in VALID_PRIORITIES:
            raise ValueError("未知紧急程度")
        return value


class SpecialistEscalationUpdate(BaseModel):
    status: str
    return_reason: str | None = None
    specialist_opinion: str | None = None
    solution: str | None = None
    final_result: str | None = None

    @field_validator("status")
    @classmethod
    def valid_escalation_status(cls, value: str) -> str:
        if value not in VALID_ESCALATION_STATUSES:
            raise ValueError("未知升级状态")
        return value


class QuoteItemInput(BaseModel):
    inventory_item_id: int | None = None
    item_name: str = Field(min_length=1, max_length=200)
    specification: str | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    cost_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    item_type: str = "part"
    remarks: str | None = None
    sort_order: int = 0

    @field_validator("item_type")
    @classmethod
    def valid_item_type(cls, value: str) -> str:
        if value not in VALID_QUOTE_ITEM_TYPES:
            raise ValueError("未知的报价项目类型")
        return value


class QuoteCreate(BaseModel):
    repair_order_id: int | None = None
    service_ticket_id: int | None = None
    discount: Decimal = Field(default=Decimal("0.00"), ge=0)
    labor_fee: Decimal = Field(default=Decimal("0.00"), ge=0)
    shipping_fee: Decimal = Field(default=Decimal("0.00"), ge=0)
    assessment_result: str | None = Field(default=None, max_length=12000)
    assessment_responsibility: str | None = Field(default=None, max_length=80)
    repair_recommendation: str | None = Field(default=None, max_length=12000)
    customer_notice: str | None = Field(default=None, max_length=12000)
    payment_url: str | None = Field(default=None, max_length=2048)
    items: list[QuoteItemInput] = Field(default_factory=list)

    @field_validator("payment_url")
    @classmethod
    def valid_payment_url(cls, value: str | None) -> str | None:
        return normalize_payment_url(value)

    @model_validator(mode="after")
    def exactly_one_quote_target(self):
        if bool(self.repair_order_id) == bool(self.service_ticket_id):
            raise ValueError("维修工单与服务工单必须且只能选择一个")
        return self


class QuoteItemRead(ORMModel):
    id: int
    inventory_item_id: int | None
    item_name: str
    quantity: Decimal
    unit_price: Decimal
    cost_price: Decimal
    amount: Decimal
    item_type: str
    remarks: str | None
    sort_order: int


class QuoteRead(ORMModel):
    id: int
    quote_no: str
    repair_order_id: int | None
    service_ticket_id: int | None
    version: int
    status: str
    subtotal: Decimal
    discount: Decimal
    labor_fee: Decimal
    shipping_fee: Decimal
    total_amount: Decimal
    assessment_result: str | None
    assessment_responsibility: str | None
    repair_recommendation: str | None
    customer_notice: str | None
    payment_url: str | None
    customer_confirmed_at: datetime | None
    items: list[QuoteItemRead]


class InventoryItemCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    category: str | None = None
    compatible_models: str | None = None
    unit: str = "件"
    purchase_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    sale_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    stock_quantity: int = Field(default=0, ge=0)
    safety_stock: int = Field(default=0, ge=0)
    supplier_id: int | None = None
    location: str | None = None
    enabled: bool = True
    client_visible: bool = False


class InventoryClientVisibilityUpdate(BaseModel):
    client_visible: bool


class InventoryItemRead(ORMModel):
    id: int
    sku: str
    name: str
    category: str | None
    compatible_models: str | None
    unit: str
    purchase_price: Decimal
    sale_price: Decimal
    stock_quantity: int
    safety_stock: int
    supplier_id: int | None
    location: str | None
    enabled: bool
    client_visible: bool


class InventoryTransactionRead(ORMModel):
    id: int
    transaction_no: str
    inventory_item_id: int
    transaction_type: str
    quantity: int
    before_quantity: int
    after_quantity: int
    unit_cost: Decimal
    repair_order_id: int | None
    purchase_order_id: int | None
    purchase_order_item_id: int | None
    inventory_lot_id: int | None
    stocktake_id: int | None
    operator_id: int | None
    remarks: str | None
    created_at: datetime


class StockChange(BaseModel):
    inventory_item_id: int
    transaction_type: str
    quantity: int
    repair_order_id: int | None = None
    operator_id: int | None = None
    unit_cost: Decimal | None = Field(default=None, ge=0)
    remarks: str | None = None

    @field_validator("quantity")
    @classmethod
    def non_zero_quantity(cls, value: int) -> int:
        if value == 0:
            raise ValueError("数量不能为 0")
        return value


class FinanceCreate(BaseModel):
    repair_order_id: int | None = None
    quote_id: int | None = None
    purchase_order_id: int | None = None
    customer_id: int | None = None
    transaction_type: str
    category: str
    amount: Decimal = Field(gt=0)
    payment_method: str | None = None
    paid_at: datetime | None = None
    description: str | None = None
    attachment_id: int | None = None


class FinanceUpdate(FinanceCreate):
    """Editable finance fields; transaction number and idempotency key stay immutable."""


class FinanceRead(ORMModel):
    id: int
    transaction_no: str
    repair_order_id: int | None
    quote_id: int | None
    purchase_order_id: int | None
    customer_id: int | None
    transaction_type: str
    category: str
    amount: Decimal
    payment_method: str | None
    paid_at: datetime
    description: str | None


class DiagnosisCreate(BaseModel):
    repair_order_id: int
    flight_log_id: int | None = None
    diagnosis_type: str
    severity: str
    confidence: Decimal = Field(ge=0, le=1)
    title: str
    description: str
    evidence_json: dict[str, Any] | None = None
    suggested_actions: str | None = None


class CalibrationCreate(BaseModel):
    repair_order_id: int
    device_id: int
    calibration_type: str
    tool_name: str
    tool_version: str | None = None
    status: str
    result_json: dict[str, Any] | None = None
    operator_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    remarks: str | None = None


class DamageSopStepInput(BaseModel):
    step_code: str = Field(min_length=1, max_length=80)
    sort_order: int = Field(default=0, ge=0, le=100000)
    section: str | None = Field(default=None, max_length=120)
    title: RequiredText = Field(max_length=240)
    instruction: RequiredText = Field(max_length=8000)
    check_type: str = "visual"
    required: bool = True
    module_name: str | None = Field(default=None, max_length=160)
    expected_result: str | None = Field(default=None, max_length=4000)
    fail_conclusion: str | None = Field(default=None, max_length=4000)
    risk_level: str = "normal"
    point_map_id: int | None = None
    point_marker_id: int | None = None

    @field_validator("check_type")
    @classmethod
    def valid_check_type(cls, value: str) -> str:
        if value not in VALID_SOP_CHECK_TYPES:
            raise ValueError("未知的检查类型")
        return value

    @field_validator("risk_level")
    @classmethod
    def valid_risk_level(cls, value: str) -> str:
        if value not in VALID_SOP_RISK_LEVELS:
            raise ValueError("未知的风险等级")
        return value


class DamageSopTemplateCreate(BaseModel):
    brand: str = Field(default="通用", min_length=1, max_length=80)
    product_category: str = Field(default="数码产品", min_length=1, max_length=80)
    series: str | None = Field(default=None, max_length=120)
    model_pattern: str = Field(default="*", min_length=1, max_length=160)
    title: RequiredText = Field(max_length=240)
    version: str = Field(default="1.0", min_length=1, max_length=40)
    status: str = "draft"
    description: str | None = Field(default=None, max_length=8000)
    source_reference: str | None = Field(default=None, max_length=600)
    access_level: str = "internal"
    steps: list[DamageSopStepInput] = Field(default_factory=list, max_length=300)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in VALID_SOP_STATUSES:
            raise ValueError("未知的 SOP 状态")
        return value

    @field_validator("access_level")
    @classmethod
    def valid_access_level(cls, value: str) -> str:
        if value not in VALID_KNOWLEDGE_ACCESS_LEVELS:
            raise ValueError("未知的资料访问级别")
        return value


class PointMapCreate(BaseModel):
    brand: str = Field(default="通用", min_length=1, max_length=80)
    product_category: str = Field(default="数码产品", min_length=1, max_length=80)
    series: str | None = Field(default=None, max_length=120)
    model_pattern: str = Field(default="*", min_length=1, max_length=160)
    module_name: RequiredText = Field(max_length=160)
    board_code: str | None = Field(default=None, max_length=120)
    title: RequiredText = Field(max_length=240)
    version: str = Field(default="1.0", min_length=1, max_length=40)
    status: str = "draft"
    source_reference: str | None = Field(default=None, max_length=600)
    access_level: str = "internal"

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in VALID_SOP_STATUSES:
            raise ValueError("未知的点位图状态")
        return value

    @field_validator("access_level")
    @classmethod
    def valid_access_level(cls, value: str) -> str:
        if value not in VALID_KNOWLEDGE_ACCESS_LEVELS:
            raise ValueError("未知的资料访问级别")
        return value


class PointMarkerCreate(BaseModel):
    marker_code: str = Field(min_length=1, max_length=80)
    sort_order: int = Field(default=0, ge=0, le=100000)
    x_percent: Decimal = Field(ge=0, le=100)
    y_percent: Decimal = Field(ge=0, le=100)
    label: RequiredText = Field(max_length=200)
    component_ref: str | None = Field(default=None, max_length=160)
    function_description: str | None = Field(default=None, max_length=4000)
    voltage_spec: str | None = Field(default=None, max_length=160)
    current_spec: str | None = Field(default=None, max_length=160)
    marker_type: str = "measurement"
    measurement_kind: str | None = Field(default=None, max_length=80)
    expected_value: str | None = Field(default=None, max_length=160)
    tolerance: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=30)
    probe_hint: str | None = Field(default=None, max_length=4000)
    risk_note: str | None = Field(default=None, max_length=4000)

    @field_validator("marker_type")
    @classmethod
    def valid_marker_type(cls, value: str) -> str:
        if value not in VALID_POINT_MARKER_TYPES:
            raise ValueError("未知的点位类型")
        return value


class PointMarkerUpdate(BaseModel):
    label: RequiredText | None = Field(default=None, max_length=200)
    component_ref: str | None = Field(default=None, max_length=160)
    function_description: str | None = Field(default=None, max_length=4000)
    voltage_spec: str | None = Field(default=None, max_length=160)
    current_spec: str | None = Field(default=None, max_length=160)
    measurement_kind: str | None = Field(default=None, max_length=80)
    expected_value: str | None = Field(default=None, max_length=160)
    tolerance: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=30)
    probe_hint: str | None = Field(default=None, max_length=4000)
    risk_note: str | None = Field(default=None, max_length=4000)


class DamageAssessmentCreate(BaseModel):
    repair_order_id: int
    template_id: int
    operator_id: int | None = None


class DamageAssessmentResultUpdate(BaseModel):
    result: str
    measured_value: str | None = Field(default=None, max_length=240)
    unit: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=8000)
    evidence_attachment_id: int | None = None

    @field_validator("result")
    @classmethod
    def valid_result(cls, value: str) -> str:
        if value not in VALID_ASSESSMENT_RESULTS - {"pending"}:
            raise ValueError("未知的定损步骤结果")
        return value


class DamageAssessmentComplete(BaseModel):
    conclusion: RequiredText = Field(max_length=8000)
    responsibility: str | None = Field(default=None, max_length=80)
    repair_recommendation: str | None = Field(default=None, max_length=8000)
    estimated_cost: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999.99"))


class CalibrationLabSimulationRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=40)
    calibration_kind: str = Field(pattern=r"^(joint_coarse|linear_hall)$")


class ShipmentCreate(BaseModel):
    repair_order_id: int
    tracking_no: str | None = None
    sender_info_json: dict[str, Any] | None = None
    receiver_info_json: dict[str, Any] | None = None


class ShipmentUpdate(BaseModel):
    logistics_status: str
    tracking_no: str | None = Field(default=None, max_length=100)
    external_order_no: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=1)
    occurred_at: datetime | None = None

    @field_validator("logistics_status")
    @classmethod
    def valid_shipment_status(cls, value: str) -> str:
        if value not in VALID_SHIPMENT_STATUSES:
            raise ValueError("未知物流状态")
        return value


class FollowUpCreate(BaseModel):
    repair_order_id: int
    customer_id: int
    follow_up_type: str = Field(default="repair_satisfaction", max_length=50)
    scheduled_at: datetime
    content: str | None = None


class FollowUpUpdate(BaseModel):
    status: str
    result: str | None = None
    next_follow_up_at: datetime | None = None

    @field_validator("status")
    @classmethod
    def valid_followup_status(cls, value: str) -> str:
        if value not in {"pending", "completed", "cancelled"}:
            raise ValueError("未知回访状态")
        return value


class SettingInput(BaseModel):
    key: str
    value: str
    description: str | None = None


class EmailSendRequest(BaseModel):
    recipient: str | None = Field(default=None, max_length=254)
    subject: str | None = Field(default=None, max_length=300)
    message: str | None = Field(default=None, max_length=5000)

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("邮箱格式不正确")
        return value


class OutboundCallCreate(BaseModel):
    customer_id: int
    service_ticket_id: int | None = None
    repair_order_id: int | None = None
    assigned_to: int | None = None
    contact_number: str = Field(min_length=3, max_length=32)
    purpose: str = Field(min_length=1, max_length=160)
    planned_at: datetime | None = None


class OutboundCallComplete(BaseModel):
    result: str
    actual_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    summary: str = Field(min_length=1)
    customer_intent: str | None = Field(default=None, max_length=80)
    next_contact_at: datetime | None = None
    recording_attachment_id: int | None = None

    @field_validator("result")
    @classmethod
    def valid_call_result(cls, value: str) -> str:
        if value not in VALID_CALL_RESULTS:
            raise ValueError("未知外呼结果")
        return value


class EmailPreviewRequest(BaseModel):
    template_type: EmailTemplateType
    service_ticket_id: int | None = None
    repair_order_id: int | None = None
    quote_id: int | None = None


class EmailTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=40)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    enabled: bool = True

    @field_validator("name", "subject")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value

    @field_validator("body")
    @classmethod
    def non_blank_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("邮件正文不能为空")
        return value.replace("\r\n", "\n").replace("\r", "\n")

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        value = value.strip()
        if value not in EMAIL_TEMPLATE_CATEGORIES:
            raise ValueError("未知邮件模板分类")
        return value


class EmailTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=40)
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    enabled: bool | None = None

    @field_validator("name", "subject")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value

    @field_validator("body")
    @classmethod
    def non_blank_optional_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("邮件正文不能为空")
        return value.replace("\r\n", "\n").replace("\r", "\n")

    @field_validator("category")
    @classmethod
    def valid_optional_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value not in EMAIL_TEMPLATE_CATEGORIES:
            raise ValueError("未知邮件模板分类")
        return value

    @model_validator(mode="after")
    def at_least_one_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提交一个需要修改的字段")
        return self


class OutboundEmailCreate(EmailPreviewRequest):
    recipient: str | None = Field(default=None, max_length=254)
    cc: list[str] = Field(default_factory=list, max_length=20)
    bcc: list[str] = Field(default_factory=list, max_length=20)
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=30)
    auto_attach_report: bool = True
    attach_service_ticket_pdf: bool = False
    attach_repair_report_pdf: bool = False

    @field_validator("recipient")
    @classmethod
    def valid_optional_recipient(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("邮箱格式不正确")
        return value

    @field_validator("cc", "bcc")
    @classmethod
    def valid_address_lists(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value:
                continue
            if "@" not in value or value.startswith("@") or value.endswith("@"):
                raise ValueError(f"邮箱格式不正确：{value}")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned


class EmailConfigUpdate(BaseModel):
    mode: str = "mock"
    sender: str = Field(default="", max_length=254)
    smtp_host: str = Field(default="smtp.feishu.cn", max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    password: str | None = Field(default=None, max_length=500)
    clear_password: bool = False
    from_name: str = Field(default="服务中心", min_length=1, max_length=120)
    reply_to: str | None = Field(default=None, max_length=254)
    use_starttls: bool = True
    timeout_seconds: int = Field(default=12, ge=3, le=120)

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"mock", "smtp"}:
            raise ValueError("邮件模式只能是 mock 或 smtp")
        return value

    @field_validator("sender", "reply_to")
    @classmethod
    def valid_email_fields(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return "" if value == "" else None
        value = value.strip()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("邮箱格式不正确")
        return value


class QuickEntryItem(BaseModel):
    inventory_item_id: int | None = None
    item_name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    cost_price: Decimal = Field(default=Decimal("0"), ge=0)
    item_type: str = "part"
    remarks: str | None = None

    @field_validator("item_type")
    @classmethod
    def valid_item_type(cls, value: str) -> str:
        if value not in VALID_QUOTE_ITEM_TYPES:
            raise ValueError("未知的报价项目类型")
        return value


class QuickEntryCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    wechat: str | None = Field(default=None, max_length=120)
    customer_type: str = "individual"
    company_name: str | None = None
    address: str | None = None
    brand: str = "DJI"
    model: str = Field(min_length=1, max_length=160)
    serial_number: str | None = Field(default=None, max_length=160)
    warranty_status: str | None = None
    fault_description: str = Field(min_length=1)
    intake_condition: str | None = None
    intake_accessories: str | None = None
    priority: str = "normal"
    customer_notes: str | None = None
    labor_fee: Decimal = Field(default=Decimal("0"), ge=0)
    shipping_fee: Decimal = Field(default=Decimal("0"), ge=0)
    discount: Decimal = Field(default=Decimal("0"), ge=0)
    payment_url: str | None = Field(default=None, max_length=2048)
    items: list[QuickEntryItem] = Field(default_factory=list)
    generate_pdf: bool = True
    send_email: bool = False

    @field_validator("phone", "email", "wechat", "company_name", "address", "serial_number", "warranty_status", "intake_condition", "intake_accessories", "customer_notes", "payment_url", mode="before")
    @classmethod
    def quick_empty_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def quick_email(cls, value: str | None) -> str | None:
        if value and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("邮箱格式不正确")
        return value

    @field_validator("payment_url")
    @classmethod
    def quick_payment_url(cls, value: str | None) -> str | None:
        return normalize_payment_url(value)
