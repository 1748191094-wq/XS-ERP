"""operations work center and procurement close loop

Revision ID: 0016_operations_procurement
Revises: 0015_offline_node_sync
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_operations_procurement"
down_revision = "0015_offline_node_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("email", sa.String(length=254)))
    op.add_column("suppliers", sa.Column("address", sa.String(length=500)))
    op.add_column("suppliers", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_suppliers_enabled", "suppliers", ["enabled"])

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_no", sa.String(length=48), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("ordered_at", sa.String(length=40)),
        sa.Column("expected_at", sa.String(length=40)),
        sa.Column("completed_at", sa.String(length=40)),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("purchase_no"),
    )
    for column in ("purchase_no", "supplier_id", "status", "ordered_at", "expected_at", "created_by"):
        op.create_index(f"ix_purchase_orders_{column}", "purchase_orders", [column])

    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id"), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=80), nullable=False),
        sa.Column("item_name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("received_quantity", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("returned_quantity", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("remarks", sa.Text()),
    )
    op.create_index("ix_purchase_order_items_purchase_order_id", "purchase_order_items", ["purchase_order_id"])
    op.create_index("ix_purchase_order_items_inventory_item_id", "purchase_order_items", ["inventory_item_id"])

    op.create_table(
        "inventory_lots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_no", sa.String(length=64), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id"), nullable=False),
        sa.Column("purchase_order_item_id", sa.Integer(), sa.ForeignKey("purchase_order_items.id")),
        sa.Column("quantity_received", sa.Numeric(12, 3), nullable=False),
        sa.Column("quantity_remaining", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("serial_numbers_json", sa.JSON()),
        sa.Column("received_at", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("lot_no"),
    )
    for column in ("lot_no", "inventory_item_id", "purchase_order_item_id", "received_at", "created_by"):
        op.create_index(f"ix_inventory_lots_{column}", "inventory_lots", [column])

    op.create_table(
        "stocktakes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stocktake_no", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("committed_at", sa.String(length=40)),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("stocktake_no"),
    )
    for column in ("stocktake_no", "status", "created_by"):
        op.create_index(f"ix_stocktakes_{column}", "stocktakes", [column])

    op.create_table(
        "stocktake_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stocktake_id", sa.Integer(), sa.ForeignKey("stocktakes.id"), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id"), nullable=False),
        sa.Column("system_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("difference_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("remarks", sa.Text()),
        sa.UniqueConstraint("stocktake_id", "inventory_item_id", name="uq_stocktake_inventory_item"),
    )
    op.create_index("ix_stocktake_items_stocktake_id", "stocktake_items", ["stocktake_id"])
    op.create_index("ix_stocktake_items_inventory_item_id", "stocktake_items", ["inventory_item_id"])

    with op.batch_alter_table("inventory_transactions") as batch_op:
        for name in ("purchase_order_id", "purchase_order_item_id", "inventory_lot_id", "stocktake_id"):
            batch_op.add_column(sa.Column(name, sa.Integer()))
        for name, remote_table in (
            ("purchase_order_id", "purchase_orders"),
            ("purchase_order_item_id", "purchase_order_items"),
            ("inventory_lot_id", "inventory_lots"),
            ("stocktake_id", "stocktakes"),
        ):
            batch_op.create_foreign_key(f"fk_inventory_transactions_{name}", remote_table, [name], ["id"])
            batch_op.create_index(f"ix_inventory_transactions_{name}", [name])

    with op.batch_alter_table("finance_transactions") as batch_op:
        batch_op.add_column(sa.Column("purchase_order_id", sa.Integer()))
        batch_op.create_foreign_key("fk_finance_transactions_purchase_order_id", "purchase_orders", ["purchase_order_id"], ["id"])
        batch_op.create_index("ix_finance_transactions_purchase_order_id", ["purchase_order_id"])


def downgrade() -> None:
    with op.batch_alter_table("finance_transactions") as batch_op:
        batch_op.drop_index("ix_finance_transactions_purchase_order_id")
        batch_op.drop_constraint("fk_finance_transactions_purchase_order_id", type_="foreignkey")
        batch_op.drop_column("purchase_order_id")
    with op.batch_alter_table("inventory_transactions") as batch_op:
        for name in ("stocktake_id", "inventory_lot_id", "purchase_order_item_id", "purchase_order_id"):
            batch_op.drop_index(f"ix_inventory_transactions_{name}")
            batch_op.drop_constraint(f"fk_inventory_transactions_{name}", type_="foreignkey")
            batch_op.drop_column(name)
    op.drop_table("stocktake_items")
    op.drop_table("stocktakes")
    op.drop_table("inventory_lots")
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_index("ix_suppliers_enabled", table_name="suppliers")
    op.drop_column("suppliers", "enabled")
    op.drop_column("suppliers", "address")
    op.drop_column("suppliers", "email")
