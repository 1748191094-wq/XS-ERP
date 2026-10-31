"""D1: link frozen quotes and diagnostic evidence to assessments."""
from alembic import op
import sqlalchemy as sa

revision = "0031_assessment_integration"
down_revision = "0030_forum_recommendation_signals"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("quotes", "diagnoses"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("damage_assessment_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_assessment", "damage_assessments", ["damage_assessment_id"], ["id"])
    with op.batch_alter_table("diagnoses") as batch:
        batch.add_column(sa.Column("sop_step_code", sa.String(80), nullable=True))
    with op.batch_alter_table("damage_assessment_results") as batch:
        batch.add_column(sa.Column("source", sa.String(20), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("source_ref_json", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("damage_assessment_results") as batch:
        batch.drop_column("source_ref_json")
        batch.drop_column("source")
    with op.batch_alter_table("diagnoses") as batch:
        batch.drop_column("sop_step_code")
    for table in ("diagnoses", "quotes"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_assessment", type_="foreignkey")
            batch.drop_column("damage_assessment_id")
