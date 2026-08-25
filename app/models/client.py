from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.entities import SoftDeleteMixin, TimestampMixin, TZDateTime, utcnow


class ClientAccount(TimestampMixin, Base):
    __tablename__ = "client_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), unique=True, index=True
    )
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    avatar_path: Mapped[str | None] = mapped_column(String(600))
    nickname: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    customer: Mapped[Any] = relationship("Customer")


class ClientSession(Base):
    __tablename__ = "client_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(96))
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    account: Mapped[ClientAccount] = relationship()


class ClientAddress(TimestampMixin, Base):
    __tablename__ = "client_addresses"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), index=True
    )
    recipient_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32))
    province: Mapped[str] = mapped_column(String(60))
    city: Mapped[str] = mapped_column(String(60))
    district: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(String(500))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ClientActionLog(Base):
    __tablename__ = "client_action_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("client_accounts.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(120))
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), default=utcnow, index=True
    )


class ClientAttachment(Base):
    __tablename__ = "client_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(40), index=True)
    resource_id: Mapped[int] = mapped_column(Integer, index=True)
    attachment_type: Mapped[str] = mapped_column(String(40), default="image")
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(600), unique=True)
    content_type: Mapped[str] = mapped_column(String(120))
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), default=utcnow, index=True
    )
    __table_args__ = (
        Index(
            "ix_client_attachment_resource",
            "account_id",
            "resource_type",
            "resource_id",
        ),
    )


class ProductCategory(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "product_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Product(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_categories.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    summary: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    after_sales: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    category: Mapped[ProductCategory | None] = relationship()
    skus: Mapped[list[ProductSKU]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    images: Mapped[list[ProductImage]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductSKU(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "product_skus"
    __table_args__ = (
        CheckConstraint("stock_quantity >= 0", name="ck_product_sku_stock_nonnegative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_product_sku_reserved_nonnegative"),
        CheckConstraint(
            "reserved_quantity <= stock_quantity",
            name="ck_product_sku_reserved_not_over_stock",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_items.id"), unique=True, index=True
    )
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    attributes_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    product: Mapped[Product] = relationship(back_populates="skus")
    inventory_item: Mapped[Any | None] = relationship("InventoryItem")


class ProductImage(Base):
    __tablename__ = "product_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    storage_path: Mapped[str] = mapped_column(String(600))
    alt_text: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    product: Mapped[Product] = relationship(back_populates="images")


class Cart(TimestampMixin, Base):
    __tablename__ = "client_carts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), unique=True, index=True
    )
    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart", cascade="all, delete-orphan"
    )


class CartItem(TimestampMixin, Base):
    __tablename__ = "client_cart_items"
    __table_args__ = (
        UniqueConstraint("cart_id", "sku_id", name="uq_client_cart_sku"),
        CheckConstraint("quantity > 0", name="ck_client_cart_item_quantity_positive"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("client_carts.id"), index=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("product_skus.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    cart: Mapped[Cart] = relationship(back_populates="items")
    sku: Mapped[ProductSKU] = relationship()


class RetailOrder(TimestampMixin, Base):
    __tablename__ = "retail_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    address_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    delivery_method: Mapped[str] = mapped_column(String(30), default="shipping")
    status: Mapped[str] = mapped_column(String(30), default="pending_payment", index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_provider: Mapped[str] = mapped_column(String(40), default="manual")
    payment_reference: Mapped[str | None] = mapped_column(String(160))
    tracking_no: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    items: Mapped[list[RetailOrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class RetailOrderItem(Base):
    __tablename__ = "retail_order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_retail_order_item_quantity_positive"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("retail_orders.id"), index=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("product_skus.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(200))
    sku_name: Mapped[str] = mapped_column(String(200))
    sku_code: Mapped[str] = mapped_column(String(80))
    attributes_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    order: Mapped[RetailOrder] = relationship(back_populates="items")


class RecycleCatalogItem(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "recycle_catalog_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String(80), default="DJI", index=True)
    model: Mapped[str] = mapped_column(String(160), index=True)
    variant: Mapped[str | None] = mapped_column(String(160))
    reference_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class RecycleRule(TimestampMixin, Base):
    __tablename__ = "recycle_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    rule_group: Mapped[str] = mapped_column(String(60), index=True)
    label: Mapped[str] = mapped_column(String(160))
    factor: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    adjustment: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class RecycleRequest(TimestampMixin, Base):
    __tablename__ = "recycle_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_no: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    catalog_item_id: Mapped[int] = mapped_column(ForeignKey("recycle_catalog_items.id"), index=True)
    service_ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_tickets.id"), unique=True, index=True
    )
    questionnaire_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    reference_min: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reference_max: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    staff_quote: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    user_decision: Mapped[str | None] = mapped_column(String(20))
    decision_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)


class ClientRepairIntake(TimestampMixin, Base):
    __tablename__ = "client_repair_intakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    repair_order_id: Mapped[int] = mapped_column(
        ForeignKey("repair_orders.id"), unique=True, index=True
    )
    service_mode: Mapped[str] = mapped_column(String(30))
    fault_type: Mapped[str] = mapped_column(String(80))
    has_water_damage: Mapped[bool] = mapped_column(Boolean, default=False)
    has_crash_damage: Mapped[bool] = mapped_column(Boolean, default=False)
    was_disassembled: Mapped[bool] = mapped_column(Boolean, default=False)
    current_state: Mapped[str | None] = mapped_column(String(200))
    contact_name: Mapped[str] = mapped_column(String(120))
    contact_phone: Mapped[str] = mapped_column(String(32))
    address_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    extra_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)


class ForumCategory(TimestampMixin, Base):
    __tablename__ = "forum_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(300))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ForumPost(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "forum_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("forum_categories.id"), index=True)
    title: Mapped[str] = mapped_column(String(180), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="published", index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    deleted_by_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("client_accounts.id"), index=True
    )
    author: Mapped[ClientAccount] = relationship(foreign_keys=[author_id])
    category: Mapped[ForumCategory] = relationship()


class ForumComment(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "forum_comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("forum_posts.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("forum_comments.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="published", index=True)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    deleted_by_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("client_accounts.id"), index=True
    )
    author: Mapped[ClientAccount] = relationship(foreign_keys=[author_id])


class ForumPostLike(Base):
    __tablename__ = "forum_post_likes"
    __table_args__ = (UniqueConstraint("post_id", "account_id", name="uq_forum_post_like"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("forum_posts.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class ForumCommentLike(Base):
    __tablename__ = "forum_comment_likes"
    __table_args__ = (
        UniqueConstraint("comment_id", "account_id", name="uq_forum_comment_like"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("forum_comments.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class ForumFavorite(Base):
    __tablename__ = "forum_favorites"
    __table_args__ = (
        UniqueConstraint("post_id", "account_id", name="uq_forum_favorite"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("forum_posts.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class ForumPostSignal(TimestampMixin, Base):
    """Per-viewer feed state used by the lightweight recommendation pipeline."""

    __tablename__ = "forum_post_signals"
    __table_args__ = (
        UniqueConstraint("post_id", "account_id", name="uq_forum_post_signal"),
        CheckConstraint(
            "impression_count >= 0", name="ck_forum_post_signal_impressions_nonnegative"
        ),
        CheckConstraint(
            "dwell_time_ms >= 0", name="ck_forum_post_signal_dwell_nonnegative"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("forum_posts.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    impression_count: Mapped[int] = mapped_column(Integer, default=0)
    dwell_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_impression_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    last_dwell_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    not_interested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    not_interested_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class ForumReport(Base):
    __tablename__ = "forum_reports"
    __table_args__ = (
        UniqueConstraint(
            "post_id", "comment_id", "reporter_id", name="uq_forum_report_target"
        ),
        CheckConstraint(
            "post_id IS NOT NULL OR comment_id IS NOT NULL",
            name="ck_forum_report_has_target",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("forum_posts.id"), index=True)
    comment_id: Mapped[int | None] = mapped_column(ForeignKey("forum_comments.id"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    handled_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)


class ClientNotification(Base):
    __tablename__ = "client_notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("client_accounts.id"), index=True)
    notification_type: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(String(1000))
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[int | None] = mapped_column(Integer)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
