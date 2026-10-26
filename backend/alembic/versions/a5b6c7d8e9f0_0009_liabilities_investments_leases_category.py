"""0009 liabilities/investments/leases category

Revision ID: a5b6c7d8e9f0
Revises: e2f3a4b5c6d7
Create Date: 2026-10-16 09:15:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'a5b6c7d8e9f0'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 负债 / 投资 / 租赁接入分类体系（08 §3：分类域含负债、投资、租赁）。
    op.add_column('liabilities', sa.Column('category_id', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_liabilities_category_id'), 'liabilities', ['category_id'], unique=False)
    op.add_column('investment_accounts', sa.Column('category_id', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_investment_accounts_category_id'), 'investment_accounts', ['category_id'], unique=False)
    op.add_column('leases', sa.Column('category_id', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_leases_category_id'), 'leases', ['category_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_leases_category_id'), table_name='leases')
    op.drop_column('leases', 'category_id')
    op.drop_index(op.f('ix_investment_accounts_category_id'), table_name='investment_accounts')
    op.drop_column('investment_accounts', 'category_id')
    op.drop_index(op.f('ix_liabilities_category_id'), table_name='liabilities')
    op.drop_column('liabilities', 'category_id')
