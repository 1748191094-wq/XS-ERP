"""random repair-order numbers with durable reservations

Revision ID: 0023_random_repair_order_numbers
Revises: 0022_device_soft_delete
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_random_repair_order_numbers"
down_revision = "0022_device_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repair_order_number_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("reserved_at", sa.String(length=40), nullable=False),
    )
    op.create_index(
        "ix_repair_order_number_reservations_order_no",
        "repair_order_number_reservations",
        ["order_no"],
        unique=True,
    )
    op.execute(
        """
        INSERT INTO repair_order_number_reservations (order_no, reserved_at)
        SELECT order_no, created_at
        FROM repair_orders
        WHERE order_no IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_repair_order_number_reservations_order_no",
        table_name="repair_order_number_reservations",
    )
    op.drop_table("repair_order_number_reservations")
