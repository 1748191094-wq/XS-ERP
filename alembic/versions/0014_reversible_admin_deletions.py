"""reversible administrator deletions

Revision ID: 0014_reversible_admin_deletions
Revises: 0013_repeatable_device_serials
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_reversible_admin_deletions"
down_revision = "0013_repeatable_device_serials"
branch_labels = None
depends_on = None


TARGET_TABLES = ("customers", "repair_orders", "service_tickets", "quotes")


def upgrade() -> None:
    for table in TARGET_TABLES:
        op.add_column(table, sa.Column("deleted_at", sa.String(length=40), nullable=True))
        op.add_column(table, sa.Column("deleted_by", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("deletion_batch_id", sa.String(length=36), nullable=True))
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])
        op.create_index(f"ix_{table}_deleted_by", table, ["deleted_by"])
        op.create_index(f"ix_{table}_deletion_batch_id", table, ["deletion_batch_id"])

    op.create_table(
        "deleted_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("deleted_by", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.String(length=40), nullable=False),
        sa.Column("restored_at", sa.String(length=40), nullable=True),
        sa.Column("restored_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["restored_by"], ["users.id"]),
        sa.UniqueConstraint("batch_id"),
    )
    for column in (
        "batch_id", "resource_type", "resource_id", "deleted_by",
        "deleted_at", "restored_at", "restored_by",
    ):
        op.create_index(f"ix_deleted_records_{column}", "deleted_records", [column])


def downgrade() -> None:
    op.drop_table("deleted_records")
    for table in TARGET_TABLES:
        op.drop_index(f"ix_{table}_deletion_batch_id", table_name=table)
        op.drop_index(f"ix_{table}_deleted_by", table_name=table)
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_column(table, "deletion_batch_id")
        op.drop_column(table, "deleted_by")
        op.drop_column(table, "deleted_at")
