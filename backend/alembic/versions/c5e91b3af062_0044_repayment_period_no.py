"""0044：借出款回款支持「期次标注」。

背景
----
借出款可能是分期、但每期金额不固定（只要在约定时间内还完即可）。
这种债权做严格的分期计划表没意义（金额对不上计划），所以对账按「总额 + 期限」做：
进度 vs 时间进度、落后还差多少、按当前节奏预计何时还清。

但用户仍希望回款时能标一下「这是第几期」，方便跟对方核对时说得清。
所以只加一个可选字段，不建计划表、不做逐期核销。

字段：receivable_repayments.period_no（可空，1 起）
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5e91b3af062"
down_revision = "b8d4f2c17e05"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column(op.get_bind(), "receivable_repayments", "period_no"):
        op.add_column("receivable_repayments", sa.Column("period_no", sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column(op.get_bind(), "receivable_repayments", "period_no"):
        op.drop_column("receivable_repayments", "period_no")
