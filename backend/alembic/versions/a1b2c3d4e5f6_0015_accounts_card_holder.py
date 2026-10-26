"""0015 accounts card & holder

accounts 新增 card_last4 / holder_name；为已初始化家庭补充银行卡、消费信贷、电子钱包等账户子分类。

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-10-17 00:15:00.000000
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'f0a1b2c3d4e5'
branch_labels = None
depends_on = None

# 需要补充的账户分类（父 → 子），幂等：已存在的父/子不重复创建
ACCOUNT_CATEGORIES: list[tuple[str, list[str]]] = [
    ("银行卡", ["储蓄卡", "信用卡"]),
    ("消费信贷", ["花呗", "借呗", "白条", "京东金条", "美团月付", "抖音月付"]),
    ("电子钱包", ["支付宝", "微信零钱"]),
    ("储蓄存款", ["活期储蓄", "定期存款"]),
    ("现金", ["现金备用", "应急金"]),
]


def upgrade() -> None:
    op.add_column('accounts', sa.Column('card_last4', sa.String(length=4), nullable=True))
    op.add_column('accounts', sa.Column('holder_name', sa.String(length=64), nullable=True))

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
    # 分类保留（不清除用户可能已引用的账户分类）
    op.drop_column('accounts', 'holder_name')
    op.drop_column('accounts', 'card_last4')
