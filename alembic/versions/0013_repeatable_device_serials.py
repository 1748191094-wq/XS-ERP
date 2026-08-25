"""allow repeated device serials for ownership history

Revision ID: 0013_repeatable_device_serials
Revises: 0012_point_map_viewer
Create Date: 2026-07-23
"""

from alembic import op


revision = "0013_repeatable_device_serials"
down_revision = "0012_point_map_viewer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 原约束是唯一索引；原位替换可保留数据和外键。
    op.drop_index("ix_drone_devices_serial_number", table_name="drone_devices")
    op.create_index(
        "ix_drone_devices_serial_number", "drone_devices", ["serial_number"], unique=False
    )


def downgrade() -> None:
    # 降级不恢复唯一约束，避免破坏归属历史。
    pass
