"""0030：负债区分「贷款金额」与「剩余金额」。

问题：录入存量房贷/车贷时只有一个「初始金额」，用户填的是合同金额还是当前剩余本金说不清；
剩余本金 = 初始金额 + 资金变动，无法在建档时就等于「当前剩余」。

改法：新增 opening_outstanding（建档时点剩余本金，可空）。
- 为空：剩余本金仍从 initial_amount 起算（新贷款，行为不变）；
- 有值：剩余本金从该值起算，initial_amount 只作为合同/贷款金额展示。

Revision ID: b3c4d5e6f7a8
Revises: a7b8c9d0e1f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3c4d5e6f7a8"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("liabilities", sa.Column("opening_outstanding", sa.Numeric(20, 4), nullable=True))
    # 车贷也用同一套贷款方式（商业 / 公积金 / 组合），与房贷保持一致
    op.add_column("vehicle_loans", sa.Column("loan_method", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vehicle_loans") as batch:
        batch.drop_column("loan_method")
    with op.batch_alter_table("liabilities") as batch:
        batch.drop_column("opening_outstanding")
