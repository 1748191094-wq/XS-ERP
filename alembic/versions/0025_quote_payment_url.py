"""quote-specific optional payment URL

Revision ID: 0025_quote_payment_url
Revises: 0024_followup_soft_delete
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_quote_payment_url"
down_revision = "0024_followup_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quotes", sa.Column("payment_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("quotes", "payment_url")
