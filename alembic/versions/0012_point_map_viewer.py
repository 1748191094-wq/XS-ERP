"""independent point map viewer and marker electrical metadata

Revision ID: 0012_point_map_viewer
Revises: 0011_damage_sop_point_maps
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_point_map_viewer"
down_revision = "0011_damage_sop_point_maps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("point_maps") as batch:
        batch.add_column(sa.Column("source_attachment_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_page", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_point_maps_source_attachment_id_attachments",
            "attachments", ["source_attachment_id"], ["id"],
        )
        batch.create_index("ix_point_maps_source_attachment_id", ["source_attachment_id"])
    with op.batch_alter_table("point_markers") as batch:
        batch.add_column(sa.Column("function_description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("voltage_spec", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("current_spec", sa.String(length=160), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("point_markers") as batch:
        batch.drop_column("current_spec")
        batch.drop_column("voltage_spec")
        batch.drop_column("function_description")
    with op.batch_alter_table("point_maps") as batch:
        batch.drop_index("ix_point_maps_source_attachment_id")
        batch.drop_constraint("fk_point_maps_source_attachment_id_attachments", type_="foreignkey")
        batch.drop_column("source_page")
        batch.drop_column("source_attachment_id")
