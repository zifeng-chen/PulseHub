"""0019 account savings & loan fields

按分类继续细化账户字段：
1. accounts 新增年利率（interest_rate）、存期（term_months）、到期日（maturity_date）；
2. 分类字段档案细分：储蓄存款（活期/定期）、借呗类借款。
   仅在当前值仍是上一版预设（debit_card / credit_line）时改写，避免覆盖用户自定义。

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


# (分类名, 原档案, 新档案, 是否仅限一级分类)
_PROFILE_UPGRADES = [
    ("储蓄存款", "debit_card", "savings_current", True),
    ("活期储蓄", "debit_card", "savings_current", False),
    ("定期存款", "debit_card", "savings_fixed", False),
    ("借呗", "credit_line", "loan_line", False),
    ("京东金条", "credit_line", "loan_line", False),
]


def upgrade() -> None:
    op.add_column("accounts", sa.Column("interest_rate", sa.Numeric(8, 4), nullable=True))
    op.add_column("accounts", sa.Column("term_months", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("maturity_date", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    for name, old, new, root_only in _PROFILE_UPGRADES:
        sql = (
            "UPDATE categories SET attribute_profile = :new "
            "WHERE domain = 'account' AND name = :name AND attribute_profile = :old"
        )
        if root_only:
            sql += " AND parent_id IS NULL"
        else:
            sql += " AND parent_id IS NOT NULL"
        bind.execute(sa.text(sql), {"new": new, "name": name, "old": old})


def downgrade() -> None:
    bind = op.get_bind()
    for name, old, new, root_only in _PROFILE_UPGRADES:
        sql = (
            "UPDATE categories SET attribute_profile = :old "
            "WHERE domain = 'account' AND name = :name AND attribute_profile = :new"
        )
        if root_only:
            sql += " AND parent_id IS NULL"
        else:
            sql += " AND parent_id IS NOT NULL"
        bind.execute(sa.text(sql), {"new": new, "name": name, "old": old})

    op.drop_column("accounts", "maturity_date")
    op.drop_column("accounts", "term_months")
    op.drop_column("accounts", "interest_rate")
