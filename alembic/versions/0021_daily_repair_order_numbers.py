"""daily Base36 repair-order numbers

Revision ID: 0021_daily_repair_order_numbers
Revises: 0020_remove_dhv2
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_daily_repair_order_numbers"
down_revision = "0020_remove_dhv2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_number_counters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("counter_date", sa.Date(), nullable=False),
        sa.Column("current_value", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("scope", "counter_date", name="uq_daily_number_counter_scope_date"),
    )
    op.create_index("ix_daily_number_counters_scope", "daily_number_counters", ["scope"])
    op.create_index("ix_daily_number_counters_counter_date", "daily_number_counters", ["counter_date"])


def downgrade() -> None:
    op.drop_table("daily_number_counters")
