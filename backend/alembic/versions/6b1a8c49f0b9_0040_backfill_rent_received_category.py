"""0040：回填历史租金实收事件的分类。

此前租金实收事件（rent_received）没有分类，导致收入分析里出现「未分类」。
现在租金实收自动归「租金收入」。

Revision ID: 6b1a8c49f0b9
Revises: 6f0f15aa640b
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6b1a8c49f0b9"
down_revision = "6f0f15aa640b"
branch_labels = None
depends_on = None


def _cat_id(conn, household_id):
    row = conn.execute(
        sa.text(
            "SELECT id FROM categories WHERE household_id = :h AND domain = 'income' "
            "AND name = '租金收入' AND deleted_at IS NULL LIMIT 1"
        ),
        {"h": household_id},
    ).first()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, household_id FROM business_events "
            "WHERE event_type = 'rent_received' AND deleted_at IS NULL AND category_id IS NULL"
        )
    ).fetchall()
    for event_id, household_id in rows:
        cat_id = _cat_id(conn, household_id)
        if cat_id is None:
            continue
        conn.execute(
            sa.text("UPDATE business_events SET category_id = :c WHERE id = :e"),
            {"c": cat_id, "e": event_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE business_events SET category_id = NULL "
            "WHERE event_type = 'rent_received' AND created_by IS NULL"
        )
    )
