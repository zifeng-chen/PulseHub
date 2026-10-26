"""0020 fix asset_type from category

历史数据修正：早期从「资产」总览页新增的资产，`asset_type` 会落到 `other`，
而用户实际选择的分类是「不动产 / 车位 / 车辆 / 贵金属」下的节点。
这会导致负债（房贷/车贷）的「关联资产」与租约的「关联房产」都选不到该资产。

本迁移按分类树把这类资产的类型纠正为正确值；已是正确类型的不动。

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

# 一级分类名 → 资产类型（与 app/services/assets.py 的映射一致）
_ROOT_TO_TYPE = [
    ("不动产", "real_estate"),
    ("车位", "parking"),
    ("车辆", "vehicle"),
    ("贵金属", "precious_metal"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for root_name, type_code in _ROOT_TO_TYPE:
        rows = bind.execute(
            sa.text(
                "SELECT id FROM categories WHERE domain = 'asset' "
                "AND parent_id IS NULL AND name = :n AND deleted_at IS NULL"
            ),
            {"n": root_name},
        ).fetchall()
        for (root_id,) in rows:
            bind.execute(
                sa.text(
                    "UPDATE assets SET asset_type = :t "
                    "WHERE asset_type = 'other' AND deleted_at IS NULL AND category_id IN ("
                    "  SELECT id FROM categories WHERE id = :rid OR parent_id = :rid)"
                ),
                {"t": type_code, "rid": root_id},
            )


def downgrade() -> None:
    # 数据修正不可逆（无法区分哪些原本是 other），保持不动
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
