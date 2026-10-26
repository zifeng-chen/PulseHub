"""0036：给存量家庭补齐新增支出分类。

新增一级「通讯」「还款」与若干二级分类（话费月租、油费、高速费、兴趣班、
房贷还款、车贷还款等）。新家庭由种子自动带上，存量家庭靠本迁移补齐。

Revision ID: b40b299fe8e7
Revises: a1012edfa22a
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "b40b299fe8e7"
down_revision = "a1012edfa22a"
branch_labels = None
depends_on = None

# 新增分类：一级 -> 二级列表
NEW_TREE = [
    ("还款", ["房贷还款", "车贷还款", "信用卡还款", "其他贷款还款"]),
    ("通讯", ["话费月租", "宽带"]),
]

# 在已有父类下补的二级分类
NEW_CHILDREN = {
    "交通": ["油费", "高速费", "洗车"],
    "教育": ["兴趣班"],
    "水电煤": ["水费", "电费", "燃气费", "供暖费"],
}


def _find_category(conn, household_id, name, parent_id):
    row = conn.execute(
        sa.text(
            "SELECT id FROM categories WHERE household_id = :h AND domain = 'expense' "
            "AND name = :n AND deleted_at IS NULL "
            "AND ((parent_id = :p) OR (:p IS NULL AND parent_id IS NULL)) LIMIT 1"
        ),
        {"h": household_id, "n": name, "p": parent_id},
    ).first()
    return row[0] if row else None


def _max_sort(conn, household_id, parent_id):
    row = conn.execute(
        sa.text(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories "
            "WHERE household_id = :h AND domain = 'expense' "
            "AND ((parent_id = :p) OR (:p IS NULL AND parent_id IS NULL))"
        ),
        {"h": household_id, "p": parent_id},
    ).first()
    return row[0]


def _insert(conn, household_id, name, parent_id, level, sort_order):
    category_id = uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO categories (id, household_id, domain, parent_id, name, level, "
            " sort_order, status, is_system, ai_recommendable, created_at, updated_at) "
            "VALUES (:id, :h, 'expense', :p, :n, :lvl, :sort, 'active', 1, 1, "
            "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": category_id, "h": household_id, "p": parent_id, "n": name,
          "lvl": level, "sort": sort_order},
    )
    return category_id


def upgrade() -> None:
    conn = op.get_bind()
    households = [
        r[0] for r in conn.execute(
            sa.text("SELECT id FROM households WHERE deleted_at IS NULL")).fetchall()
    ]
    for h in households:
        for parent_name, children in NEW_TREE:
            parent_id = _find_category(conn, h, parent_name, None)
            if parent_id is None:
                parent_id = _insert(conn, h, parent_name, None, 1, _max_sort(conn, h, None))
            for child in children:
                if _find_category(conn, h, child, parent_id) is None:
                    _insert(conn, h, child, parent_id, 2, _max_sort(conn, h, parent_id))
        for parent_name, children in NEW_CHILDREN.items():
            parent_id = _find_category(conn, h, parent_name, None)
            if parent_id is None:
                continue
            for child in children:
                if _find_category(conn, h, child, parent_id) is None:
                    _insert(conn, h, child, parent_id, 2, _max_sort(conn, h, parent_id))


def downgrade() -> None:
    conn = op.get_bind()
    names = ["房贷还款", "车贷还款", "信用卡还款", "其他贷款还款",
             "话费月租", "宽带", "油费", "高速费", "洗车", "兴趣班",
             "水费", "电费", "燃气费", "供暖费", "通讯", "还款"]
    for n in names:
        conn.execute(sa.text(
            "DELETE FROM categories WHERE domain = 'expense' AND name = :n "
            "AND created_by IS NULL"), {"n": n})
