"""0025 credit card is not a debt

信用卡不属于债务：其欠款由账户余额（负值）表达，不应再作为负债记录计入负债合计。
本迁移只清理负债域中的「信用卡」分类（无引用时软删），历史信用卡记录保持原样，
但它们已不再计入净资产（见 stats._liability_parts）。

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("消费分期", "取现", "信用卡"):
        bind.execute(
            sa.text(
                "UPDATE categories SET deleted_at = NOW() "
                "WHERE domain = 'liability' AND name = :n AND deleted_at IS NULL "
                "AND id NOT IN ("
                "  SELECT DISTINCT category_id FROM liabilities WHERE category_id IS NOT NULL"
                ")"
            ),
            {"n": name},
        )


def downgrade() -> None:
    # 分类软删可逆，但无法还原用户当时的选择顺序，保持不动
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
