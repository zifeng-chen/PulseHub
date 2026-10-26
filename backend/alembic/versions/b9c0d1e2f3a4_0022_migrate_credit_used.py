"""0022 migrate credit_used to opening_balance

「已用额度」改为由余额派生（余额为负 = 欠款）。历史上它是独立字段，
若不迁移，已有信用卡的欠款会显示为 0。

只在 `opening_balance = 0`（没有人工填写期初）时迁移，避免与既有期初冲突；
两者都有值的历史数据保持原样，由用户核对后手工调整。

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
"""

import sqlalchemy as sa
from alembic import op

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE accounts "
        "SET opening_balance = -credit_used, credit_used = NULL "
        "WHERE credit_limit IS NOT NULL "
        "  AND credit_used IS NOT NULL AND credit_used <> 0 "
        "  AND (opening_balance IS NULL OR opening_balance = 0) "
        "  AND deleted_at IS NULL"
    ))


def downgrade() -> None:
    # 数据迁移不可逆（无法区分原始 credit_used 来源），保持不动
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
