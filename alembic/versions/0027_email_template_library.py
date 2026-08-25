"""custom email template library

Revision ID: 0027_email_template_library
Revises: 0026_replacement_ticket_fields
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_email_template_library"
down_revision = "0026_replacement_ticket_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_email_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_type", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.Column("deletion_batch_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_type"),
    )
    op.create_index("ix_custom_email_templates_template_type", "custom_email_templates", ["template_type"], unique=True)
    op.create_index("ix_custom_email_templates_name", "custom_email_templates", ["name"])
    op.create_index("ix_custom_email_templates_category", "custom_email_templates", ["category"])
    op.create_index("ix_custom_email_templates_enabled", "custom_email_templates", ["enabled"])
    op.create_index("ix_custom_email_templates_created_by", "custom_email_templates", ["created_by"])
    op.create_index("ix_custom_email_templates_updated_by", "custom_email_templates", ["updated_by"])
    op.create_index("ix_custom_email_templates_deleted_at", "custom_email_templates", ["deleted_at"])
    op.create_index("ix_custom_email_templates_deleted_by", "custom_email_templates", ["deleted_by"])
    op.create_index("ix_custom_email_templates_deletion_batch_id", "custom_email_templates", ["deletion_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_custom_email_templates_deletion_batch_id", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_deleted_by", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_deleted_at", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_updated_by", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_created_by", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_enabled", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_category", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_name", table_name="custom_email_templates")
    op.drop_index("ix_custom_email_templates_template_type", table_name="custom_email_templates")
    op.drop_table("custom_email_templates")
