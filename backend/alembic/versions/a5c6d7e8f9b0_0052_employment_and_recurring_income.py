"""0052 工作档案 + 周期固定收入

Revision ID: a5c6d7e8f9b0
Revises: f3b5d7a9c1e4
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a5c6d7e8f9b0"
down_revision = "f3b5d7a9c1e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employment_profiles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.String(32), nullable=False, index=True),
        sa.Column("member_id", sa.String(32), nullable=False, index=True),
        sa.Column("employer", sa.String(128), nullable=True),
        sa.Column("employment_status", sa.String(16), nullable=False, server_default="employed"),
        sa.Column("social_insurance_base", sa.Numeric(20, 4), nullable=True),
        sa.Column("housing_fund_base", sa.Numeric(20, 4), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "recurring_incomes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.String(32), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("category_id", sa.String(32), nullable=True, index=True),
        sa.Column("account_id", sa.String(32), nullable=True, index=True),
        sa.Column("member_id", sa.String(32), nullable=True, index=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("frequency", sa.String(16), nullable=False, server_default="monthly"),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("next_due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("recurring_incomes")
    op.drop_table("employment_profiles")
