"""0048 业务事件复合索引

列表与统计固定按「家庭 + 状态 + occurred_at 区间」过滤并排序，
单列索引会退化为回表 + filesort；补一个三列复合索引。

Revision ID: b8d0f2a4c6e1
Revises: a7c9e1f3b5d2
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op

revision = "b8d0f2a4c6e1"
down_revision = "a7c9e1f3b5d2"
branch_labels = None
depends_on = None

INDEX = "ix_events_household_status_occurred"
TABLE = "business_events"


def upgrade() -> None:
    op.create_index(INDEX, TABLE, ["household_id", "status", "occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
