"""0055 AI 会话上下文改为 TEXT

`ai_conversations.context` 原本是 VARCHAR(255)，只够存一句页面名。
2026-11-04「接着问 AI 助手」开始携带整段分析解读（结论/关注/建议/关键指标），
写入时直接 `Data too long for column 'context'`（1406），整条消息 500 失败。

改为 TEXT；同时服务层把上下文截到 4000 字，避免有人把整个快照塞进来。

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-11-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None

TABLE = "ai_conversations"
COLUMN = "context"


def upgrade() -> None:
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # 回退会截断超长上下文，仅用于紧急回滚
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
