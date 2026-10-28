"""0053 账户型信用卡账单

信用卡有两种建模：负债型（Liability + CreditCard + credit_card_bills）
与账户型（Account + credit_limit）。账户型没有卡实体，账单单独落表，
用于回答「本期该还多少、几号到期」，还款时冲减 paid_amount。

Revision ID: b6d7e8f9a0c1
Revises: a5c6d7e8f9b0
Create Date: 2026-10-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6d7e8f9a0c1"
down_revision = "a5c6d7e8f9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_card_bills",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.String(32), nullable=False, index=True),
        sa.Column("account_id", sa.String(32), nullable=False, index=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bill_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("minimum_payment", sa.Numeric(20, 4), nullable=True),
        sa.Column("paid_amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 同一张卡同一账单周期只允许一张账单：重复生成要挡住，而不是覆盖
    op.create_index(
        "ux_account_card_bills_period",
        "account_card_bills",
        ["account_id", "period_end"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_account_card_bills_period", table_name="account_card_bills")
    op.drop_table("account_card_bills")
