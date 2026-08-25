"""initial modular repair management schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-16
"""
from alembic import op

from app.core.database import Base
from app.models import entities  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 新库只新增表；旧 quotation.db 独立保留并由 scripts/migrate_legacy.py 安全迁移。
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # 数据安全优先：不提供自动删表降级。需要降级时先备份后人工处理。
    pass
