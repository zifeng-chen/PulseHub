"""0028：借出款（应收）明细与回款记录 + 借出款分类细化。

- 新增 asset_receivables / receivable_repayments 两张表；
- 把「借出款」提升为资产域一级分类，下挂：现金出借 / 银行卡出借 / 信用卡出借 /
  电子钱包出借 / 其他出借（新装与既有家庭都生效，脚本幂等）。

Revision ID: f1a2b3c4d5e6
Revises: c9d0e1f2a3b4
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

CHILDREN = [
    ("现金出借", 0),
    ("银行卡出借", 1),
    ("信用卡出借", 2),
    ("电子钱包出借", 3),
    ("其他出借", 4),
]


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("updated_by", sa.String(length=32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ]


def _new_id() -> str:
    return uuid.uuid4().hex


def upgrade() -> None:
    op.create_table(
        "asset_receivables",
        sa.Column("household_id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("borrower_name", sa.String(length=64), nullable=False),
        sa.Column("borrower_id_no", sa.String(length=64), nullable=True),
        sa.Column("lend_type", sa.String(length=32), nullable=False, server_default="cash"),
        sa.Column("principal", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("lent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repayment_method", sa.String(length=32), nullable=False, server_default="lump_sum"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fee_bearer", sa.String(length=16), nullable=False, server_default="borrower"),
        sa.Column("fee_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("source_account_id", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_base_columns(),
    )
    op.create_index("ix_asset_receivables_household_id", "asset_receivables", ["household_id"])
    op.create_index("ix_asset_receivables_asset_id", "asset_receivables", ["asset_id"], unique=True)

    op.create_table(
        "receivable_repayments",
        sa.Column("household_id", sa.String(length=32), nullable=False),
        sa.Column("receivable_id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("account_id", sa.String(length=32), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("event_id", sa.String(length=32), nullable=True),
        *_base_columns(),
    )
    op.create_index("ix_receivable_repayments_household_id", "receivable_repayments", ["household_id"])
    op.create_index("ix_receivable_repayments_receivable_id", "receivable_repayments", ["receivable_id"])
    op.create_index("ix_receivable_repayments_asset_id", "receivable_repayments", ["asset_id"])

    bind = op.get_bind()
    households = bind.execute(
        sa.text("SELECT DISTINCT household_id FROM categories WHERE domain = 'asset'")
    ).fetchall()

    for (household_id,) in households:
        root = bind.execute(
            sa.text(
                "SELECT id FROM categories WHERE household_id = :h AND domain = 'asset' "
                "AND name = '借出款' AND deleted_at IS NULL LIMIT 1"
            ),
            {"h": household_id},
        ).fetchone()
        if root is None:
            root_id = _new_id()
            bind.execute(
                sa.text(
                    "INSERT INTO categories (id, household_id, domain, parent_id, name, level, "
                    "sort_order, status, is_system, ai_recommendable, applies_to) "
                    "VALUES (:id, :h, 'asset', NULL, '借出款', 1, 80, 'active', 1, 1, 'other')"
                ),
                {"id": root_id, "h": household_id},
            )
        else:
            root_id = root[0]
            # 原来挂在「其他资产」下面：提升为一级分类，已有资产仍指向同一个分类，不受影响
            bind.execute(
                sa.text(
                    "UPDATE categories SET parent_id = NULL, level = 1, applies_to = 'other' "
                    "WHERE id = :id"
                ),
                {"id": root_id},
            )

        for name, order in CHILDREN:
            exists = bind.execute(
                sa.text(
                    "SELECT id FROM categories WHERE household_id = :h AND domain = 'asset' "
                    "AND name = :n AND parent_id = :p AND deleted_at IS NULL LIMIT 1"
                ),
                {"h": household_id, "n": name, "p": root_id},
            ).fetchone()
            if exists:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO categories (id, household_id, domain, parent_id, name, level, "
                    "sort_order, status, is_system, ai_recommendable, applies_to) "
                    "VALUES (:id, :h, 'asset', :p, :n, 2, :o, 'active', 1, 1, 'other')"
                ),
                {"id": _new_id(), "h": household_id, "p": root_id, "n": name, "o": order},
            )


def downgrade() -> None:
    # 分类层级调整不可逆；只回滚新表
    op.drop_table("receivable_repayments")
    op.drop_table("asset_receivables")
