"""verified database backup records

Revision ID: 0005_database_backups
Revises: 0004_auth_roles_audit
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_database_backups"
down_revision = "0004_auth_roles_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=600), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("database_kind", sa.String(length=30), nullable=False, server_default="sqlite"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="verified"),
        sa.Column("integrity_result", sa.String(length=300), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("verified_at", sa.String(length=40), nullable=True),
        sa.UniqueConstraint("filename"),
        sa.UniqueConstraint("storage_path"),
    )
    for column in ("filename", "sha256", "status", "created_by", "created_at"):
        op.create_index(f"ix_backup_records_{column}", "backup_records", [column])


def downgrade() -> None:
    op.drop_table("backup_records")
