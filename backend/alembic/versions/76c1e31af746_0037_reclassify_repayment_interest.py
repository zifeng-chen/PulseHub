"""0037：把历史还款利息事件回填到「还款」分类。

此前还款利息统一记到「利息与费用 / 贷款利息」，现在按债务类型细化：
房贷→房贷还款、车贷→车贷还款、其余→其他贷款还款。本迁移把历史事件
从「贷款利息」挪到正确分类。

Revision ID: 76c1e31af746
Revises: b40b299fe8e7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "76c1e31af746"
down_revision = "b40b299fe8e7"
branch_labels = None
depends_on = None


def _cat_id(conn, household_id, name, parent_name):
    row = conn.execute(
        sa.text(
            "SELECT c.id FROM categories c JOIN categories p ON p.id = c.parent_id "
            "WHERE c.household_id = :h AND c.domain = 'expense' AND c.name = :n "
            "AND p.name = :pn AND c.deleted_at IS NULL LIMIT 1"
        ),
        {"h": household_id, "n": name, "pn": parent_name},
    ).first()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()
    # 房贷还款、车贷还款、其他贷款还款 都挂在「还款」一级下
    # 找到所有还款利息事件（标题以「负债利息与费用」开头），按关联负债类型归类
    rows = conn.execute(
        sa.text(
            "SELECT e.id, e.household_id, l.liability_type "
            "FROM business_events e JOIN liabilities l ON l.id = e.liability_id "
            "WHERE e.event_type = 'expense' AND e.deleted_at IS NULL "
            "AND l.deleted_at IS NULL AND e.title LIKE '负债利息与费用%'"
        )
    ).fetchall()

    name_by_type = {
        "mortgage": "房贷还款",
        "vehicle_loan": "车贷还款",
    }
    for event_id, household_id, liability_type in rows:
        name = name_by_type.get(liability_type, "其他贷款还款")
        cat_id = _cat_id(conn, household_id, name, "还款")
        if cat_id is None:
            continue
        conn.execute(
            sa.text("UPDATE business_events SET category_id = :c WHERE id = :e"),
            {"c": cat_id, "e": event_id},
        )


def downgrade() -> None:
    # 回退到旧的「贷款利息」分类
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT e.id, e.household_id FROM business_events e "
            "JOIN liabilities l ON l.id = e.liability_id "
            "WHERE e.event_type = 'expense' AND e.deleted_at IS NULL "
            "AND e.title LIKE '负债利息与费用%'"
        )
    ).fetchall()
    for event_id, household_id in rows:
        cat_id = _cat_id(conn, household_id, "贷款利息", "利息与费用")
        if cat_id is None:
            continue
        conn.execute(
            sa.text("UPDATE business_events SET category_id = :c WHERE id = :e"),
            {"c": cat_id, "e": event_id},
        )
