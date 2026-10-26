"""0039：回填历史还款本金事件的分类。

此前还款本金事件（liability_repay）没有分类，导致消费分析里出现「未分类」，
且本金/利息拆成两笔。现在本金与利息同分类（房贷还款/车贷还款/其他贷款还款）。

Revision ID: 6f0f15aa640b
Revises: 49ce87e41b91
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6f0f15aa640b"
down_revision = "49ce87e41b91"
branch_labels = None
depends_on = None


def _cat_id(conn, household_id, name):
    row = conn.execute(
        sa.text(
            "SELECT c.id FROM categories c JOIN categories p ON p.id = c.parent_id "
            "WHERE c.household_id = :h AND c.domain = 'expense' AND c.name = :n "
            "AND p.name = '还款' AND c.deleted_at IS NULL LIMIT 1"
        ),
        {"h": household_id, "n": name},
    ).first()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT e.id, e.household_id, l.liability_type "
            "FROM business_events e JOIN liabilities l ON l.id = e.liability_id "
            "WHERE e.event_type = 'liability_repay' AND e.deleted_at IS NULL "
            "AND e.category_id IS NULL AND l.deleted_at IS NULL"
        )
    ).fetchall()
    name_by_type = {"mortgage": "房贷还款", "vehicle_loan": "车贷还款"}
    for event_id, household_id, liability_type in rows:
        name = name_by_type.get(liability_type, "其他贷款还款")
        cat_id = _cat_id(conn, household_id, name)
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
            "WHERE event_type = 'liability_repay' AND created_by IS NULL"
        )
    )
