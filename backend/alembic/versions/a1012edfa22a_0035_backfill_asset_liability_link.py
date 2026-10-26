"""0035：回填「负债 → 资产」的反向关联。

房贷/车贷只写了负债侧的 linked_asset_id，资产侧的 linked_liability_id 一直为空，
资产详情因此显示「关联负债：未关联」，可同一页又列出了贷款摘要。
这里按负债侧数据回填，并把资产的「是否有房贷/车贷」标记同步为是。

Revision ID: a1012edfa22a
Revises: 6ee1e70224b1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1012edfa22a"
down_revision = "6ee1e70224b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # 1) 资产侧反向引用：取该资产下最早建立的一笔房贷/车贷
    # 不用表别名：SQLite 不支持 UPDATE ... AS a SET a.col，MySQL 又两种都行
    conn.execute(sa.text(
        "UPDATE assets SET linked_liability_id = ("
        "  SELECT l.id FROM liabilities l "
        "  WHERE l.linked_asset_id = assets.id AND l.deleted_at IS NULL "
        "    AND l.liability_type IN ('mortgage', 'vehicle_loan') "
        "  ORDER BY l.created_at ASC LIMIT 1"
        ") "
        "WHERE deleted_at IS NULL "
        "AND (linked_liability_id IS NULL OR linked_liability_id = '') "
        "AND EXISTS ("
        "  SELECT 1 FROM liabilities l2 "
        "  WHERE l2.linked_asset_id = assets.id AND l2.deleted_at IS NULL "
        "    AND l2.liability_type IN ('mortgage', 'vehicle_loan')"
        ")"
    ))
    # 2) 资产上的「是否有房贷 / 是否有车贷」标记
    conn.execute(sa.text(
        "UPDATE real_estates SET has_mortgage = 1 "
        "WHERE deleted_at IS NULL AND (has_mortgage = 0 OR has_mortgage IS NULL) "
        "AND EXISTS (SELECT 1 FROM liabilities l WHERE l.linked_asset_id = real_estates.asset_id "
        "            AND l.deleted_at IS NULL AND l.liability_type = 'mortgage')"
    ))
    conn.execute(sa.text(
        "UPDATE vehicles SET has_loan = 1 "
        "WHERE deleted_at IS NULL AND (has_loan = 0 OR has_loan IS NULL) "
        "AND EXISTS (SELECT 1 FROM liabilities l WHERE l.linked_asset_id = vehicles.asset_id "
        "            AND l.deleted_at IS NULL AND l.liability_type = 'vehicle_loan')"
    ))


def downgrade() -> None:
    # 数据回填不做反向操作：清掉会把用户后续手工建立的关系一起抹掉
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
