"""service workflow extensions

Revision ID: 0019_service_workflow_extensions
Revises: 0018_quote_assessment_fields
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_service_workflow_extensions"
down_revision = "0018_quote_assessment_fields"
branch_labels = None
depends_on = None


SOFT_DELETE_TABLES = ("inventory_items", "finance_transactions", "damage_assessments")


def _add_soft_delete(table: str) -> None:
    op.add_column(table, sa.Column("deleted_at", sa.String(length=40), nullable=True))
    op.add_column(table, sa.Column("deleted_by", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("deletion_batch_id", sa.String(length=36), nullable=True))
    for column in ("deleted_at", "deleted_by", "deletion_batch_id"):
        op.create_index(f"ix_{table}_{column}", table, [column])


def _drop_soft_delete(table: str) -> None:
    for column in ("deletion_batch_id", "deleted_by", "deleted_at"):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_column(table, column)


def upgrade() -> None:
    op.add_column("users", sa.Column("employee_no", sa.String(length=24), nullable=True))
    op.execute("UPDATE users SET employee_no = 'ST' || printf('%04d', id) WHERE employee_no IS NULL")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("employee_no", existing_type=sa.String(length=24), nullable=False)
        batch_op.create_unique_constraint("uq_users_employee_no", ["employee_no"])
        batch_op.create_index("ix_users_employee_no", ["employee_no"])

    with op.batch_alter_table("repair_orders") as batch_op:
        batch_op.add_column(
            sa.Column(
                "processing_group_id",
                sa.Integer(),
                sa.ForeignKey("processing_groups.id", name="fk_repair_orders_processing_group_id"),
                nullable=True,
            )
        )
        batch_op.create_index("ix_repair_orders_processing_group_id", ["processing_group_id"])
    op.execute(
        """
        UPDATE repair_orders
        SET processing_group_id = (
            SELECT service_tickets.processing_group_id
            FROM service_tickets
            WHERE service_tickets.repair_order_id = repair_orders.id
            LIMIT 1
        )
        WHERE processing_group_id IS NULL
        """
    )

    with op.batch_alter_table("finance_transactions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "quote_id",
                sa.Integer(),
                sa.ForeignKey("quotes.id", name="fk_finance_transactions_quote_id"),
                nullable=True,
            )
        )
        batch_op.create_index("ix_finance_transactions_quote_id", ["quote_id"])
    for table in SOFT_DELETE_TABLES:
        _add_soft_delete(table)

    op.create_table(
        "work_order_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.Column("deletion_batch_id", sa.String(length=36), nullable=True),
    )
    for column in ("name", "created_by", "deleted_at", "deleted_by", "deletion_batch_id"):
        op.create_index(f"ix_work_order_groups_{column}", "work_order_groups", [column])
    op.create_table(
        "work_order_group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("work_order_groups.id"), nullable=False),
        sa.Column("repair_order_id", sa.Integer(), sa.ForeignKey("repair_orders.id"), nullable=False),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("added_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("group_id", "repair_order_id", name="uq_work_order_group_member"),
        sa.UniqueConstraint("repair_order_id", name="uq_work_order_group_single_membership"),
    )
    for column in ("group_id", "repair_order_id", "added_by"):
        op.create_index(f"ix_work_order_group_members_{column}", "work_order_group_members", [column])

    op.create_table(
        "customer_note_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("note_type", sa.String(length=20), nullable=False),
        sa.Column("service_group_id", sa.Integer(), sa.ForeignKey("processing_groups.id"), nullable=True),
        sa.Column("previous_content", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("changed_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "(note_type = 'large' AND service_group_id IS NULL) OR "
            "(note_type = 'small' AND service_group_id IS NOT NULL)",
            name="ck_customer_note_scope",
        ),
    )
    for column in ("customer_id", "note_type", "service_group_id", "changed_by", "changed_at"):
        op.create_index(f"ix_customer_note_revisions_{column}", "customer_note_revisions", [column])


def downgrade() -> None:
    op.drop_table("customer_note_revisions")
    op.drop_table("work_order_group_members")
    op.drop_table("work_order_groups")
    for table in reversed(SOFT_DELETE_TABLES):
        _drop_soft_delete(table)
    with op.batch_alter_table("finance_transactions") as batch_op:
        batch_op.drop_index("ix_finance_transactions_quote_id")
        batch_op.drop_column("quote_id")
    with op.batch_alter_table("repair_orders") as batch_op:
        batch_op.drop_index("ix_repair_orders_processing_group_id")
        batch_op.drop_column("processing_group_id")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_employee_no")
        batch_op.drop_constraint("uq_users_employee_no", type_="unique")
        batch_op.drop_column("employee_no")
