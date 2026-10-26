"""0017 clean legacy account categories

清理账户域的旧分类残留（早期种子的「日常收支」「备用金」及其子分类，
与后来新增的「银行卡」「储蓄存款」「现金」等重叠）：

1. 把旧「日常收支」下仍有价值的子分类（工资卡 / 日常消费卡）改挂到「银行卡」下，
   并归入 bank 适用范围；
2. 对**未被任何账户引用**的旧一级分类（日常收支 / 备用金）及其子分类停用，
   使它们不再出现在新建账户的分类下拉中。

幂等：重复执行结果一致；有账户引用时保留原分类不动。

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

_LEGACY_ROOTS = ("日常收支", "备用金")
_MOVE_CHILDREN = ("工资卡", "日常消费卡")


def _first_id(bind, sql: str, params: dict | None = None):
    row = bind.execute(sa.text(sql), params or {}).fetchone()
    return row[0] if row else None


def upgrade() -> None:
    bind = op.get_bind()

    bank_id = _first_id(
        bind,
        "SELECT id FROM categories WHERE domain = 'account' AND parent_id IS NULL "
        "AND name = '银行卡' AND deleted_at IS NULL LIMIT 1",
    )
    legacy_root_id = _first_id(
        bind,
        "SELECT id FROM categories WHERE domain = 'account' AND parent_id IS NULL "
        "AND name = '日常收支' AND deleted_at IS NULL LIMIT 1",
    )

    # 1) 保留有用的子分类：改挂到「银行卡」下并归入 bank 类型
    if bank_id and legacy_root_id:
        bind.execute(
            sa.text(
                "UPDATE categories SET parent_id = :b, level = 2, status = 'active', "
                "applies_to = 'bank' "
                "WHERE domain = 'account' AND parent_id = :l AND name IN :names"
            ).bindparams(sa.bindparam("names", expanding=True)),
            {"b": bank_id, "l": legacy_root_id, "names": list(_MOVE_CHILDREN)},
        )

    # 2) 停用旧一级分类及其子分类（仅在无账户引用时）
    for name in _LEGACY_ROOTS:
        root_ids = [
            r[0]
            for r in bind.execute(
                sa.text(
                    "SELECT id FROM categories WHERE domain = 'account' AND parent_id IS NULL "
                    "AND name = :n AND deleted_at IS NULL"
                ),
                {"n": name},
            ).fetchall()
        ]
        for root_id in root_ids:
            used = bind.execute(
                sa.text(
                    "SELECT 1 FROM accounts WHERE category_id = :cid AND deleted_at IS NULL LIMIT 1"
                ),
                {"cid": root_id},
            ).fetchone()
            if used:
                continue
            bind.execute(
                sa.text("UPDATE categories SET status = 'disabled' WHERE id = :cid"),
                {"cid": root_id},
            )
            bind.execute(
                sa.text("UPDATE categories SET status = 'disabled' WHERE parent_id = :cid"),
                {"cid": root_id},
            )


def downgrade() -> None:
    # 数据清理无副作用；如需恢复可手动在「分类管理」中启用
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
