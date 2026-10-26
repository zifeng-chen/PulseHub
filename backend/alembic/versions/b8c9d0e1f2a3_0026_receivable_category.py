"""0026：其他资产下新增「借出款」二级分类。

借出资金按应收资产管理：
- 借出：新增资产「借出款」，用「待扣资金」从账户扣款 → 现金减少、资产增加，净资产不变；
- 收回：用资产「处置」登记，金额进入指定账户，差额计为已实现损益。

Revision ID: b8c9d0e1f2a3
Revises: a4b5c6d7e8f9
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    conn = op.get_bind()
    roots = conn.execute(sa.text(
        "SELECT id, household_id FROM categories "
        "WHERE domain = 'asset' AND level = 1 AND name = '其他资产' AND deleted_at IS NULL"
    )).fetchall()

    for root_id, household_id in roots:
        exists = conn.execute(sa.text(
            "SELECT 1 FROM categories WHERE parent_id = :pid AND name = '借出款' AND deleted_at IS NULL"
        ), {"pid": root_id}).first()
        if exists:
            continue
        now = _now()
        conn.execute(sa.text(
            "INSERT INTO categories "
            "(id, household_id, domain, parent_id, name, level, sort_order, status, "
            " is_system, ai_recommendable, created_at, updated_at) "
            "VALUES (:id, :hh, 'asset', :pid, '借出款', 2, 0, 'active', 1, 0, :now, :now)"
        ), {"id": uuid.uuid4().hex, "hh": household_id, "pid": root_id, "now": now})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE categories SET deleted_at = :now "
        "WHERE domain = 'asset' AND name = '借出款' AND deleted_at IS NULL"
    ), {"now": _now()})
