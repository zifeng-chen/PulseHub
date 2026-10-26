"""0014 accounts category

accounts 新增 category_id（关联「账户」分类域）；为已初始化家庭补充「账户」分类（08 §3）。

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-10-16 15:30:00.000000
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = 'f0a1b2c3d4e5'
down_revision = 'e9f0a1b2c3d4'
branch_labels = None
depends_on = None

ACCOUNT_CATEGORIES: list[tuple[str, list[str]]] = [
    ("日常收支", ["工资卡", "日常消费卡"]),
    ("储蓄存款", ["活期储蓄", "定期存款"]),
    ("收款账户", []),
    ("备用金", ["现金备用", "应急金"]),
    ("其他账户", []),
]


def upgrade() -> None:
    op.add_column('accounts', sa.Column('category_id', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_accounts_category_id'), 'accounts', ['category_id'], unique=False)

    # 为每个家庭补充「账户」分类域（幂等）
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    households = bind.execute(sa.text("SELECT id FROM households")).fetchall()
    for (hid,) in households:
        for index, (parent_name, children) in enumerate(ACCOUNT_CATEGORIES):
            exists = bind.execute(
                sa.text(
                    "SELECT id FROM categories WHERE household_id = :h AND domain = 'account' "
                    "AND name = :n AND parent_id IS NULL AND deleted_at IS NULL LIMIT 1"
                ),
                {"h": hid, "n": parent_name},
            ).first()
            if exists:
                pid = exists[0]
            else:
                pid = uuid.uuid4().hex
                bind.execute(
                    sa.text(
                        "INSERT INTO categories "
                        "(id, household_id, domain, parent_id, name, level, sort_order, status, "
                        "is_system, ai_recommendable, created_at, updated_at) "
                        "VALUES (:cid, :h, 'account', NULL, :n, 1, :s, 'active', 1, 0, :now, :now)"
                    ),
                    {"cid": pid, "h": hid, "n": parent_name, "s": index, "now": now},
                )
            for cindex, child in enumerate(children):
                cexists = bind.execute(
                    sa.text(
                        "SELECT 1 FROM categories WHERE household_id = :h AND parent_id = :p "
                        "AND name = :n AND deleted_at IS NULL LIMIT 1"
                    ),
                    {"h": hid, "p": pid, "n": child},
                ).first()
                if cexists:
                    continue
                bind.execute(
                    sa.text(
                        "INSERT INTO categories "
                        "(id, household_id, domain, parent_id, name, level, sort_order, status, "
                        "is_system, ai_recommendable, created_at, updated_at) "
                        "VALUES (:cid, :h, 'account', :p, :n, 2, :s, 'active', 1, 0, :now, :now)"
                    ),
                    {"cid": uuid.uuid4().hex, "h": hid, "p": pid, "n": child,
                     "s": cindex, "now": now},
                )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM categories WHERE domain = 'account'"))
    op.drop_index(op.f('ix_accounts_category_id'), table_name='accounts')
    op.drop_column('accounts', 'category_id')
