"""0012 category subcategories

给已初始化家庭的默认一级分类补充常见二级子分类（幂等：按 parent_id + name 判重）。

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-16 15:00:00.000000
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = 'd8e9f0a1b2c3'
down_revision = 'c7d8e9f0a1b2'
branch_labels = None
depends_on = None

# 一级分类名 → 二级子分类（跨分类域，父名唯一）
SUBCATEGORIES: dict[str, list[str]] = {
    "餐饮": ["早餐", "午餐", "晚餐", "外卖", "买菜"],
    "交通": ["打车", "加油", "公交地铁", "停车费"],
    "居住": ["房租", "物业费", "维修"],
    "购物": ["日用百货", "服饰", "数码", "家电"],
    "医疗": ["门诊", "药品", "体检"],
    "教育": ["学费", "培训", "书籍"],
    "娱乐": ["影音", "旅行", "运动健身"],
    "不动产": ["住宅", "商铺", "写字楼"],
    "车辆": ["燃油车", "新能源车"],
    "贵金属": ["黄金", "白银"],
    "信用卡": ["消费分期", "取现"],
    "股票": ["A股", "港股", "美股"],
    "基金": ["股票基金", "债券基金", "货币基金"],
    "存款": ["活期", "定期"],
}


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    households = bind.execute(sa.text("SELECT id FROM households")).fetchall()
    for (hid,) in households:
        rows = bind.execute(
            sa.text(
                "SELECT id, name FROM categories "
                "WHERE household_id = :h AND deleted_at IS NULL AND level = 1"
            ),
            {"h": hid},
        ).fetchall()
        name_to_id = {name: cid for cid, name in rows}
        for parent_name, children in SUBCATEGORIES.items():
            pid = name_to_id.get(parent_name)
            if not pid:
                continue
            for idx, child in enumerate(children):
                exists = bind.execute(
                    sa.text(
                        "SELECT 1 FROM categories WHERE household_id = :h AND parent_id = :p "
                        "AND name = :n AND deleted_at IS NULL LIMIT 1"
                    ),
                    {"h": hid, "p": pid, "n": child},
                ).first()
                if exists:
                    continue
                bind.execute(
                    sa.text(
                        "INSERT INTO categories "
                        "(id, household_id, domain, parent_id, name, level, sort_order, status, "
                        "is_system, ai_recommendable, created_at, updated_at) "
                        "SELECT :cid, :h, domain, :p, :n, 2, :s, 'active', 1, ai_recommendable, :now, :now "
                        "FROM categories WHERE id = :p"
                    ),
                    {"cid": uuid.uuid4().hex, "h": hid, "p": pid, "n": child,
                     "s": idx, "now": now},
                )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM categories WHERE level = 2 AND is_system = 1"))
