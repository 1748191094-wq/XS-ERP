from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.types import TypeDecorator

from app.core.database import Base
from app.core.inventory_quantity import inventory_quantity


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TZDateTime(TypeDecorator[datetime]):
    """跨 SQLite/PostgreSQL/MySQL 保存带时区 ISO-8601 时间。"""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value: str | datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class DailyNumberCounter(Base):
    """Atomic daily counters used for short, human-readable business numbers."""

    __tablename__ = "daily_number_counters"
    __table_args__ = (
        UniqueConstraint("scope", "counter_date", name="uq_daily_number_counter_scope_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    counter_date: Mapped[date] = mapped_column(Date, index=True)
    current_value: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, nullable=False)


class RepairOrderNumberReservation(Base):
    """Persist a generated repair-order number before it is handed to a caller."""

    __tablename__ = "repair_order_number_reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    reserved_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, nullable=False)


class SoftDeleteMixin:
    """Keep business history recoverable instead of physically deleting rows."""

    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    deleted_by: Mapped[int | None] = mapped_column(Integer, index=True)
    deletion_batch_id: Mapped[str | None] = mapped_column(String(36), index=True)


class Customer(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), index=True, nullable=True)
    wechat: Mapped[str | None] = mapped_column(String(120))
    wecom_external_user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    wecom_group_id: Mapped[str | None] = mapped_column(String(128), index=True)
    customer_type: Mapped[str] = mapped_column(String(32), default="individual")
    company_name: Mapped[str | None] = mapped_column(String(200))
    province: Mapped[str | None] = mapped_column(String(60))
    city: Mapped[str | None] = mapped_column(String(60))
    address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    devices: Mapped[list[DroneDevice]] = relationship(back_populates="customer")
    repair_orders: Mapped[list[RepairOrder]] = relationship(back_populates="customer")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    employee_no: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(50), default="engineer")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    wecom_userid: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class DeletedRecord(Base):
    """One reversible administrator deletion operation."""

    __tablename__ = "deleted_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    resource_type: Mapped[str] = mapped_column(String(40), index=True)
    resource_id: Mapped[int] = mapped_column(Integer, index=True)
    label: Mapped[str] = mapped_column(String(300))
    deleted_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    deleted_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    restored_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    restored_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)


class CustomerNoteRevision(Base):
    """Internal-only, append-only customer note history."""

    __tablename__ = "customer_note_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    note_type: Mapped[str] = mapped_column(String(20), index=True)
    service_group_id: Mapped[int | None] = mapped_column(ForeignKey("processing_groups.id"), index=True)
    previous_content: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    changed_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    customer: Mapped[Customer] = relationship()
    service_group: Mapped[ProcessingGroup | None] = relationship()
    actor: Mapped[User] = relationship()


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(96))
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    ip_address: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    user: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    username: Mapped[str | None] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(120))
    method: Mapped[str | None] = mapped_column(String(12))
    path: Mapped[str | None] = mapped_column(String(500))
    status_code: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)


class BackupRecord(Base):
    __tablename__ = "backup_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    storage_path: Mapped[str] = mapped_column(String(600), unique=True)
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    database_kind: Mapped[str] = mapped_column(String(30), default="sqlite")
    status: Mapped[str] = mapped_column(String(30), default="verified", index=True)
    integrity_result: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class DroneDevice(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "drone_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    brand: Mapped[str] = mapped_column(String(80), default="DJI")
    model: Mapped[str] = mapped_column(String(160), index=True)
    # 二手流转时，同一序列号可对应多条归属记录。
    serial_number: Mapped[str] = mapped_column(String(160), index=True)
    activation_date: Mapped[date | None] = mapped_column(Date)
    purchase_date: Mapped[date | None] = mapped_column(Date)
    warranty_status: Mapped[str | None] = mapped_column(String(50))
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False)
    remarks: Mapped[str | None] = mapped_column(Text)
    customer: Mapped[Customer] = relationship(back_populates="devices")
    repair_orders: Mapped[list[RepairOrder]] = relationship(back_populates="device")


class RepairOrder(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "repair_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    source_request_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("drone_devices.id"), index=True)
    fault_description: Mapped[str] = mapped_column(Text)
    intake_condition: Mapped[str | None] = mapped_column(Text)
    intake_accessories: Mapped[str | None] = mapped_column(Text)
    engineer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    processing_group_id: Mapped[int | None] = mapped_column(ForeignKey("processing_groups.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending_inspection", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    received_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expected_finish_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    total_quote_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_received: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    internal_notes: Mapped[str | None] = mapped_column(Text)
    customer_notes: Mapped[str | None] = mapped_column(Text)
    customer: Mapped[Customer] = relationship(back_populates="repair_orders")
    device: Mapped[DroneDevice] = relationship(back_populates="repair_orders")
    engineer: Mapped[User | None] = relationship()
    processing_group: Mapped[ProcessingGroup | None] = relationship()
    status_history: Mapped[list[RepairOrderStatusHistory]] = relationship(back_populates="repair_order", cascade="all, delete-orphan")
    quotes: Mapped[list[Quote]] = relationship(back_populates="repair_order")


class RepairOrderStatusHistory(Base):
    __tablename__ = "repair_order_status_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40))
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    repair_order: Mapped[RepairOrder] = relationship(back_populates="status_history")


class WorkOrderGroup(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "work_order_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    members: Mapped[list[WorkOrderGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class WorkOrderGroupMember(Base):
    __tablename__ = "work_order_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "repair_order_id", name="uq_work_order_group_member"),
        UniqueConstraint("repair_order_id", name="uq_work_order_group_single_membership"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("work_order_groups.id"), index=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    added_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    group: Mapped[WorkOrderGroup] = relationship(back_populates="members")
    repair_order: Mapped[RepairOrder] = relationship()


class ProcessingGroup(TimestampMixin, Base):
    __tablename__ = "processing_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    group_type: Mapped[str] = mapped_column(String(40), default="service", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    members: Mapped[list[ProcessingGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class ProcessingGroupMember(Base):
    __tablename__ = "processing_group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_processing_group_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("processing_groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    member_role: Mapped[str] = mapped_column(String(30), default="member")
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    group: Mapped[ProcessingGroup] = relationship(back_populates="members")
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class ServiceTicket(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "service_tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ticket_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("drone_devices.id"), index=True)
    repair_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("repair_orders.id"), unique=True, index=True
    )
    current_owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    processing_group_id: Mapped[int | None] = mapped_column(ForeignKey("processing_groups.id"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    due_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    first_response_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    last_reminded_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    replacement_inspection_result: Mapped[str | None] = mapped_column(Text)
    trade_in_credit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    return_reference: Mapped[str | None] = mapped_column(String(200))
    outbound_to_customer_tracking_no: Mapped[str | None] = mapped_column(String(120))
    customer: Mapped[Customer | None] = relationship()
    device: Mapped[DroneDevice | None] = relationship()
    repair_order: Mapped[RepairOrder | None] = relationship()
    current_owner: Mapped[User | None] = relationship(foreign_keys=[current_owner_id])
    processing_group: Mapped[ProcessingGroup | None] = relationship()
    collaborators: Mapped[list[ServiceTicketCollaborator]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    notes: Mapped[list[ServiceTicketNote]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    timeline: Mapped[list[ServiceTicketTimeline]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    quotes: Mapped[list[Quote]] = relationship(back_populates="service_ticket")


class ServiceTicketCollaborator(Base):
    __tablename__ = "service_ticket_collaborators"
    __table_args__ = (UniqueConstraint("ticket_id", "user_id", name="uq_ticket_collaborator"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    collaborator_role: Mapped[str] = mapped_column(String(30), default="assistant")
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    ticket: Mapped[ServiceTicket] = relationship(back_populates="collaborators")
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class ServiceTicketNote(Base):
    __tablename__ = "service_ticket_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    visibility: Mapped[str] = mapped_column(String(30), default="internal", index=True)
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    ticket: Mapped[ServiceTicket] = relationship(back_populates="notes")
    author: Mapped[User | None] = relationship()


class ServiceTicketTimeline(Base):
    __tablename__ = "service_ticket_timeline"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    summary: Mapped[str] = mapped_column(String(300))
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    ticket: Mapped[ServiceTicket] = relationship(back_populates="timeline")
    actor: Mapped[User | None] = relationship()


class SpecialistEscalation(TimestampMixin, Base):
    __tablename__ = "specialist_escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    escalation_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    service_ticket_id: Mapped[int] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    problem_summary: Mapped[str] = mapped_column(Text)
    attempted_solutions: Mapped[str] = mapped_column(Text)
    urgency: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    status: Mapped[str] = mapped_column(String(30), default="submitted", index=True)
    assigned_specialist_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    specialist_group_id: Mapped[int | None] = mapped_column(ForeignKey("processing_groups.id"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    returned_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    return_reason: Mapped[str | None] = mapped_column(Text)
    specialist_opinion: Mapped[str | None] = mapped_column(Text)
    solution: Mapped[str | None] = mapped_column(Text)
    final_result: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    ticket: Mapped[ServiceTicket] = relationship()
    assigned_specialist: Mapped[User | None] = relationship(foreign_keys=[assigned_specialist_id])
    specialist_group: Mapped[ProcessingGroup | None] = relationship()


class Quote(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint("repair_order_id", "version", name="uq_quote_order_version"),
        UniqueConstraint("service_ticket_id", "version", name="uq_quote_ticket_version"),
        CheckConstraint(
            "(repair_order_id IS NOT NULL) <> (service_ticket_id IS NOT NULL)",
            name="ck_quote_exactly_one_target",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    service_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    labor_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    # 报价版本冻结评估上下文，避免后续工单变更影响 PDF。
    assessment_result: Mapped[str | None] = mapped_column(Text)
    assessment_responsibility: Mapped[str | None] = mapped_column(String(80))
    repair_recommendation: Mapped[str | None] = mapped_column(Text)
    customer_notice: Mapped[str | None] = mapped_column(Text)
    payment_url: Mapped[str | None] = mapped_column(String(2048))
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    repair_order: Mapped[RepairOrder | None] = relationship(back_populates="quotes")
    service_ticket: Mapped[ServiceTicket | None] = relationship(back_populates="quotes")
    items: Mapped[list[QuoteItem]] = relationship(back_populates="quote", cascade="all, delete-orphan", order_by="QuoteItem.sort_order")


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    contact: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class InventoryItem(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "inventory_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str | None] = mapped_column(String(80))
    compatible_models: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(30), default="件")
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    sale_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    stock_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0.000"))
    safety_stock: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0.000"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    location: Mapped[str | None] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 维修物料默认不公开，需管理员显式授权展示。
    client_visible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    supplier: Mapped[Supplier | None] = relationship()

    @validates("stock_quantity", "safety_stock")
    def validate_whole_quantity(self, _key: str, value: object) -> Decimal:
        return inventory_quantity(value)


class QuoteItem(Base):
    __tablename__ = "quote_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), index=True)
    inventory_item_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_items.id"))
    item_name: Mapped[str] = mapped_column(String(200))
    specification: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1.000"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    item_type: Mapped[str] = mapped_column(String(30), default="part")
    remarks: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    quote: Mapped[Quote] = relationship(back_populates="items")
    inventory_item: Mapped[InventoryItem | None] = relationship()


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String(30), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    before_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    after_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    purchase_order_item_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order_items.id"), index=True)
    inventory_lot_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_lots.id"), index=True)
    stocktake_id: Mapped[int | None] = mapped_column(ForeignKey("stocktakes.id"), index=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    remarks: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    inventory_item: Mapped[InventoryItem] = relationship()

    @validates("quantity", "before_quantity", "after_quantity")
    def validate_whole_quantity(self, _key: str, value: object) -> Decimal:
        return inventory_quantity(value)


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    ordered_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    expected_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    supplier: Mapped[Supplier] = relationship()
    items: Mapped[list[PurchaseOrderItem]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan", order_by="PurchaseOrderItem.id"
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    sku_snapshot: Mapped[str] = mapped_column(String(80))
    item_name_snapshot: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0.000"))
    returned_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0.000"))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    remarks: Mapped[str | None] = mapped_column(Text)
    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    inventory_item: Mapped[InventoryItem] = relationship()

    @validates("quantity", "received_quantity", "returned_quantity")
    def validate_whole_quantity(self, _key: str, value: object) -> Decimal:
        return inventory_quantity(value)


class InventoryLot(Base):
    __tablename__ = "inventory_lots"
    id: Mapped[int] = mapped_column(primary_key=True)
    lot_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    purchase_order_item_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order_items.id"), index=True)
    quantity_received: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    serial_numbers_json: Mapped[list[str] | None] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)

    @validates("quantity_received", "quantity_remaining")
    def validate_whole_quantity(self, _key: str, value: object) -> Decimal:
        return inventory_quantity(value)


class Stocktake(TimestampMixin, Base):
    __tablename__ = "stocktakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    stocktake_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    committed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    items: Mapped[list[StocktakeItem]] = relationship(
        back_populates="stocktake", cascade="all, delete-orphan", order_by="StocktakeItem.id"
    )


class StocktakeItem(Base):
    __tablename__ = "stocktake_items"
    __table_args__ = (UniqueConstraint("stocktake_id", "inventory_item_id", name="uq_stocktake_inventory_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    stocktake_id: Mapped[int] = mapped_column(ForeignKey("stocktakes.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    system_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    counted_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    difference_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    remarks: Mapped[str | None] = mapped_column(Text)
    stocktake: Mapped[Stocktake] = relationship(back_populates="items")
    inventory_item: Mapped[InventoryItem] = relationship()

    @validates("system_quantity", "counted_quantity", "difference_quantity")
    def validate_whole_quantity(self, _key: str, value: object) -> Decimal:
        return inventory_quantity(value)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    attachment_type: Mapped[str] = mapped_column(String(40), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(600), unique=True)
    content_type: Mapped[str | None] = mapped_column(String(120))
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class FinanceTransaction(SoftDeleteMixin, Base):
    __tablename__ = "finance_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"), index=True)
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(60))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str | None] = mapped_column(String(50))
    paid_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    description: Mapped[str | None] = mapped_column(Text)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    quote: Mapped[Quote | None] = relationship()


class FlightLog(Base):
    __tablename__ = "flight_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("drone_devices.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(600), unique=True)
    file_type: Mapped[str] = mapped_column(String(30), default="unknown")
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    parser_name: Mapped[str | None] = mapped_column(String(80))
    parser_version: Mapped[str | None] = mapped_column(String(30))
    parse_status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    parse_progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    parsed_data_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    uploaded_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    parsed_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class Diagnosis(Base):
    __tablename__ = "diagnoses"
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    flight_log_id: Mapped[int | None] = mapped_column(ForeignKey("flight_logs.id"), index=True)
    diagnosis_type: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    suggested_actions: Mapped[str | None] = mapped_column(Text)
    requires_human_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed_result: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class CalibrationRecord(Base):
    __tablename__ = "calibration_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("drone_devices.id"), index=True)
    calibration_type: Mapped[str] = mapped_column(String(80))
    tool_name: Mapped[str] = mapped_column(String(160))
    tool_version: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    remarks: Mapped[str | None] = mapped_column(Text)


class DamageSopTemplate(TimestampMixin, Base):
    __tablename__ = "damage_sop_templates"
    __table_args__ = (
        UniqueConstraint(
            "brand", "model_pattern", "title", "version",
            name="uq_damage_sop_template_version",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String(80), default="通用", index=True)
    product_category: Mapped[str] = mapped_column(String(80), default="数码产品", index=True)
    series: Mapped[str | None] = mapped_column(String(120), index=True)
    model_pattern: Mapped[str] = mapped_column(String(160), default="*", index=True)
    title: Mapped[str] = mapped_column(String(240), index=True)
    version: Mapped[str] = mapped_column(String(40), default="1.0")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    description: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(String(600))
    access_level: Mapped[str] = mapped_column(String(30), default="internal", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    steps: Mapped[list[DamageSopStep]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="DamageSopStep.sort_order"
    )


class PointMap(TimestampMixin, Base):
    __tablename__ = "point_maps"
    __table_args__ = (
        UniqueConstraint(
            "brand", "model_pattern", "module_name", "title", "version",
            name="uq_point_map_version",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String(80), default="通用", index=True)
    product_category: Mapped[str] = mapped_column(String(80), default="数码产品", index=True)
    series: Mapped[str | None] = mapped_column(String(120), index=True)
    model_pattern: Mapped[str] = mapped_column(String(160), default="*", index=True)
    module_name: Mapped[str] = mapped_column(String(160), index=True)
    board_code: Mapped[str | None] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(240), index=True)
    version: Mapped[str] = mapped_column(String(40), default="1.0")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    image_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"), index=True)
    source_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"), index=True)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_reference: Mapped[str | None] = mapped_column(String(600))
    access_level: Mapped[str] = mapped_column(String(30), default="internal", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    markers: Mapped[list[PointMarker]] = relationship(
        back_populates="point_map", cascade="all, delete-orphan", order_by="PointMarker.sort_order"
    )


class PointMarker(Base):
    __tablename__ = "point_markers"
    __table_args__ = (
        UniqueConstraint("point_map_id", "marker_code", name="uq_point_map_marker_code"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    point_map_id: Mapped[int] = mapped_column(ForeignKey("point_maps.id"), index=True)
    marker_code: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    x_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    y_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    label: Mapped[str] = mapped_column(String(200))
    component_ref: Mapped[str | None] = mapped_column(String(160))
    function_description: Mapped[str | None] = mapped_column(Text)
    voltage_spec: Mapped[str | None] = mapped_column(String(160))
    current_spec: Mapped[str | None] = mapped_column(String(160))
    marker_type: Mapped[str] = mapped_column(String(30), default="measurement", index=True)
    measurement_kind: Mapped[str | None] = mapped_column(String(80))
    expected_value: Mapped[str | None] = mapped_column(String(160))
    tolerance: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(30))
    probe_hint: Mapped[str | None] = mapped_column(Text)
    risk_note: Mapped[str | None] = mapped_column(Text)
    point_map: Mapped[PointMap] = relationship(back_populates="markers")


class DamageSopStep(Base):
    __tablename__ = "damage_sop_steps"
    __table_args__ = (
        UniqueConstraint("template_id", "step_code", name="uq_damage_sop_step_code"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("damage_sop_templates.id"), index=True)
    step_code: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str | None] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(240))
    instruction: Mapped[str] = mapped_column(Text)
    check_type: Mapped[str] = mapped_column(String(30), default="visual", index=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    module_name: Mapped[str | None] = mapped_column(String(160), index=True)
    expected_result: Mapped[str | None] = mapped_column(Text)
    fail_conclusion: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    point_map_id: Mapped[int | None] = mapped_column(ForeignKey("point_maps.id"), index=True)
    point_marker_id: Mapped[int | None] = mapped_column(ForeignKey("point_markers.id"), index=True)
    template: Mapped[DamageSopTemplate] = relationship(back_populates="steps")
    point_map: Mapped[PointMap | None] = relationship(foreign_keys=[point_map_id])
    point_marker: Mapped[PointMarker | None] = relationship(foreign_keys=[point_marker_id])


class DamageAssessment(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "damage_assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("drone_devices.id"), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("damage_sop_templates.id"), index=True)
    template_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="in_progress", index=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    conclusion: Mapped[str | None] = mapped_column(Text)
    responsibility: Mapped[str | None] = mapped_column(String(80))
    repair_recommendation: Mapped[str | None] = mapped_column(Text)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    results: Mapped[list[DamageAssessmentResult]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="DamageAssessmentResult.sort_order"
    )


class DamageAssessmentResult(Base):
    __tablename__ = "damage_assessment_results"
    __table_args__ = (
        UniqueConstraint("assessment_id", "step_code", name="uq_damage_assessment_step_code"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("damage_assessments.id"), index=True)
    sop_step_id: Mapped[int | None] = mapped_column(ForeignKey("damage_sop_steps.id"), index=True)
    step_code: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    step_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    measured_value: Mapped[str | None] = mapped_column(String(240))
    unit: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    evidence_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"), index=True)
    point_marker_id: Mapped[int | None] = mapped_column(ForeignKey("point_markers.id"), index=True)
    completed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    assessment: Mapped[DamageAssessment] = relationship(back_populates="results")


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="sf_express")
    tracking_no: Mapped[str | None] = mapped_column(String(100), index=True)
    sender_info_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    receiver_info_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    logistics_status: Mapped[str] = mapped_column(String(40), default="draft")
    external_order_no: Mapped[str | None] = mapped_column(String(120))
    label_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    logistics_status: Mapped[str] = mapped_column(String(40), index=True)
    location: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class FollowUpTask(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "follow_up_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    follow_up_type: Mapped[str] = mapped_column(String(50), default="repair_satisfaction")
    scheduled_at: Mapped[datetime] = mapped_column(TZDateTime(), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    content: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    result: Mapped[str | None] = mapped_column(Text)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class TaskRecord(Base):
    __tablename__ = "task_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    task_type: Mapped[str] = mapped_column(String(50), index=True)
    related_type: Mapped[str | None] = mapped_column(String(50))
    related_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class EmailDelivery(Base):
    __tablename__ = "email_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), index=True)
    repair_order_id: Mapped[int] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    task_record_id: Mapped[int | None] = mapped_column(ForeignKey("task_records.id"), unique=True)
    recipient: Mapped[str] = mapped_column(String(254), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    message: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(40), default="mock")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attachment_path: Mapped[str | None] = mapped_column(String(600))
    error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class OutboundCall(TimestampMixin, Base):
    __tablename__ = "outbound_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    service_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    contact_number: Mapped[str] = mapped_column(String(32), index=True)
    purpose: Mapped[str] = mapped_column(String(160))
    planned_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    actual_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    result: Mapped[str | None] = mapped_column(String(30), index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    customer_intent: Mapped[str | None] = mapped_column(String(80))
    next_contact_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    recording_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    provider: Mapped[str] = mapped_column(String(40), default="manual")
    external_call_id: Mapped[str | None] = mapped_column(String(160), index=True)


class CustomEmailTemplate(SoftDeleteMixin, TimestampMixin, Base):
    """Administrator-managed email copy; built-in templates stay code-owned."""

    __tablename__ = "custom_email_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_type: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class OutboundEmail(TimestampMixin, Base):
    __tablename__ = "outbound_emails"
    id: Mapped[int] = mapped_column(primary_key=True)
    email_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    template_type: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    service_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    repair_order_id: Mapped[int | None] = mapped_column(ForeignKey("repair_orders.id"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"), index=True)
    task_record_id: Mapped[int | None] = mapped_column(ForeignKey("task_records.id"), unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    recipient: Mapped[str] = mapped_column(String(254), index=True)
    cc_json: Mapped[list[str] | None] = mapped_column(JSON)
    bcc_json: Mapped[list[str] | None] = mapped_column(JSON)
    subject_snapshot: Mapped[str] = mapped_column(String(300))
    body_snapshot: Mapped[str] = mapped_column(Text)
    attachment_snapshot_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(40), default="pending")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    error_message: Mapped[str | None] = mapped_column(Text)


class SystemSetting(TimestampMixin, Base):
    __tablename__ = "system_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str | None] = mapped_column(String(300))
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncNode(TimestampMixin, Base):
    __tablename__ = "sync_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="terminal", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    last_ip: Mapped[str | None] = mapped_column(String(80))


class SyncEntityState(TimestampMixin, Base):
    __tablename__ = "sync_entity_states"
    __table_args__ = (
        UniqueConstraint("entity_type", "record_key", name="uq_sync_entity_state_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    record_key: Mapped[str] = mapped_column(String(240), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    server_revision: Mapped[int] = mapped_column(Integer, default=0)


class SyncOutboxEvent(Base):
    __tablename__ = "sync_outbox_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    origin_node_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    record_key: Mapped[str] = mapped_column(String(240), index=True)
    operation: Mapped[str] = mapped_column(String(20), default="upsert")
    base_revision: Mapped[int] = mapped_column(Integer, default=0)
    base_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class SyncCanonicalRecord(TimestampMixin, Base):
    __tablename__ = "sync_canonical_records"
    __table_args__ = (
        UniqueConstraint("entity_type", "record_key", name="uq_sync_canonical_record_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    record_key: Mapped[str] = mapped_column(String(240), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    origin_node_id: Mapped[str] = mapped_column(String(36), index=True)


class SyncServerChange(Base):
    __tablename__ = "sync_server_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    origin_node_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    record_key: Mapped[str] = mapped_column(String(240), index=True)
    operation: Mapped[str] = mapped_column(String(20), default="upsert")
    revision: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)


class SyncConflict(Base):
    __tablename__ = "sync_conflicts"
    id: Mapped[int] = mapped_column(primary_key=True)
    conflict_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    event_id: Mapped[str] = mapped_column(String(36), index=True)
    origin_node_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    record_key: Mapped[str] = mapped_column(String(240), index=True)
    base_revision: Mapped[int] = mapped_column(Integer)
    current_revision: Mapped[int] = mapped_column(Integer)
    base_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    incoming_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    current_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    conflicting_fields_json: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    resolution: Mapped[str | None] = mapped_column(String(30))


Index("ix_follow_up_due", FollowUpTask.status, FollowUpTask.scheduled_at)
