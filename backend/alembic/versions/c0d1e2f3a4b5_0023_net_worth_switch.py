"""0023 net worth switch for liabilities & investments

净资产口径统一：账户、资产、负债、投资都应能单独决定是否纳入家庭净值。
此前只有账户与资产有开关，负债与投资是"一律计入"。

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
"""

import sqlalchemy as sa
from alembic import op

revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("liabilities", "investment_accounts"):
        op.add_column(
            table,
            sa.Column("include_in_net_worth", sa.Boolean(), nullable=False,
                      server_default=sa.text("1")),
        )


def downgrade() -> None:
    for table in ("liabilities", "investment_accounts"):
        op.drop_column(table, "include_in_net_worth")
