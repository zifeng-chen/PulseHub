"""0027：贝壳开放平台配置（房产估值）。

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_beike_configs_household_id", table_name="beike_configs")
    op.drop_table("beike_configs")
