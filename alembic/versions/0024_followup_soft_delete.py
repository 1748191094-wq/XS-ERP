"""reversible follow-up task deletion

Revision ID: 0024_followup_soft_delete
Revises: 0023_random_repair_order_numbers
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_followup_soft_delete"
down_revision = "0023_random_repair_order_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("follow_up_tasks", sa.Column("deleted_at", sa.String(length=40), nullable=True))
    op.add_column("follow_up_tasks", sa.Column("deleted_by", sa.Integer(), nullable=True))
    op.add_column("follow_up_tasks", sa.Column("deletion_batch_id", sa.String(length=36), nullable=True))
    op.create_index("ix_follow_up_tasks_deleted_at", "follow_up_tasks", ["deleted_at"])
    op.create_index("ix_follow_up_tasks_deleted_by", "follow_up_tasks", ["deleted_by"])
    op.create_index("ix_follow_up_tasks_deletion_batch_id", "follow_up_tasks", ["deletion_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_follow_up_tasks_deletion_batch_id", table_name="follow_up_tasks")
    op.drop_index("ix_follow_up_tasks_deleted_by", table_name="follow_up_tasks")
    op.drop_index("ix_follow_up_tasks_deleted_at", table_name="follow_up_tasks")
    op.drop_column("follow_up_tasks", "deletion_batch_id")
    op.drop_column("follow_up_tasks", "deleted_by")
    op.drop_column("follow_up_tasks", "deleted_at")
