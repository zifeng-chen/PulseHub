"""0010 business_events realized_gain

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-10-16 12:20:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b6c7d8e9f0a1'
down_revision = 'a5b6c7d8e9f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 资产处置的已实现损益（15 §6.2：差额由确定性服务计算已实现损益）。
    op.add_column('business_events', sa.Column('realized_gain', sa.Numeric(20, 4), nullable=True))


def downgrade() -> None:
    op.drop_column('business_events', 'realized_gain')
