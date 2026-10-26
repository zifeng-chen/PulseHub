"""0049 租约增加「是否计入净资产」

已出租的房产有时不想计入家庭净资产（代持、公司产权等），
开关放在租约上而不是资产上：同一房产换租约时可以单独决定。

Revision ID: c1e3a5b7d9f2
Revises: b8d0f2a4c6e1
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1e3a5b7d9f2"
down_revision = "b8d0f2a4c6e1"
branch_labels = None
depends_on = None

TABLE = "leases"
COLUMN = "include_in_net_worth"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
