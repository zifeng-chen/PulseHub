"""0021 add refund_of

退款需要能追溯到原始支出，用于校验可退金额与展示来源。

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""

import sqlalchemy as sa
from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("business_events", sa.Column("refund_of", sa.String(32), nullable=True))
    op.create_index("ix_business_events_refund_of", "business_events", ["refund_of"])


def downgrade() -> None:
    op.drop_index("ix_business_events_refund_of", table_name="business_events")
    op.drop_column("business_events", "refund_of")
