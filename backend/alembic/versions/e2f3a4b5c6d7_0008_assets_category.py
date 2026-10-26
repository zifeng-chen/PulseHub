"""0008 assets category

Revision ID: e2f3a4b5c6d7
Revises: 66064fb50aab
Create Date: 2026-10-16 08:30:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e2f3a4b5c6d7'
down_revision = '66064fb50aab'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 资产接入分类体系（08 §3：分类域含「资产」；15 §2 资产子类型补充分类）。
    op.add_column('assets', sa.Column('category_id', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_assets_category_id'), 'assets', ['category_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_assets_category_id'), table_name='assets')
    op.drop_column('assets', 'category_id')
