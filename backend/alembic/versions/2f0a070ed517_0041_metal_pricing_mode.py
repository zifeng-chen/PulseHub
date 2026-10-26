"""0041：贵金属增加计价方式与品牌字典字段。

- pricing_mode：by_weight（克重计价）/ flat（一口价），替代原先与分类重复的「品类」
- flat_price：一口价金额
- brand_code：品牌字典 code，用于购买渠道联动

Revision ID: 2f0a070ed517
Revises: 6b1a8c49f0b9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2f0a070ed517"
down_revision = "6b1a8c49f0b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("precious_metals", sa.Column("brand_code", sa.String(length=32), nullable=True))
    op.add_column("precious_metals", sa.Column("pricing_mode", sa.String(length=16),
                                               nullable=False, server_default="by_weight"))
    op.add_column("precious_metals", sa.Column("flat_price", sa.Numeric(20, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("precious_metals", "flat_price")
    op.drop_column("precious_metals", "pricing_mode")
    op.drop_column("precious_metals", "brand_code")
