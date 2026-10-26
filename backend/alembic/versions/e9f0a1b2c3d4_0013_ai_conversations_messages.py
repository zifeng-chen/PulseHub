"""0013 ai conversations and messages

AI 对话会话与消息（09 §3：新建对话 / 历史对话）。

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-16 15:10:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = 'e9f0a1b2c3d4'
down_revision = 'd8e9f0a1b2c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ai_conversations',
        sa.Column('household_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=False),
        sa.Column('context', sa.String(length=255), nullable=True),
        sa.Column('message_count', sa.Integer(), nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('updated_by', sa.String(length=32), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_conversations_household_id'), 'ai_conversations', ['household_id'], unique=False)
    op.create_index(op.f('ix_ai_conversations_user_id'), 'ai_conversations', ['user_id'], unique=False)

    op.create_table('ai_messages',
        sa.Column('household_id', sa.String(length=32), nullable=False),
        sa.Column('conversation_id', sa.String(length=32), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=True),
        sa.Column('model_type', sa.String(length=32), nullable=True),
        sa.Column('sources', sa.Text(), nullable=True),
        sa.Column('trust', sa.Text(), nullable=True),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('updated_by', sa.String(length=32), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_messages_conversation_id'), 'ai_messages', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_ai_messages_household_id'), 'ai_messages', ['household_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ai_messages_household_id'), table_name='ai_messages')
    op.drop_index(op.f('ix_ai_messages_conversation_id'), table_name='ai_messages')
    op.drop_table('ai_messages')
    op.drop_index(op.f('ix_ai_conversations_user_id'), table_name='ai_conversations')
    op.drop_index(op.f('ix_ai_conversations_household_id'), table_name='ai_conversations')
    op.drop_table('ai_conversations')
