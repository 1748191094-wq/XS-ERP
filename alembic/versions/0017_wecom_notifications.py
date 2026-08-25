"""wecom reminder notifications and user binding

Revision ID: 0017_wecom_notifications
Revises: 0016_operations_procurement
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_wecom_notifications"
down_revision = "0016_operations_procurement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("wecom_userid", sa.String(length=128), nullable=True))
        batch_op.create_index("ix_users_wecom_userid", ["wecom_userid"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_wecom_userid")
        batch_op.drop_column("wecom_userid")
