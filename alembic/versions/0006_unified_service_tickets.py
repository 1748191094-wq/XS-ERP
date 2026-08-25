"""unified service tickets and specialist escalation

Revision ID: 0006_unified_service_tickets
Revises: 0005_database_backups
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_unified_service_tickets"
down_revision = "0005_database_backups"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "processing_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False, unique=True),
        sa.Column("group_type", sa.String(length=40), nullable=False, server_default="service"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text(), nullable=True),
        *timestamps(),
    )
    for column in ("name", "group_type", "enabled"):
        op.create_index(f"ix_processing_groups_{column}", "processing_groups", [column])

    op.create_table(
        "processing_group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("processing_groups.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("member_role", sa.String(length=30), nullable=False, server_default="member"),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("added_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("group_id", "user_id", name="uq_processing_group_user"),
    )
    op.create_index("ix_processing_group_members_group_id", "processing_group_members", ["group_id"])
    op.create_index("ix_processing_group_members_user_id", "processing_group_members", ["user_id"])

    op.create_table(
        "service_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_no", sa.String(length=64), nullable=False, unique=True),
        sa.Column("ticket_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="normal"),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("drone_devices.id"), nullable=True),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=True, unique=True),
        sa.Column("current_owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("processing_group_id", sa.Integer(), sa.ForeignKey("processing_groups.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_at", sa.String(length=40), nullable=True),
        sa.Column("first_response_at", sa.String(length=40), nullable=True),
        sa.Column("resolved_at", sa.String(length=40), nullable=True),
        sa.Column("closed_at", sa.String(length=40), nullable=True),
        sa.Column("last_reminded_at", sa.String(length=40), nullable=True),
        sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="0"),
        *timestamps(),
    )
    for column in (
        "ticket_no", "ticket_type", "status", "priority", "customer_id", "device_id",
        "repair_order_id", "current_owner_id", "processing_group_id", "created_by", "due_at",
    ):
        op.create_index(f"ix_service_tickets_{column}", "service_tickets", [column])

    op.create_table(
        "service_ticket_collaborators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("service_tickets.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("collaborator_role", sa.String(length=30), nullable=False, server_default="assistant"),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("added_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("ticket_id", "user_id", name="uq_ticket_collaborator"),
    )
    op.create_index("ix_service_ticket_collaborators_ticket_id", "service_ticket_collaborators", ["ticket_id"])
    op.create_index("ix_service_ticket_collaborators_user_id", "service_ticket_collaborators", ["user_id"])

    op.create_table(
        "service_ticket_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("service_tickets.id"), nullable=False),
        sa.Column("visibility", sa.String(length=30), nullable=False, server_default="internal"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    for column in ("ticket_id", "visibility", "author_id", "created_at"):
        op.create_index(f"ix_service_ticket_notes_{column}", "service_ticket_notes", [column])

    op.create_table(
        "service_ticket_timeline",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("service_tickets.id"), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    for column in ("ticket_id", "event_type", "actor_id", "created_at"):
        op.create_index(f"ix_service_ticket_timeline_{column}", "service_ticket_timeline", [column])

    op.create_table(
        "specialist_escalations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("escalation_no", sa.String(length=64), nullable=False, unique=True),
        sa.Column("service_ticket_id", sa.Integer(), sa.ForeignKey("service_tickets.id"), nullable=False),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("problem_summary", sa.Text(), nullable=False),
        sa.Column("attempted_solutions", sa.Text(), nullable=False),
        sa.Column("urgency", sa.String(length=20), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="submitted"),
        sa.Column("assigned_specialist_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("specialist_group_id", sa.Integer(), sa.ForeignKey("processing_groups.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("accepted_at", sa.String(length=40), nullable=True),
        sa.Column("returned_at", sa.String(length=40), nullable=True),
        sa.Column("return_reason", sa.Text(), nullable=True),
        sa.Column("specialist_opinion", sa.Text(), nullable=True),
        sa.Column("solution", sa.Text(), nullable=True),
        sa.Column("final_result", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        *timestamps(),
    )
    for column in (
        "escalation_no", "service_ticket_id", "repair_order_id", "urgency", "status",
        "assigned_specialist_id", "specialist_group_id", "created_by",
    ):
        op.create_index(f"ix_specialist_escalations_{column}", "specialist_escalations", [column])

    op.execute(sa.text("""
        INSERT INTO service_tickets (
            ticket_no, ticket_type, title, description, status, priority,
            customer_id, device_id, repair_order_id, current_owner_id, due_at,
            resolved_at, reminder_count, created_at, updated_at
        )
        SELECT
            'TKT-' || order_no,
            'repair',
            '维修服务：' || substr(fault_description, 1, 180),
            fault_description,
            CASE status
                WHEN 'pending_inspection' THEN 'open'
                WHEN 'inspecting' THEN 'in_progress'
                WHEN 'pending_quote' THEN 'waiting_internal'
                WHEN 'quoted' THEN 'waiting_customer'
                WHEN 'customer_confirmed' THEN 'in_progress'
                WHEN 'repairing' THEN 'in_progress'
                WHEN 'pending_test' THEN 'in_progress'
                WHEN 'pending_shipping' THEN 'waiting_internal'
                WHEN 'completed' THEN 'resolved'
                WHEN 'cancelled' THEN 'cancelled'
                ELSE 'open'
            END,
            priority, customer_id, device_id, id, engineer_id, expected_finish_at,
            CASE WHEN status = 'completed' THEN completed_at ELSE NULL END,
            0, created_at, updated_at
        FROM repair_orders
    """))
    op.execute(sa.text("""
        INSERT INTO service_ticket_timeline (
            ticket_id, event_type, summary, actor_id, created_at
        )
        SELECT id, 'migrated', '由现有维修工单迁移建立统一服务工单', created_by, created_at
        FROM service_tickets
    """))


def downgrade() -> None:
    for table in (
        "specialist_escalations", "service_ticket_timeline", "service_ticket_notes",
        "service_ticket_collaborators", "service_tickets", "processing_group_members",
        "processing_groups",
    ):
        op.drop_table(table)
