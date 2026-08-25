"""add explicit client visibility to inventory items

Revision ID: 0029_client_inventory_visibility
Revises: 0028_client_platform
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_client_inventory_visibility"
down_revision = "0028_client_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "client_visible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index(
            "ix_inventory_items_client_visible", ["client_visible"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch_op:
        batch_op.drop_index("ix_inventory_items_client_visible")
        batch_op.drop_column("client_visible")
