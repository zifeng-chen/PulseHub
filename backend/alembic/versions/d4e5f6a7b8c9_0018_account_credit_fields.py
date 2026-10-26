"""0018 account credit fields & category attribute_profile

1. accounts 新增信用卡/信用类字段：信用额度、已用额度、卡片有效期、账单日、还款日；
2. categories 新增 attribute_profile（字段档案），并按账户域分类回填，
   使新建账户时能按所选分类显示不同专属字段。

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


# 账户域一级分类 → 字段档案
_ROOT_PROFILE = [
    ("银行卡", "debit_card"),
    ("储蓄存款", "debit_card"),
    ("消费信贷", "credit_line"),
    ("电子钱包", "wallet"),
    ("现金", "cash"),
]
# 子分类覆盖父分类档案
_CHILD_PROFILE = [
    ("信用卡", "credit_card"),
]


def upgrade() -> None:
    op.add_column("accounts", sa.Column("credit_limit", sa.Numeric(20, 4), nullable=True))
    op.add_column("accounts", sa.Column("credit_used", sa.Numeric(20, 4), nullable=True))
    op.add_column("accounts", sa.Column("card_expiry", sa.String(length=7), nullable=True))
    op.add_column("accounts", sa.Column("bill_day", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("due_day", sa.Integer(), nullable=True))

    op.add_column("categories", sa.Column("attribute_profile", sa.String(length=32), nullable=True))

    bind = op.get_bind()

    # 一级分类：设档案；其子分类继承（仅当子分类未设置）
    for name, profile in _ROOT_PROFILE:
        rows = bind.execute(
            sa.text(
                "SELECT id FROM categories WHERE domain = 'account' AND parent_id IS NULL "
                "AND name = :n AND deleted_at IS NULL"
            ),
            {"n": name},
        ).fetchall()
        for (category_id,) in rows:
            bind.execute(
                sa.text(
                    "UPDATE categories SET attribute_profile = :p "
                    "WHERE id = :cid AND attribute_profile IS NULL"
                ),
                {"p": profile, "cid": category_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE categories SET attribute_profile = :p "
                    "WHERE parent_id = :cid AND attribute_profile IS NULL"
                ),
                {"p": profile, "cid": category_id},
            )

    # 子分类特例（信用卡需要额度/有效期/账单日/还款日）
    for name, profile in _CHILD_PROFILE:
        bind.execute(
            sa.text(
                "UPDATE categories SET attribute_profile = :p "
                "WHERE domain = 'account' AND parent_id IS NOT NULL AND name = :n"
            ),
            {"p": profile, "n": name},
        )


def downgrade() -> None:
    op.drop_column("categories", "attribute_profile")
    op.drop_column("accounts", "due_day")
    op.drop_column("accounts", "bill_day")
    op.drop_column("accounts", "card_expiry")
    op.drop_column("accounts", "credit_used")
    op.drop_column("accounts", "credit_limit")
