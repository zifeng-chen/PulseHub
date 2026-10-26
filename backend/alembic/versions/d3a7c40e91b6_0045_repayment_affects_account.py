"""0045：借出款支持「补录历史回款（不进当前账户）」。

需求背景
--------
借出款常见两种历史情况：
1. 之前借出去的（`lent_mode=existing`）——已由 0043 支持，不动账户；
2. **借出后对方已经还过一部分，但那笔钱早被花掉了**——现在补录时，
   如果把回款按正常流程记，会生成资金流入事件，把当前账户余额虚增。

所以回款需要区分「当时真的进过账户」与「只是账面上冲减应收」。
`affects_account=false` 时：只写回款记录、减少应收、记估值时间线，
不生成 receivable_repay 事件，也不影响账户余额与收支统计。

字段：receivable_repayments.affects_account（默认 1，历史数据都是正常回款）
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d3a7c40e91b6"
down_revision = "c5e91b3af062"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column(op.get_bind(), "receivable_repayments", "affects_account"):
        op.add_column("receivable_repayments", sa.Column(
            "affects_account", sa.Boolean(), nullable=False, server_default=sa.text("1")))


def downgrade() -> None:
    if _has_column(op.get_bind(), "receivable_repayments", "affects_account"):
        op.drop_column("receivable_repayments", "affects_account")
