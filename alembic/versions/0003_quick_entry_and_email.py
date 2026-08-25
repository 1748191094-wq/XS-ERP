"""quick entry and email delivery support

Revision ID: 0003_quick_entry_email
Revises: 0002_idempotency_keys
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_quick_entry_email"
down_revision = "0002_idempotency_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.add_column(sa.Column("email", sa.String(length=254), nullable=True))
        batch.create_index("ix_customers_email", ["email"], unique=False)
    with op.batch_alter_table("repair_orders") as batch:
        batch.add_column(sa.Column("source_request_key", sa.String(length=100), nullable=True))
        batch.create_unique_constraint("uq_repair_orders_source_request_key", ["source_request_key"])
    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_no", sa.String(length=48), nullable=False),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quotes.id"), nullable=False),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=False),
        sa.Column("task_record_id", sa.Integer(), sa.ForeignKey("task_records.id"), nullable=True),
        sa.Column("recipient", sa.String(length=254), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="mock"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("attachment_path", sa.String(length=600), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.String(length=40), nullable=False),
        sa.Column("sent_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("delivery_no"),
        sa.UniqueConstraint("task_record_id"),
    )
    op.create_index("ix_email_deliveries_delivery_no", "email_deliveries", ["delivery_no"])
    op.create_index("ix_email_deliveries_quote_id", "email_deliveries", ["quote_id"])
    op.create_index("ix_email_deliveries_repair_order_id", "email_deliveries", ["repair_order_id"])
    op.create_index("ix_email_deliveries_recipient", "email_deliveries", ["recipient"])
    op.create_index("ix_email_deliveries_status", "email_deliveries", ["status"])


def downgrade() -> None:
    op.drop_table("email_deliveries")
    with op.batch_alter_table("repair_orders") as batch:
        batch.drop_constraint("uq_repair_orders_source_request_key", type_="unique")
        batch.drop_column("source_request_key")
    with op.batch_alter_table("customers") as batch:
        batch.drop_index("ix_customers_email")
        batch.drop_column("email")
