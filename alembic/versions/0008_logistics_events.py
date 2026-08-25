"""logistics events and offline queue trail

Revision ID: 0008_logistics_events
Revises: 0007_communications
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_logistics_events"
down_revision = "0007_communications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shipment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipment_id", sa.Integer(), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("logistics_status", sa.String(length=40), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("occurred_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    for column in ("shipment_id", "logistics_status", "recorded_by", "occurred_at"):
        op.create_index(f"ix_shipment_events_{column}", "shipment_events", [column])


def downgrade() -> None:
    op.drop_table("shipment_events")
