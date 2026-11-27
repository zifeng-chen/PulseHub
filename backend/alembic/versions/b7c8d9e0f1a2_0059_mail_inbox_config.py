"""0059 邮件记账配置

信用卡消费邮件自动记账：IMAP 账号 + 加密授权码 + 每天拉取时段。
新增独立表，不动现有业务表。

Revision ID: b7c8d9e0f1a2
Revises: f2a3b4c5d6e7
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

TABLE = "mail_inbox_configs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.String(32), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="qq"),
        sa.Column("email", sa.String(128), nullable=True),
        sa.Column("auth_code_cipher", sa.Text(), nullable=True),
        sa.Column("imap_host", sa.String(128), nullable=False, server_default="imap.qq.com"),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("fetch_times", sa.String(64), nullable=False, server_default="09:00,14:00,21:00"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_fetch_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(32), nullable=False, server_default="unconfigured"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_uid", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
