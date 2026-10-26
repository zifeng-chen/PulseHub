"""0029：负债分类改为「用途」+ 房贷增加「贷款方式」。

问题：负债域的默认分类原本是 房贷/车贷/消费贷/其他负债，和「债务类型」下拉完全重复，
用户看到两个一样的选项，且分类没有额外信息量。

改法：
1. 分类改成「这笔钱借来做什么」：购房 / 购车 / 装修 / 教育 / 医疗 / 经营周转 / 日常周转 / 其他；
2. 已有负债按名称重映射：房贷→购房、车贷→购车、消费贷→日常周转、其他负债→其他；
3. 老分类只有在「没有子分类、且没有负债引用」时才软删除，用户自建的子分类不受影响；
4. 房贷扩展表增加 loan_method（commercial 商业 / provident_fund 公积金 / combined 组合）。

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

NEW_CATEGORIES = ["购房", "购车", "装修", "教育", "医疗", "经营周转", "日常周转", "其他"]
OLD_TO_NEW = {
    "房贷": "购房",
    "车贷": "购车",
    "消费贷": "日常周转",
    "其他负债": "其他",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def migrate_categories(conn) -> None:
    """把负债分类从「类型同名」迁到「用途」；只动没有子分类、没有引用的旧分类。

    单独抽出来是为了能脱离 alembic 上下文，用真实 SQL 做回归测试。
    """
    # 以家庭表为准：即使某个家庭把负债分类删光了，也补上新的用途分类
    households = [row[0] for row in conn.execute(sa.text(
        "SELECT id FROM households WHERE deleted_at IS NULL"
    )).fetchall()]
    if not households:
        households = [row[0] for row in conn.execute(sa.text(
            "SELECT DISTINCT household_id FROM categories WHERE domain = 'liability'"
        )).fetchall()]

    for household_id in households:
        # 2.1 补齐新的用途分类
        ids: dict[str, str] = {}
        for index, name in enumerate(NEW_CATEGORIES):
            row = conn.execute(sa.text(
                "SELECT id FROM categories "
                "WHERE household_id = :hh AND domain = 'liability' AND level = 1 "
                "AND name = :name AND deleted_at IS NULL"
            ), {"hh": household_id, "name": name}).first()
            if row:
                ids[name] = row[0]
                continue
            new_id = uuid.uuid4().hex
            now = _now()
            conn.execute(sa.text(
                "INSERT INTO categories "
                "(id, household_id, domain, parent_id, name, level, sort_order, status, "
                " is_system, ai_recommendable, created_at, updated_at) "
                "VALUES (:id, :hh, 'liability', NULL, :name, 1, :order, 'active', 1, 0, :now, :now)"
            ), {"id": new_id, "hh": household_id, "name": name, "order": index, "now": now})
            ids[name] = new_id

        # 2.2 旧分类的负债改挂到新分类
        for old_name, new_name in OLD_TO_NEW.items():
            old_rows = conn.execute(sa.text(
                "SELECT id FROM categories "
                "WHERE household_id = :hh AND domain = 'liability' AND level = 1 "
                "AND name = :name AND deleted_at IS NULL"
            ), {"hh": household_id, "name": old_name}).fetchall()
            for (old_id,) in old_rows:
                conn.execute(sa.text(
                    "UPDATE liabilities SET category_id = :new WHERE category_id = :old"
                ), {"new": ids[new_name], "old": old_id})

        # 2.3 旧分类：没有子分类、也没有负债引用时软删除
        for old_name in OLD_TO_NEW:
            old_rows = conn.execute(sa.text(
                "SELECT id FROM categories "
                "WHERE household_id = :hh AND domain = 'liability' AND level = 1 "
                "AND name = :name AND deleted_at IS NULL"
            ), {"hh": household_id, "name": old_name}).fetchall()
            for (old_id,) in old_rows:
                children = conn.execute(sa.text(
                    "SELECT 1 FROM categories WHERE parent_id = :pid AND deleted_at IS NULL LIMIT 1"
                ), {"pid": old_id}).first()
                used = conn.execute(sa.text(
                    "SELECT 1 FROM liabilities WHERE category_id = :cid LIMIT 1"
                ), {"cid": old_id}).first()
                if children or used:
                    continue
                conn.execute(sa.text(
                    "UPDATE categories SET deleted_at = :now WHERE id = :id"
                ), {"now": _now(), "id": old_id})


def upgrade() -> None:
    # 1) 房贷贷款方式
    op.add_column("mortgages", sa.Column("loan_method", sa.String(length=32), nullable=True))
    # 2) 每个家庭的负债分类
    migrate_categories(op.get_bind())


def downgrade() -> None:
    conn = op.get_bind()
    reverse = {v: k for k, v in OLD_TO_NEW.items()}

    for household_id, name in conn.execute(sa.text(
        "SELECT household_id, name FROM categories "
        "WHERE domain = 'liability' AND level = 1 AND deleted_at IS NULL"
    )).fetchall():
        if name not in reverse:
            continue
        old_name = reverse[name]
        old_row = conn.execute(sa.text(
            "SELECT id FROM categories WHERE household_id = :hh AND domain = 'liability' "
            "AND name = :name AND deleted_at IS NULL"
        ), {"hh": household_id, "name": old_name}).first()
        if old_row:
            conn.execute(sa.text(
                "UPDATE liabilities SET category_id = :old "
                "WHERE category_id = (SELECT id FROM categories WHERE household_id = :hh "
                "AND domain = 'liability' AND name = :new AND level = 1)"
            ), {"old": old_row[0], "hh": household_id, "new": name})

    # 恢复旧分类，软删除新加的用途分类
    conn.execute(sa.text(
        "UPDATE categories SET deleted_at = NULL WHERE domain = 'liability' "
        "AND name IN ('房贷', '车贷', '消费贷', '其他负债')"
    ))
    conn.execute(sa.text(
        "UPDATE categories SET deleted_at = :now WHERE domain = 'liability' "
        "AND name IN ('购房', '购车', '装修', '教育', '医疗', '经营周转', '日常周转')"
    ), {"now": _now()})

    with op.batch_alter_table("mortgages") as batch:
        batch.drop_column("loan_method")
