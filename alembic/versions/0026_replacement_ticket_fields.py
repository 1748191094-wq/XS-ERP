"""replacement service ticket fields

Revision ID: 0026_replacement_ticket_fields
Revises: 0025_quote_payment_url
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_replacement_ticket_fields"
down_revision = "0025_quote_payment_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("service_tickets", sa.Column("replacement_inspection_result", sa.Text(), nullable=True))
    op.add_column("service_tickets", sa.Column("trade_in_credit", sa.Numeric(12, 2), nullable=True))
    op.add_column("service_tickets", sa.Column("return_reference", sa.String(length=200), nullable=True))
    op.add_column("service_tickets", sa.Column("outbound_to_customer_tracking_no", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("service_tickets", "outbound_to_customer_tracking_no")
    op.drop_column("service_tickets", "return_reference")
    op.drop_column("service_tickets", "trade_in_credit")
    op.drop_column("service_tickets", "replacement_inspection_result")
