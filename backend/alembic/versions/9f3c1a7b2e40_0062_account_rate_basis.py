"""0062：账户收益口径（近 7 日年化 / 近 1 月年化）

余额宝类产品展示的是「近 7 日年化」，理财是「近 1 月年化」，与存款年利率不是一回事。
单靠 interest_rate 无法表达口径，加一列区分，避免把 -1.25%（近一月）当成整年利率。

Revision ID: 9f3c1a7b2e40
Revises: f4a5b6c7d8e9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9f3c1a7b2e40"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("rate_basis", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "rate_basis")
