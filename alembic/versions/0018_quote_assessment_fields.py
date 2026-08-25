"""freeze assessment details on quotation versions

Revision ID: 0018_quote_assessment_fields
Revises: 0017_wecom_notifications
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_quote_assessment_fields"
down_revision = "0017_wecom_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quotes") as batch_op:
        batch_op.add_column(sa.Column("assessment_result", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("assessment_responsibility", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("repair_recommendation", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("customer_notice", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quotes") as batch_op:
        batch_op.drop_column("customer_notice")
        batch_op.drop_column("repair_recommendation")
        batch_op.drop_column("assessment_responsibility")
        batch_op.drop_column("assessment_result")
