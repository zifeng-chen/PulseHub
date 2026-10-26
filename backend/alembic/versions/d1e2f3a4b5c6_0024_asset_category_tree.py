"""0024 asset categories: parking becomes a real-estate subcategory

资产分类体系调整：
- 「不动产」下只保留「住宅」「车位」，移除「商铺」「写字楼」
- 独立的「车位」一级分类并入「不动产」作为二级分类
- 资产类型去掉 parking，历史 parking 资产归入 real_estate
- 资产分类补 applies_to，供「选类型 → 只显示该类型分类」联动

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
"""

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None

TYPE_BY_ROOT = {
    "不动产": "real_estate",
    "车辆": "vehicle",
    "贵金属": "precious_metal",
    "其他资产": "other",
}


def upgrade() -> None:
    bind = op.get_bind()

    def rows(sql: str, **params):
        return bind.execute(sa.text(sql), params).fetchall()

    # 1) 一级分类补 applies_to
    for name, key in TYPE_BY_ROOT.items():
        bind.execute(
            sa.text(
                "UPDATE categories SET applies_to = :k "
                "WHERE domain = 'asset' AND level = 1 AND name = :n "
                "AND (applies_to IS NULL OR applies_to = '')"
            ),
            {"k": key, "n": name},
        )

    # 2) 子分类继承父级的 applies_to
    children = rows(
        "SELECT c.id AS cid, p.applies_to AS applies FROM categories c "
        "JOIN categories p ON c.parent_id = p.id "
        "WHERE c.domain = 'asset' AND c.level = 2 AND p.applies_to IS NOT NULL"
    )
    for cid, applies in children:
        bind.execute(
            sa.text("UPDATE categories SET applies_to = :k WHERE id = :i"),
            {"k": applies, "i": cid},
        )

    # 3) 独立「车位」一级分类 → 挪到「不动产」下成为二级分类
    estate = rows(
        "SELECT id FROM categories WHERE domain = 'asset' AND level = 1 "
        "AND name = '不动产' AND deleted_at IS NULL LIMIT 1"
    )
    if estate:
        estate_id = estate[0][0]
        parking_roots = rows(
            "SELECT id FROM categories WHERE domain = 'asset' AND level = 1 "
            "AND name = '车位' AND deleted_at IS NULL"
        )
        for (parking_id,) in parking_roots:
            # 已有资产挂在这个分类上时保留分类本身，只改层级，避免出现孤儿引用
            bind.execute(
                sa.text(
                    "UPDATE categories SET parent_id = :p, level = 2, sort_order = 1, "
                    "applies_to = 'real_estate' WHERE id = :i"
                ),
                {"p": estate_id, "i": parking_id},
            )

    # 4) 移除「商铺」「写字楼」：仅在没有资产引用时软删，避免破坏历史数据
    for name in ("商铺", "写字楼"):
        bind.execute(
            sa.text(
                "UPDATE categories SET deleted_at = NOW() "
                "WHERE domain = 'asset' AND name = :n AND deleted_at IS NULL "
                "AND id NOT IN ("
                "  SELECT DISTINCT category_id FROM assets WHERE category_id IS NOT NULL"
                ")"
            ),
            {"n": name},
        )

    # 5) 历史 parking 资产保持原样：类型仍可用（车位专项字段与关联规则依赖它），
    #    只是前端新增入口改为「不动产 / 车位」分类。


def downgrade() -> None:
    # 分类合并不可逆（无法还原原来的层级与名称），保持不动
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
