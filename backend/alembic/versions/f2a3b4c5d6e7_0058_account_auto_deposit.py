"""0058 公积金账户自动缴存

公积金账户每月有单位与个人缴存进来，用户不希望每月手动补登。
给账户加「每月缴存额 + 缴存日」，到期由后台循环自动记一笔缴存
（provident_fund_deposit，不计入个人收支）。

默认留空，即不自动缴存。

Revision ID: f2a3b4c5d6e7
Revises: e3f4a5b6c7d8
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

TABLE = "accounts"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("auto_deposit_amount", sa.Numeric(20, 4), nullable=True))
    op.add_column(TABLE, sa.Column("auto_deposit_day", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "auto_deposit_day")
    op.drop_column(TABLE, "auto_deposit_amount")
