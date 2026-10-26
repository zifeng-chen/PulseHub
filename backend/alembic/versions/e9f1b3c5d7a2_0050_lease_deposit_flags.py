"""0050 租约押金：是否已收 / 是否计入净资产

押金先是一个事实标记（收了没有），再单独决定是否计入净资产。
与「登记押金」流程并存：登记过押金（held）时视为已收，且钱已在账户里，不再重复计入。

Revision ID: e9f1b3c5d7a2
Revises: c1e3a5b7d9f2
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e9f1b3c5d7a2"
down_revision = "c1e3a5b7d9f2"
branch_labels = None
depends_on = None

TABLE = "leases"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("deposit_received", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(TABLE, sa.Column("deposit_include_in_net_worth", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column(TABLE, "deposit_include_in_net_worth")
    op.drop_column(TABLE, "deposit_received")
