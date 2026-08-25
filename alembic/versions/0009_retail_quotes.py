"""retail service ticket quotes

Revision ID: 0009_retail_quotes
Revises: 0008_logistics_events
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_retail_quotes"
down_revision = "0008_logistics_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.alter_column("repair_order_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("service_ticket_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_quotes_service_ticket_id_service_tickets",
            "service_tickets",
            ["service_ticket_id"],
            ["id"],
        )
        batch.create_unique_constraint("uq_quote_ticket_version", ["service_ticket_id", "version"])
        batch.create_check_constraint(
            "ck_quote_exactly_one_target",
            "(repair_order_id IS NOT NULL) <> (service_ticket_id IS NOT NULL)",
        )
    op.create_index("ix_quotes_service_ticket_id", "quotes", ["service_ticket_id"])


def downgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.drop_constraint("ck_quote_exactly_one_target", type_="check")
        batch.drop_constraint("uq_quote_ticket_version", type_="unique")
        batch.drop_constraint("fk_quotes_service_ticket_id_service_tickets", type_="foreignkey")
        batch.drop_column("service_ticket_id")
        batch.alter_column("repair_order_id", existing_type=sa.Integer(), nullable=False)
