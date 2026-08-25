"""客户平台基础：账号、商城、回收、维修入口、社区与通知。

Revision ID: 0028_client_platform
Revises: 0027_email_template_library
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0028_client_platform"
down_revision = "0027_email_template_library"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    ]


def _soft_delete() -> list[sa.Column]:
    return [
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.Column("deletion_batch_id", sa.String(length=36), nullable=True),
    ]


def _index(table: str, *columns: str, unique: bool = False, name: str | None = None) -> None:
    op.create_index(name or f"ix_{table}_{'_'.join(columns)}", table, list(columns), unique=unique)


def upgrade() -> None:
    op.create_table(
        "forum_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("slug", sa.String(length=100), nullable=False, unique=True),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
    )
    _index("forum_categories", "slug", unique=True)
    _index("forum_categories", "enabled")

    op.create_table(
        "product_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("slug", sa.String(length=100), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_soft_delete(),
        *_timestamps(),
    )
    _index("product_categories", "slug", unique=True)
    _index("product_categories", "enabled")
    for column in ("deleted_at", "deleted_by", "deletion_batch_id"):
        _index("product_categories", column)

    op.create_table(
        "recycle_catalog_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("variant", sa.String(length=160), nullable=True),
        sa.Column("reference_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_soft_delete(),
        *_timestamps(),
    )
    for column in ("brand", "model", "enabled", "deleted_at", "deleted_by", "deletion_batch_id"):
        _index("recycle_catalog_items", column)

    op.create_table(
        "recycle_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=80), nullable=False, unique=True),
        sa.Column("rule_group", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("factor", sa.Numeric(8, 4), nullable=True),
        sa.Column("adjustment", sa.Numeric(12, 2), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_timestamps(),
    )
    _index("recycle_rules", "code", unique=True)
    _index("recycle_rules", "rule_group")
    _index("recycle_rules", "enabled")

    op.create_table(
        "client_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False, unique=True),
        sa.Column("username", sa.String(length=80), nullable=False, unique=True),
        sa.Column("phone", sa.String(length=32), nullable=False, unique=True),
        sa.Column("email", sa.String(length=254), nullable=True, unique=True),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("avatar_path", sa.String(length=600), nullable=True),
        sa.Column("nickname", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.String(length=40), nullable=True),
        sa.Column("last_login_at", sa.String(length=40), nullable=True),
        *_timestamps(),
    )
    for column, unique in (
        ("customer_id", True), ("username", True), ("phone", True), ("email", True),
        ("status", False), ("locked_until", False),
    ):
        _index("client_accounts", column, unique=unique)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("product_categories.id"), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=220), nullable=False, unique=True),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("after_sales", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("featured", sa.Boolean(), nullable=False),
        *_soft_delete(),
        *_timestamps(),
    )
    for column in ("category_id", "name", "status", "featured", "deleted_at", "deleted_by", "deletion_batch_id"):
        _index("products", column)
    _index("products", "slug", unique=True)

    op.create_table(
        "client_action_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", sa.String(length=120), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.String(length=80), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    for column in ("account_id", "action", "resource_type", "success", "created_at"):
        _index("client_action_logs", column)

    op.create_table(
        "client_addresses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("recipient_name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("province", sa.String(length=60), nullable=False),
        sa.Column("city", sa.String(length=60), nullable=False),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        *_timestamps(),
    )
    _index("client_addresses", "account_id")
    _index("client_addresses", "is_default")

    op.create_table(
        "client_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("attachment_type", sa.String(length=40), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=600), nullable=False, unique=True),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    for column in ("account_id", "resource_type", "resource_id", "sha256", "created_at"):
        _index("client_attachments", column)
    _index("client_attachments", "account_id", "resource_type", "resource_id", name="ix_client_attachment_resource")

    op.create_table(
        "client_carts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False, unique=True),
        *_timestamps(),
    )
    _index("client_carts", "account_id", unique=True)

    op.create_table(
        "client_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.String(length=1000), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=True),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("read_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    for column in ("account_id", "notification_type", "is_read", "created_at"):
        _index("client_notifications", column)

    op.create_table(
        "client_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(length=96), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("last_seen_at", sa.String(length=40), nullable=False),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.Column("ip_address", sa.String(length=80), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
    )
    _index("client_sessions", "account_id")
    _index("client_sessions", "token_hash", unique=True)
    _index("client_sessions", "expires_at")
    _index("client_sessions", "revoked_at")

    op.create_table(
        "forum_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("forum_categories.id"), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("is_featured", sa.Boolean(), nullable=False),
        sa.Column("view_count", sa.Integer(), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=False),
        sa.Column("comment_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True, unique=True),
        sa.Column("deleted_by_client_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=True),
        *_soft_delete(),
        *_timestamps(),
    )
    for column in (
        "author_id", "category_id", "title", "status", "is_pinned", "is_featured",
        "deleted_by_client_id", "deleted_at", "deleted_by", "deletion_batch_id",
    ):
        _index("forum_posts", column)

    op.create_table(
        "product_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("storage_path", sa.String(length=600), nullable=False),
        sa.Column("alt_text", sa.String(length=200), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    _index("product_images", "product_id")

    op.create_table(
        "product_skus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id"), nullable=True, unique=True),
        sa.Column("sku", sa.String(length=80), nullable=False, unique=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("original_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("stock_quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_soft_delete(),
        *_timestamps(),
        sa.CheckConstraint("stock_quantity >= 0", name="ck_product_sku_stock_nonnegative"),
        sa.CheckConstraint("reserved_quantity >= 0", name="ck_product_sku_reserved_nonnegative"),
        sa.CheckConstraint("reserved_quantity <= stock_quantity", name="ck_product_sku_reserved_not_over_stock"),
    )
    for column in (
        "product_id", "enabled", "deleted_at", "deleted_by", "deletion_batch_id",
    ):
        _index("product_skus", column)
    _index("product_skus", "inventory_item_id", unique=True)
    _index("product_skus", "sku", unique=True)

    op.create_table(
        "retail_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_no", sa.String(length=48), nullable=False, unique=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("address_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("delivery_method", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("shipping_fee", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_provider", sa.String(length=40), nullable=False),
        sa.Column("payment_reference", sa.String(length=160), nullable=True),
        sa.Column("tracking_no", sa.String(length=120), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("cancelled_at", sa.String(length=40), nullable=True),
        *_timestamps(),
    )
    _index("retail_orders", "order_no", unique=True)
    for column in ("account_id", "customer_id", "status"):
        _index("retail_orders", column)

    op.create_table(
        "client_cart_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cart_id", sa.Integer(), sa.ForeignKey("client_carts.id"), nullable=False),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("product_skus.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("cart_id", "sku_id", name="uq_client_cart_sku"),
        sa.CheckConstraint("quantity > 0", name="ck_client_cart_item_quantity_positive"),
    )
    _index("client_cart_items", "cart_id")
    _index("client_cart_items", "sku_id")

    op.create_table(
        "client_repair_intakes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=False, unique=True),
        sa.Column("service_mode", sa.String(length=30), nullable=False),
        sa.Column("fault_type", sa.String(length=80), nullable=False),
        sa.Column("has_water_damage", sa.Boolean(), nullable=False),
        sa.Column("has_crash_damage", sa.Boolean(), nullable=False),
        sa.Column("was_disassembled", sa.Boolean(), nullable=False),
        sa.Column("current_state", sa.String(length=200), nullable=True),
        sa.Column("contact_name", sa.String(length=120), nullable=False),
        sa.Column("contact_phone", sa.String(length=32), nullable=False),
        sa.Column("address_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("extra_json", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False, unique=True),
        *_timestamps(),
    )
    _index("client_repair_intakes", "account_id")
    _index("client_repair_intakes", "repair_order_id", unique=True)

    op.create_table(
        "forum_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("forum_posts.id"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("forum_comments.id"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True, unique=True),
        sa.Column("deleted_by_client_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=True),
        *_soft_delete(),
        *_timestamps(),
    )
    for column in (
        "post_id", "author_id", "parent_id", "status", "deleted_by_client_id",
        "deleted_at", "deleted_by", "deletion_batch_id",
    ):
        _index("forum_comments", column)

    op.create_table(
        "forum_favorites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("forum_posts.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("post_id", "account_id", name="uq_forum_favorite"),
    )
    _index("forum_favorites", "post_id")
    _index("forum_favorites", "account_id")

    op.create_table(
        "forum_post_likes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("forum_posts.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("post_id", "account_id", name="uq_forum_post_like"),
    )
    _index("forum_post_likes", "post_id")
    _index("forum_post_likes", "account_id")

    op.create_table(
        "retail_order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("retail_orders.id"), nullable=False),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("product_skus.id"), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("sku_name", sa.String(length=200), nullable=False),
        sa.Column("sku_code", sa.String(length=80), nullable=False),
        sa.Column("attributes_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_retail_order_item_quantity_positive"),
    )
    _index("retail_order_items", "order_id")
    _index("retail_order_items", "sku_id")

    op.create_table(
        "forum_comment_likes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("forum_comments.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("comment_id", "account_id", name="uq_forum_comment_like"),
    )
    _index("forum_comment_likes", "comment_id")
    _index("forum_comment_likes", "account_id")

    op.create_table(
        "forum_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("forum_posts.id"), nullable=True),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("forum_comments.id"), nullable=True),
        sa.Column("reporter_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("handled_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("handled_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("post_id", "comment_id", "reporter_id", name="uq_forum_report_target"),
        sa.CheckConstraint("post_id IS NOT NULL OR comment_id IS NOT NULL", name="ck_forum_report_has_target"),
    )
    for column in ("post_id", "comment_id", "reporter_id", "status", "handled_by", "created_at"):
        _index("forum_reports", column)

    op.create_table(
        "recycle_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_no", sa.String(length=48), nullable=False, unique=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("catalog_item_id", sa.Integer(), sa.ForeignKey("recycle_catalog_items.id"), nullable=False),
        sa.Column("service_ticket_id", sa.Integer(), sa.ForeignKey("service_tickets.id"), nullable=True, unique=True),
        sa.Column("questionnaire_json", sa.JSON(), nullable=False),
        sa.Column("reference_min", sa.Numeric(12, 2), nullable=False),
        sa.Column("reference_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("staff_quote", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("user_decision", sa.String(length=20), nullable=True),
        sa.Column("decision_at", sa.String(length=40), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True, unique=True),
        *_timestamps(),
    )
    _index("recycle_requests", "request_no", unique=True)
    for column in ("account_id", "customer_id", "catalog_item_id", "status"):
        _index("recycle_requests", column)
    _index("recycle_requests", "service_ticket_id", unique=True)

    forum_category = sa.table(
        "forum_categories",
        sa.column("name", sa.String()), sa.column("slug", sa.String()),
        sa.column("description", sa.String()), sa.column("sort_order", sa.Integer()),
        sa.column("enabled", sa.Boolean()), sa.column("created_at", sa.String()),
        sa.column("updated_at", sa.String()),
    )
    now = "2026-08-23T00:00:00+00:00"
    op.bulk_insert(forum_category, [
        {"name": "维修交流", "slug": "repair", "description": "故障与维修经验", "sort_order": 10, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "飞行交流", "slug": "flight", "description": "飞行与航拍交流", "sort_order": 20, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "设备讨论", "slug": "devices", "description": "设备与配件讨论", "sort_order": 30, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "二手交流", "slug": "second-hand", "description": "二手设备经验", "sort_order": 40, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "使用技巧", "slug": "tips", "description": "使用技巧与教程", "sort_order": 50, "enabled": True, "created_at": now, "updated_at": now},
    ])
    product_category = sa.table(
        "product_categories",
        sa.column("name", sa.String()), sa.column("slug", sa.String()),
        sa.column("sort_order", sa.Integer()), sa.column("enabled", sa.Boolean()),
        sa.column("created_at", sa.String()), sa.column("updated_at", sa.String()),
    )
    op.bulk_insert(product_category, [
        {"name": "无人机", "slug": "drones", "sort_order": 10, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "相机与云台", "slug": "cameras-gimbals", "sort_order": 20, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "遥控器", "slug": "controllers", "sort_order": 30, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "电池与配件", "slug": "batteries-accessories", "sort_order": 40, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "二手机", "slug": "preowned", "sort_order": 50, "enabled": True, "created_at": now, "updated_at": now},
        {"name": "其他", "slug": "other", "sort_order": 90, "enabled": True, "created_at": now, "updated_at": now},
    ])
    recycle_rule = sa.table(
        "recycle_rules",
        sa.column("code", sa.String()), sa.column("rule_group", sa.String()),
        sa.column("label", sa.String()), sa.column("factor", sa.Numeric()),
        sa.column("adjustment", sa.Numeric()), sa.column("enabled", sa.Boolean()),
        sa.column("sort_order", sa.Integer()), sa.column("created_at", sa.String()),
        sa.column("updated_at", sa.String()),
    )
    rules = [
        ("appearance_new", "appearance", "近乎全新", "1.0000", 10),
        ("appearance_excellent", "appearance", "轻微使用痕迹", "0.9200", 20),
        ("appearance_good", "appearance", "正常使用痕迹", "0.8200", 30),
        ("appearance_fair", "appearance", "明显磕碰磨损", "0.6800", 40),
        ("function_normal", "function", "功能正常", "1.0000", 10),
        ("function_partial", "function", "部分功能异常", "0.7000", 20),
        ("function_unusable", "function", "无法正常使用", "0.3500", 30),
        ("gimbal_normal", "gimbal", "云台正常", "1.0000", 10),
        ("gimbal_issue", "gimbal", "云台异常", "0.7500", 20),
        ("lens_normal", "lens", "镜头正常", "1.0000", 10),
        ("lens_scratched", "lens", "镜头划伤", "0.8500", 20),
        ("repair_none", "repair_history", "无拆修", "1.0000", 10),
        ("repair_existing", "repair_history", "有拆修记录", "0.8800", 20),
    ]
    op.bulk_insert(recycle_rule, [
        {"code": code, "rule_group": group, "label": label, "factor": factor,
         "adjustment": "0.00", "enabled": True, "sort_order": order,
         "created_at": now, "updated_at": now}
        for code, group, label, factor, order in rules
    ])


def downgrade() -> None:
    for table in (
        "recycle_requests", "forum_reports", "forum_comment_likes", "retail_order_items",
        "forum_post_likes", "forum_favorites", "forum_comments", "client_repair_intakes",
        "client_cart_items", "retail_orders", "product_skus", "product_images", "forum_posts",
        "client_sessions", "client_notifications", "client_carts", "client_attachments",
        "client_addresses", "client_action_logs", "products", "client_accounts",
        "recycle_rules", "recycle_catalog_items", "product_categories", "forum_categories",
    ):
        op.drop_table(table)
