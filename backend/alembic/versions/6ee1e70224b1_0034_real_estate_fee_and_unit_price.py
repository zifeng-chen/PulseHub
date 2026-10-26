"""0034：不动产补「物业费单价」与「参考单价」。

住宅表单一直在收集这两个值（物业费单价 元/㎡·月、参考单价 元/㎡），但模型与
扩展字段白名单里都没有它们：保存时被静默丢弃，于是「编辑 → 再打开」输入框为空，
详情页也无从展示。这里补上列并纳入扩展字段。

Revision ID: 6ee1e70224b1
Revises: e6f7a8b9c0d1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6ee1e70224b1"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "real_estates", sa.Column("management_fee_price", sa.Numeric(14, 4), nullable=True)
    )
    op.add_column("real_estates", sa.Column("unit_price", sa.Numeric(20, 4), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("real_estates") as batch:
        batch.drop_column("unit_price")
        batch.drop_column("management_fee_price")
