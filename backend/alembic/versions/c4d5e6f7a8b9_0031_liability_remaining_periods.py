"""0031：负债增加「剩余期数」。

存量房贷/车贷建档时只知道「还剩多少期」，没有这个字段时还款计划会按合同总期限
从建档日重新排一遍，期数与每期金额都会和银行对不上。剩余期数为空时保持旧行为。

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("liabilities", sa.Column("remaining_periods", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("liabilities") as batch:
        batch.drop_column("remaining_periods")
