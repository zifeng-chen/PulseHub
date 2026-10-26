"""0033：为「利息与费用」补支出分类，并回填系统自动记账的流水。

登记还款时系统会自动生成一条「负债利息与费用 · 某贷款」的支出事件，
转账手续费同理，但这两类事件此前没有 category_id，流水与分析里一律显示
「未分类」，和「这是贷款还款产生的利息」的业务事实对不上。

本迁移：
1. 给每个家庭补出支出分类「利息与费用 → 贷款利息 / 手续费」（已存在则复用）；
2. 把历史上 category_id 为空的这两类事件回填到对应分类。

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None

PARENT_NAME = "利息与费用"
INTEREST_NAME = "贷款利息"
FEE_NAME = "手续费"
# 系统自动生成的两类支出：标题前缀固定，回填时按前缀识别
INTEREST_TITLE = "负债利息与费用%"
FEE_TITLE = "转账手续费%"


def _find_category(conn, household_id: str, name: str, parent_id: str | None) -> str | None:
    row = conn.execute(
        sa.text(
            "SELECT id FROM categories "
            "WHERE household_id = :h AND domain = 'expense' AND name = :n "
            "AND deleted_at IS NULL "
            "AND ((parent_id = :p) OR (parent_id IS NULL AND :p IS NULL)) "
            "LIMIT 1"
        ),
        {"h": household_id, "n": name, "p": parent_id},
    ).first()
    return row[0] if row else None


def _ensure_category(
    conn, household_id: str, name: str, parent_id: str | None, level: int, sort_order: int,
) -> str:
    existing = _find_category(conn, household_id, name, parent_id)
    if existing:
        return existing
    category_id = uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO categories "
            "(id, household_id, domain, parent_id, name, level, sort_order, status, "
            " is_system, ai_recommendable, created_at, updated_at) "
            "VALUES (:id, :h, 'expense', :p, :n, :lvl, :sort, 'active', 1, 1, "
            "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "id": category_id, "h": household_id, "p": parent_id,
            "n": name, "lvl": level, "sort": sort_order,
        },
    )
    return category_id


def _backfill(conn, household_id: str, category_id: str, title_like: str) -> None:
    conn.execute(
        sa.text(
            "UPDATE business_events SET category_id = :c "
            "WHERE household_id = :h AND category_id IS NULL "
            "AND event_type = 'expense' AND deleted_at IS NULL AND title LIKE :t"
        ),
        {"c": category_id, "h": household_id, "t": title_like},
    )


def upgrade() -> None:
    conn = op.get_bind()
    households = [
        row[0] for row in conn.execute(
            sa.text("SELECT id FROM households WHERE deleted_at IS NULL")
        ).fetchall()
    ]
    for household_id in households:
        parent_id = _find_category(conn, household_id, PARENT_NAME, None)
        if parent_id is None:
            # 「利息与费用」排在「其他支出」之前，保持「其他」在最末的习惯
            other = conn.execute(
                sa.text(
                    "SELECT sort_order FROM categories "
                    "WHERE household_id = :h AND domain = 'expense' AND parent_id IS NULL "
                    "AND name = '其他支出' AND deleted_at IS NULL LIMIT 1"
                ),
                {"h": household_id},
            ).first()
            if other is not None:
                order = int(other[0])
                conn.execute(
                    sa.text(
                        "UPDATE categories SET sort_order = sort_order + 1 "
                        "WHERE household_id = :h AND domain = 'expense' "
                        "AND parent_id IS NULL AND sort_order >= :o"
                    ),
                    {"h": household_id, "o": order},
                )
            else:
                order = int(conn.execute(
                    sa.text(
                        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories "
                        "WHERE household_id = :h AND domain = 'expense' AND parent_id IS NULL"
                    ),
                    {"h": household_id},
                ).scalar() or 0)
            parent_id = _ensure_category(conn, household_id, PARENT_NAME, None, 1, order)

        interest_id = _ensure_category(conn, household_id, INTEREST_NAME, parent_id, 2, 0)
        fee_id = _ensure_category(conn, household_id, FEE_NAME, parent_id, 2, 1)

        _backfill(conn, household_id, interest_id, INTEREST_TITLE)
        _backfill(conn, household_id, fee_id, FEE_TITLE)


def downgrade() -> None:
    conn = op.get_bind()
    households = [
        row[0] for row in conn.execute(
            sa.text("SELECT id FROM households WHERE deleted_at IS NULL")
        ).fetchall()
    ]
    for household_id in households:
        conn.execute(
            sa.text(
                "UPDATE business_events SET category_id = NULL "
                "WHERE household_id = :h AND deleted_at IS NULL "
                "AND (title LIKE :i OR title LIKE :f)"
            ),
            {"h": household_id, "i": INTEREST_TITLE, "f": FEE_TITLE},
        )
        for name in (INTEREST_NAME, FEE_NAME):
            conn.execute(
                sa.text(
                    "DELETE FROM categories WHERE household_id = :h AND domain = 'expense' "
                    "AND name = :n AND created_by IS NULL"
                ),
                {"h": household_id, "n": name},
            )
        parent = conn.execute(
            sa.text(
                "SELECT sort_order FROM categories "
                "WHERE household_id = :h AND domain = 'expense' AND parent_id IS NULL "
                "AND name = :n AND created_by IS NULL LIMIT 1"
            ),
            {"h": household_id, "n": PARENT_NAME},
        ).first()
        if parent is not None:
            # 把当初为它腾位置而整体后移的分类挪回去
            conn.execute(
                sa.text(
                    "UPDATE categories SET sort_order = sort_order - 1 "
                    "WHERE household_id = :h AND domain = 'expense' "
                    "AND parent_id IS NULL AND sort_order > :o"
                ),
                {"h": household_id, "o": int(parent[0])},
            )
            conn.execute(
                sa.text(
                    "DELETE FROM categories WHERE household_id = :h AND domain = 'expense' "
                    "AND name = :n AND parent_id IS NULL AND created_by IS NULL"
                ),
                {"h": household_id, "n": PARENT_NAME},
            )
