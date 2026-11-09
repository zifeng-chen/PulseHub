"""0057 负债自动记账开关

商业贷款/公积金贷款的月供由银行或公积金中心按月自动扣款，用户不希望在系统里
每个月手动补登。给负债加一个开关：开启后到还款日自动按「月供 + 下次还款日」
生成还款记录（扣款账户取自 linked_account_id），关闭后只保留到期提醒。

默认关闭，避免升级后凭空多出账务记录。

Revision ID: e3f4a5b6c7d8
Revises: c9d8e7f6a5b4
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "c9d8e7f6a5b4"
branch_labels = None
depends_on = None

TABLE = "liabilities"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("auto_repay", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column(TABLE, "auto_repay")
