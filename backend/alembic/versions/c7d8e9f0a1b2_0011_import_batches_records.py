"""0011 import batches & records

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-16 12:50:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c7d8e9f0a1b2'
down_revision = 'b6c7d8e9f0a1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 人工审核式数据导入（24 §7：上传→校验→映射→预览→重复检查→草稿→批量确认）。
    op.create_table('import_batches',
        sa.Column('household_id', sa.String(length=32), nullable=True),
        sa.Column('source_file_name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('field_mapping', sa.Text(), nullable=True),
        sa.Column('total_rows', sa.Integer(), nullable=False),
        sa.Column('success_rows', sa.Integer(), nullable=False),
        sa.Column('error_rows', sa.Integer(), nullable=False),
        sa.Column('duplicate_count', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('updated_by', sa.String(length=32), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_import_batches_household_id'), 'import_batches', ['household_id'], unique=False)

    op.create_table('import_records',
        sa.Column('household_id', sa.String(length=32), nullable=True),
        sa.Column('batch_id', sa.String(length=32), nullable=False),
        sa.Column('row_no', sa.Integer(), nullable=False),
        sa.Column('raw_data', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('duplicate_of', sa.Integer(), nullable=True),
        sa.Column('event_id', sa.String(length=32), nullable=True),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('updated_by', sa.String(length=32), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_import_records_batch_id'), 'import_records', ['batch_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_import_records_batch_id'), table_name='import_records')
    op.drop_table('import_records')
    op.drop_index(op.f('ix_import_batches_household_id'), table_name='import_batches')
    op.drop_table('import_batches')
