"""damage assessment SOP templates and point maps

Revision ID: 0011_damage_sop_point_maps
Revises: 0010_technical_tool_tasks
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_damage_sop_point_maps"
down_revision = "0010_technical_tool_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "damage_sop_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand", sa.String(length=80), nullable=False, server_default="通用"),
        sa.Column("product_category", sa.String(length=80), nullable=False, server_default="数码产品"),
        sa.Column("series", sa.String(length=120), nullable=True),
        sa.Column("model_pattern", sa.String(length=160), nullable=False, server_default="*"),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.String(length=600), nullable=True),
        sa.Column("access_level", sa.String(length=30), nullable=False, server_default="internal"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint(
            "brand", "model_pattern", "title", "version",
            name="uq_damage_sop_template_version",
        ),
    )
    for column in ("brand", "product_category", "series", "model_pattern", "title", "status", "access_level", "created_by"):
        op.create_index(f"ix_damage_sop_templates_{column}", "damage_sop_templates", [column])

    op.create_table(
        "point_maps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand", sa.String(length=80), nullable=False, server_default="通用"),
        sa.Column("product_category", sa.String(length=80), nullable=False, server_default="数码产品"),
        sa.Column("series", sa.String(length=120), nullable=True),
        sa.Column("model_pattern", sa.String(length=160), nullable=False, server_default="*"),
        sa.Column("module_name", sa.String(length=160), nullable=False),
        sa.Column("board_code", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("image_attachment_id", sa.Integer(), sa.ForeignKey("attachments.id"), nullable=True),
        sa.Column("source_reference", sa.String(length=600), nullable=True),
        sa.Column("access_level", sa.String(length=30), nullable=False, server_default="internal"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint(
            "brand", "model_pattern", "module_name", "title", "version",
            name="uq_point_map_version",
        ),
    )
    for column in (
        "brand", "product_category", "series", "model_pattern", "module_name", "board_code",
        "title", "status", "image_attachment_id", "access_level", "created_by",
    ):
        op.create_index(f"ix_point_maps_{column}", "point_maps", [column])

    op.create_table(
        "point_markers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("point_map_id", sa.Integer(), sa.ForeignKey("point_maps.id"), nullable=False),
        sa.Column("marker_code", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("x_percent", sa.Numeric(6, 3), nullable=False),
        sa.Column("y_percent", sa.Numeric(6, 3), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("component_ref", sa.String(length=160), nullable=True),
        sa.Column("marker_type", sa.String(length=30), nullable=False, server_default="measurement"),
        sa.Column("measurement_kind", sa.String(length=80), nullable=True),
        sa.Column("expected_value", sa.String(length=160), nullable=True),
        sa.Column("tolerance", sa.String(length=120), nullable=True),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("probe_hint", sa.Text(), nullable=True),
        sa.Column("risk_note", sa.Text(), nullable=True),
        sa.UniqueConstraint("point_map_id", "marker_code", name="uq_point_map_marker_code"),
    )
    op.create_index("ix_point_markers_point_map_id", "point_markers", ["point_map_id"])
    op.create_index("ix_point_markers_marker_type", "point_markers", ["marker_type"])

    op.create_table(
        "damage_sop_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("damage_sop_templates.id"), nullable=False),
        sa.Column("step_code", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("section", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("check_type", sa.String(length=30), nullable=False, server_default="visual"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("module_name", sa.String(length=160), nullable=True),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("fail_conclusion", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="normal"),
        sa.Column("point_map_id", sa.Integer(), sa.ForeignKey("point_maps.id"), nullable=True),
        sa.Column("point_marker_id", sa.Integer(), sa.ForeignKey("point_markers.id"), nullable=True),
        sa.UniqueConstraint("template_id", "step_code", name="uq_damage_sop_step_code"),
    )
    for column in ("template_id", "section", "check_type", "module_name", "risk_level", "point_map_id", "point_marker_id"):
        op.create_index(f"ix_damage_sop_steps_{column}", "damage_sop_steps", [column])

    op.create_table(
        "damage_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_no", sa.String(length=48), nullable=False),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("drone_devices.id"), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("damage_sop_templates.id"), nullable=False),
        sa.Column("template_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="in_progress"),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("responsibility", sa.String(length=80), nullable=True),
        sa.Column("repair_recommendation", sa.Text(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("assessment_no", name="uq_damage_assessments_assessment_no"),
    )
    for column in ("assessment_no", "repair_order_id", "device_id", "template_id", "status", "operator_id"):
        op.create_index(f"ix_damage_assessments_{column}", "damage_assessments", [column])

    op.create_table(
        "damage_assessment_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("damage_assessments.id"), nullable=False),
        sa.Column("sop_step_id", sa.Integer(), sa.ForeignKey("damage_sop_steps.id"), nullable=True),
        sa.Column("step_code", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("measured_value", sa.String(length=240), nullable=True),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("evidence_attachment_id", sa.Integer(), sa.ForeignKey("attachments.id"), nullable=True),
        sa.Column("point_marker_id", sa.Integer(), sa.ForeignKey("point_markers.id"), nullable=True),
        sa.Column("completed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.UniqueConstraint("assessment_id", "step_code", name="uq_damage_assessment_step_code"),
    )
    for column in (
        "assessment_id", "sop_step_id", "result", "evidence_attachment_id",
        "point_marker_id", "completed_by",
    ):
        op.create_index(f"ix_damage_assessment_results_{column}", "damage_assessment_results", [column])


def downgrade() -> None:
    op.drop_table("damage_assessment_results")
    op.drop_table("damage_assessments")
    op.drop_table("damage_sop_steps")
    op.drop_table("point_markers")
    op.drop_table("point_maps")
    op.drop_table("damage_sop_templates")

