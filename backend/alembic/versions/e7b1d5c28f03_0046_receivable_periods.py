"""0046：借出款分期计划表。

为什么需要
----------
金额每期不同的分期，只靠「本金 + 约定还清日」对不了账：用户想知道
「第 3 期该还 3,200，实际还了多少」。之前总账口径只能算「还剩多少」，
不能逐期核对。

设计
----
- `receivable_periods`：每期一行（期次 / 应还月 / 应还金额 / 已分摊）。
- 回款**不绑定**具体期次：按 period_no 顺序自动分摊（先冲最早未结清的期），
  这样「一笔回款跨多期」「多笔回款凑一期」都能算对。
- 期次状态不落库，序列化时按 paid_amount 与 due_date 现算，避免状态过期。

表结构由 alembic 建；本地 SQLite 库用 scripts/ensure_local_schema.py 补。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7b1d5c28f03"
down_revision = "d3a7c40e91b6"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "receivable_periods"):
        return
    op.create_table(
        "receivable_periods",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.String(32), nullable=False, index=True),
        sa.Column("receivable_id", sa.String(32), nullable=False, index=True),
        sa.Column("period_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("due_date", sa.DateTime(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("paid_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_receivable_periods_rec_no", "receivable_periods",
                    ["receivable_id", "period_no"])


def downgrade() -> None:
    if _has_table(op.get_bind(), "receivable_periods"):
        op.drop_table("receivable_periods")
