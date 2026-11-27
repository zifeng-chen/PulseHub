"""0060：删除贝壳开放平台配置（房产估值下线）。

贝壳接口已无可用授权次数，配置入口与功能一并下线；旧表保留一个版本周期的
建表语句在 downgrade 里，便于回滚时恢复结构（数据不回补）。

Revision ID: d2e3f4a5b6c7
Revises: b7c8d9e0f1a2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS beike_configs")


def downgrade() -> None:
    op.create_table(
        "beike_configs",
        sa.Column("household_id", sa.String(length=32), nullable=True),
        sa.Column("app_key_cipher", sa.Text(), nullable=True),
        sa.Column("app_secret_cipher", sa.Text(), nullable=True),
        sa.Column("base_url", sa.String(length=255), nullable=False,
                  server_default="https://gw-open.ke.com"),
        sa.Column("default_city", sa.String(length=32), nullable=True),
        sa.Column("default_city_code", sa.String(length=16), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_status", sa.String(length=32), nullable=False, server_default="unconfigured"),
        sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("updated_by", sa.String(length=32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_beike_configs_household_id", "beike_configs", ["household_id"])
