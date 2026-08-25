"""add per-viewer forum recommendation signals

Revision ID: 0030_forum_recommendation_signals
Revises: 0029_client_inventory_visibility
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_forum_recommendation_signals"
down_revision = "0029_client_inventory_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forum_post_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("forum_posts.id"), nullable=False),
        sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("client_accounts.id"), nullable=False
        ),
        sa.Column("impression_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dwell_time_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_impression_at", sa.String(length=40), nullable=True),
        sa.Column("last_dwell_at", sa.String(length=40), nullable=True),
        sa.Column("not_interested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("not_interested_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("post_id", "account_id", name="uq_forum_post_signal"),
        sa.CheckConstraint(
            "impression_count >= 0", name="ck_forum_post_signal_impressions_nonnegative"
        ),
        sa.CheckConstraint(
            "dwell_time_ms >= 0", name="ck_forum_post_signal_dwell_nonnegative"
        ),
    )
    op.create_index("ix_forum_post_signals_post_id", "forum_post_signals", ["post_id"])
    op.create_index(
        "ix_forum_post_signals_account_id", "forum_post_signals", ["account_id"]
    )
    op.create_index(
        "ix_forum_post_signals_last_impression_at",
        "forum_post_signals",
        ["last_impression_at"],
    )
    op.create_index(
        "ix_forum_post_signals_not_interested",
        "forum_post_signals",
        ["not_interested"],
    )


def downgrade() -> None:
    op.drop_index("ix_forum_post_signals_not_interested", table_name="forum_post_signals")
    op.drop_index("ix_forum_post_signals_last_impression_at", table_name="forum_post_signals")
    op.drop_index("ix_forum_post_signals_account_id", table_name="forum_post_signals")
    op.drop_index("ix_forum_post_signals_post_id", table_name="forum_post_signals")
    op.drop_table("forum_post_signals")
