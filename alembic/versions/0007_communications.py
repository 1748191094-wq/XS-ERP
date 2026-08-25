"""outbound calls and snapshot email queue

Revision ID: 0007_communications
Revises: 0006_unified_service_tickets
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_communications"
down_revision = "0006_unified_service_tickets"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "outbound_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("call_no", sa.String(length=48), nullable=False, unique=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("service_ticket_id", sa.Integer(), sa.ForeignKey("service_tickets.id"), nullable=True),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=True),
        sa.Column("assigned_to", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("contact_number", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=160), nullable=False),
        sa.Column("planned_at", sa.String(length=40), nullable=True),
        sa.Column("actual_at", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="planned"),
        sa.Column("result", sa.String(length=30), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("customer_intent", sa.String(length=80), nullable=True),
        sa.Column("next_contact_at", sa.String(length=40), nullable=True),
        sa.Column("recording_attachment_id", sa.Integer(), sa.ForeignKey("attachments.id"), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("external_call_id", sa.String(length=160), nullable=True),
        *timestamps(),
    )
    for column in (
        "call_no", "customer_id", "service_ticket_id", "repair_order_id", "assigned_to",
        "created_by", "contact_number", "planned_at", "status", "result", "next_contact_at",
        "external_call_id",
    ):
        op.create_index(f"ix_outbound_calls_{column}", "outbound_calls", [column])

    op.create_table(
        "outbound_emails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_no", sa.String(length=48), nullable=False, unique=True),
        sa.Column("template_type", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("service_ticket_id", sa.Integer(), sa.ForeignKey("service_tickets.id"), nullable=True),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=True),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quotes.id"), nullable=True),
        sa.Column("task_record_id", sa.Integer(), sa.ForeignKey("task_records.id"), nullable=True, unique=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("recipient", sa.String(length=254), nullable=False),
        sa.Column("cc_json", sa.JSON(), nullable=True),
        sa.Column("bcc_json", sa.JSON(), nullable=True),
        sa.Column("subject_snapshot", sa.String(length=300), nullable=False),
        sa.Column("body_snapshot", sa.Text(), nullable=False),
        sa.Column("attachment_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_retry_at", sa.String(length=40), nullable=True),
        sa.Column("last_attempt_at", sa.String(length=40), nullable=True),
        sa.Column("sent_at", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *timestamps(),
    )
    for column in (
        "email_no", "template_type", "customer_id", "service_ticket_id", "repair_order_id",
        "quote_id", "created_by", "recipient", "status", "next_retry_at",
    ):
        op.create_index(f"ix_outbound_emails_{column}", "outbound_emails", [column])


def downgrade() -> None:
    op.drop_table("outbound_emails")
    op.drop_table("outbound_calls")
