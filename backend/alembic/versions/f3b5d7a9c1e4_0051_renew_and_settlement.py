"""0051 续租链 + 退租结算单

Revision ID: f3b5d7a9c1e4
Revises: e9f1b3c5d7a2
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3b5d7a9c1e4"
down_revision = "e9f1b3c5d7a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leases", sa.Column("previous_lease_id", sa.String(32), nullable=True))
    op.create_index("ix_leases_previous_lease_id", "leases", ["previous_lease_id"])

    op.create_table(
        "lease_settlements",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.String(32), nullable=False, index=True),
        sa.Column("lease_id", sa.String(32), nullable=False, index=True),
        sa.Column("settle_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deposit_amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("utility_total", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("damage_total", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("refund_amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="settled"),
        sa.Column("refund_account_id", sa.String(32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "lease_settlement_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.String(32), nullable=False, index=True),
        sa.Column("settlement_id", sa.String(32), nullable=False, index=True),
        sa.Column("item_type", sa.String(16), nullable=False, server_default="damage"),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("lease_settlement_items")
    op.drop_table("lease_settlements")
    op.drop_index("ix_leases_previous_lease_id", table_name="leases")
    op.drop_column("leases", "previous_lease_id")
