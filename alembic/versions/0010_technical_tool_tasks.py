"""controlled technical tool tasks and DHv2 audit trail

Revision ID: 0010_technical_tool_tasks
Revises: 0009_retail_quotes
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_technical_tool_tasks"
down_revision = "0009_retail_quotes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technical_tool_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_no", sa.String(length=48), nullable=False),
        sa.Column("task_record_id", sa.Integer(), sa.ForeignKey("task_records.id"), nullable=True),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("drone_devices.id"), nullable=False),
        sa.Column("tool_key", sa.String(length=80), nullable=False, server_default="drone_hacks_v2"),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("executable_path_snapshot", sa.String(length=600), nullable=True),
        sa.Column("executable_version", sa.String(length=80), nullable=True),
        sa.Column("executable_sha256", sa.String(length=64), nullable=True),
        sa.Column("signer_subject", sa.String(length=500), nullable=True),
        sa.Column("device_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("safety_check_json", sa.JSON(), nullable=True),
        sa.Column("input_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("warning_acknowledged_at", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=True),
        sa.Column("finished_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("task_no", name="uq_technical_tool_tasks_task_no"),
        sa.UniqueConstraint("task_record_id", name="uq_technical_tool_tasks_task_record_id"),
    )
    for column in (
        "task_no", "repair_order_id", "device_id", "tool_key", "operation_type", "status",
        "requested_by", "operator_id", "executable_sha256",
    ):
        op.create_index(f"ix_technical_tool_tasks_{column}", "technical_tool_tasks", [column])

    op.create_table(
        "technical_tool_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "technical_tool_task_id",
            sa.Integer(),
            sa.ForeignKey("technical_tool_tasks.id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    for column in ("technical_tool_task_id", "event_type", "actor_id", "created_at"):
        op.create_index(f"ix_technical_tool_events_{column}", "technical_tool_events", [column])

    op.create_table(
        "technical_tool_locks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("drone_devices.id"), nullable=False),
        sa.Column(
            "technical_tool_task_id",
            sa.Integer(),
            sa.ForeignKey("technical_tool_tasks.id"),
            nullable=False,
        ),
        sa.Column("acquired_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("acquired_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("device_id", name="uq_technical_tool_locks_device_id"),
        sa.UniqueConstraint("technical_tool_task_id", name="uq_technical_tool_locks_task_id"),
    )
    op.create_index("ix_technical_tool_locks_device_id", "technical_tool_locks", ["device_id"])
    op.create_index(
        "ix_technical_tool_locks_technical_tool_task_id",
        "technical_tool_locks",
        ["technical_tool_task_id"],
    )


def downgrade() -> None:
    op.drop_table("technical_tool_locks")
    op.drop_table("technical_tool_events")
    op.drop_table("technical_tool_tasks")
