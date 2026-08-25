"""offline node synchronization

Revision ID: 0015_offline_node_sync
Revises: 0014_reversible_admin_deletions
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_offline_node_sync"
down_revision = "0014_reversible_admin_deletions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drone_devices", sa.Column("sync_key", sa.String(length=36), nullable=True))
    op.create_index("ix_drone_devices_sync_key", "drone_devices", ["sync_key"], unique=True)

    op.create_table(
        "sync_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="terminal"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.String(40)),
        sa.Column("last_ip", sa.String(80)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("node_id"),
    )
    op.create_index("ix_sync_nodes_node_id", "sync_nodes", ["node_id"])
    op.create_index("ix_sync_nodes_role", "sync_nodes", ["role"])
    op.create_index("ix_sync_nodes_enabled", "sync_nodes", ["enabled"])
    op.create_index("ix_sync_nodes_last_seen_at", "sync_nodes", ["last_seen_at"])

    op.create_table(
        "sync_entity_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("record_key", sa.String(240), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("server_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("entity_type", "record_key", name="uq_sync_entity_state_key"),
    )
    for column in ("entity_type", "record_key", "payload_hash"):
        op.create_index(f"ix_sync_entity_states_{column}", "sync_entity_states", [column])

    op.create_table(
        "sync_outbox_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("origin_node_id", sa.String(36), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("record_key", sa.String(240), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False, server_default="upsert"),
        sa.Column("base_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("base_payload_json", sa.JSON()),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("acknowledged_at", sa.String(40)),
    )
    for column in ("event_id", "origin_node_id", "entity_type", "record_key", "payload_hash", "status", "created_at"):
        op.create_index(f"ix_sync_outbox_events_{column}", "sync_outbox_events", [column])

    op.create_table(
        "sync_canonical_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("record_key", sa.String(240), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("origin_node_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("entity_type", "record_key", name="uq_sync_canonical_record_key"),
    )
    for column in ("entity_type", "record_key", "payload_hash", "origin_node_id"):
        op.create_index(f"ix_sync_canonical_records_{column}", "sync_canonical_records", [column])

    op.create_table(
        "sync_server_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("origin_node_id", sa.String(36), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("record_key", sa.String(240), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False, server_default="upsert"),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    for column in ("event_id", "origin_node_id", "entity_type", "record_key", "payload_hash", "created_at"):
        op.create_index(f"ix_sync_server_changes_{column}", "sync_server_changes", [column])

    op.create_table(
        "sync_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conflict_id", sa.String(36), nullable=False, unique=True),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("origin_node_id", sa.String(36), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("record_key", sa.String(240), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("base_payload_json", sa.JSON()),
        sa.Column("incoming_payload_json", sa.JSON(), nullable=False),
        sa.Column("current_payload_json", sa.JSON(), nullable=False),
        sa.Column("conflicting_fields_json", sa.JSON()),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("resolved_at", sa.String(40)),
        sa.Column("resolution", sa.String(30)),
    )
    for column in ("conflict_id", "event_id", "origin_node_id", "entity_type", "record_key", "status", "created_at"):
        op.create_index(f"ix_sync_conflicts_{column}", "sync_conflicts", [column])


def downgrade() -> None:
    for table in (
        "sync_conflicts",
        "sync_server_changes",
        "sync_canonical_records",
        "sync_outbox_events",
        "sync_entity_states",
        "sync_nodes",
    ):
        op.drop_table(table)
    op.drop_index("ix_drone_devices_sync_key", table_name="drone_devices")
    op.drop_column("drone_devices", "sync_key")
