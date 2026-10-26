"""0038：新增「周期固定支出」表。

话费月租、订阅、物业费等金额固定、每月/每周重复的支出，登记一次后到期自动
生成支出事件，避免每月手记。

Revision ID: 49ce87e41b91
Revises: 76c1e31af746
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "49ce87e41b91"
down_revision = "76c1e31af746"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recurring_expenses",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("updated_by", sa.String(length=32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("household_id", sa.String(length=32), index=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category_id", sa.String(length=32), nullable=True),
        sa.Column("account_id", sa.String(length=32), nullable=True),
        sa.Column("member_id", sa.String(length=32), nullable=True),
        sa.Column("currency", sa.String(length=8), default="CNY"),
        sa.Column("amount", sa.Numeric(20, 4), default=0),
        sa.Column("frequency", sa.String(length=16), default="monthly"),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("next_due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), default="active"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), default=1),
    )


def downgrade() -> None:
    op.drop_table("recurring_expenses")
