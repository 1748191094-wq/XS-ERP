"""add idempotency keys for stock and finance writes

Revision ID: 0002_idempotency_keys
Revises: 0001_initial
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_idempotency_keys"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 命名唯一索引避免新库触发 Alembic 表复制循环。
    op.add_column(
        "inventory_transactions",
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "uq_inventory_transaction_idempotency",
        "inventory_transactions",
        ["idempotency_key"],
        unique=True,
    )
    op.add_column(
        "finance_transactions",
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "uq_finance_transaction_idempotency",
        "finance_transactions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_finance_transaction_idempotency", table_name="finance_transactions")
    op.drop_column("finance_transactions", "idempotency_key")
    op.drop_index("uq_inventory_transaction_idempotency", table_name="inventory_transactions")
    op.drop_column("inventory_transactions", "idempotency_key")
