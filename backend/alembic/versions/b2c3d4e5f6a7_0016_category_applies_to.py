"""0016 category applies_to

分类新增 applies_to（适用范围）。账户域用于按账户类型联动筛选分类，
并给已初始化家庭的账户域分类回填标签（幂等）。

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


# 账户域一级分类名 → 适用账户类型（account_type 枚举 code，逗号分隔）
_ACCOUNT_APPLIES = [
    ("银行卡", "bank"),
    ("储蓄存款", "bank"),
    ("消费信贷", "e_wallet"),
    ("电子钱包", "e_wallet"),
    ("现金", "cash"),
]


def upgrade() -> None:
    op.add_column("categories", sa.Column("applies_to", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    # 给现有家庭的账户域分类打标签：一级分类与其子分类一致；仅填空值，保证幂等
    for name, applies in _ACCOUNT_APPLIES:
        rows = bind.execute(
            sa.text(
                "SELECT id FROM categories "
                "WHERE domain = 'account' AND parent_id IS NULL AND name = :n "
                "AND deleted_at IS NULL"
            ),
            {"n": name},
        ).fetchall()
        for (category_id,) in rows:
            bind.execute(
                sa.text(
                    "UPDATE categories SET applies_to = :a "
                    "WHERE id = :cid AND applies_to IS NULL"
                ),
                {"a": applies, "cid": category_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE categories SET applies_to = :a "
                    "WHERE parent_id = :cid AND applies_to IS NULL"
                ),
                {"a": applies, "cid": category_id},
            )


def downgrade() -> None:
    op.drop_column("categories", "applies_to")
