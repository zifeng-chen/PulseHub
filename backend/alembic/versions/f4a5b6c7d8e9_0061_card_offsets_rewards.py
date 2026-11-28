"""0061：信用卡账单抵扣 + 积分余额快照

抵扣金 / 积分抵现都是「银行减免账单」，没有现金流入：
- account_card_bills.offset_amount 记录累计抵扣，未还 = 账单 − 已还 − 抵扣；
- card_bill_offsets 保留每一笔抵扣（类型、金额、积分数量、关联事件），可撤销可追溯；
- card_reward_snapshots 存邮件里的积分余额（账户级，只读参考，不参与账务）。

事件类型 card_offset 在代码枚举里，不需要改表。

Revision ID: f4a5b6c7d8e9
Revises: d2e3f4a5b6c7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_card_bills",
        sa.Column("offset_amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
    )

    op.create_table(
        "card_bill_offsets",
        sa.Column("household_id", sa.String(length=32), nullable=False),
        sa.Column("bill_id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("offset_type", sa.String(length=16), nullable=False, server_default="credit"),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("event_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
    op.create_index("ix_card_bill_offsets_household_id", "card_bill_offsets", ["household_id"])
    op.create_index("ix_card_bill_offsets_bill_id", "card_bill_offsets", ["bill_id"])
    op.create_index("ix_card_bill_offsets_account_id", "card_bill_offsets", ["account_id"])

    op.create_table(
        "card_reward_snapshots",
        sa.Column("household_id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="cmb"),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_limit", sa.Numeric(20, 4), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="email"),
        sa.Column("source_uid", sa.String(length=64), nullable=True),
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
    op.create_index("ix_card_reward_snapshots_household_id", "card_reward_snapshots", ["household_id"])
    op.create_index("ix_card_reward_snapshots_snapshot_date", "card_reward_snapshots", ["snapshot_date"])


def downgrade() -> None:
    op.drop_index("ix_card_reward_snapshots_snapshot_date", table_name="card_reward_snapshots")
    op.drop_index("ix_card_reward_snapshots_household_id", table_name="card_reward_snapshots")
    op.drop_table("card_reward_snapshots")

    op.drop_index("ix_card_bill_offsets_account_id", table_name="card_bill_offsets")
    op.drop_index("ix_card_bill_offsets_bill_id", table_name="card_bill_offsets")
    op.drop_index("ix_card_bill_offsets_household_id", table_name="card_bill_offsets")
    op.drop_table("card_bill_offsets")

    op.drop_column("account_card_bills", "offset_amount")
