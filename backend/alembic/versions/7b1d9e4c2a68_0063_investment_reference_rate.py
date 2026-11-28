"""0063：投资账户参考收益率（近 7 日 / 近 1 月年化）

货币基金、活期理财没有买卖现金流，XIRR 算不出来；银行 App 展示的是
「近 7 日年化」「近 1 月年化」。加两列存这个参考值，与 XIRR 并列展示。

Revision ID: 7b1d9e4c2a68
Revises: 9f3c1a7b2e40
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7b1d9e4c2a68"
down_revision = "9f3c1a7b2e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("investment_accounts", sa.Column("reference_rate", sa.Numeric(8, 4), nullable=True))
    op.add_column("investment_accounts", sa.Column("rate_basis", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("investment_accounts", "rate_basis")
    op.drop_column("investment_accounts", "reference_rate")
