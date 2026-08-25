"""reversible drone-device deletion

Revision ID: 0022_device_soft_delete
Revises: 0021_daily_repair_order_numbers
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_device_soft_delete"
down_revision = "0021_daily_repair_order_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drone_devices", sa.Column("deleted_at", sa.String(length=40), nullable=True))
    op.add_column("drone_devices", sa.Column("deleted_by", sa.Integer(), nullable=True))
    op.add_column("drone_devices", sa.Column("deletion_batch_id", sa.String(length=36), nullable=True))
    op.create_index("ix_drone_devices_deleted_at", "drone_devices", ["deleted_at"])
    op.create_index("ix_drone_devices_deleted_by", "drone_devices", ["deleted_by"])
    op.create_index("ix_drone_devices_deletion_batch_id", "drone_devices", ["deletion_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_drone_devices_deletion_batch_id", table_name="drone_devices")
    op.drop_index("ix_drone_devices_deleted_by", table_name="drone_devices")
    op.drop_index("ix_drone_devices_deleted_at", table_name="drone_devices")
    op.drop_column("drone_devices", "deletion_batch_id")
    op.drop_column("drone_devices", "deleted_by")
    op.drop_column("drone_devices", "deleted_at")
