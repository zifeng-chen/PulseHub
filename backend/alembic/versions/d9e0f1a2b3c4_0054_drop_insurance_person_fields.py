"""0054 车辆保险移除「被保险人 / 受益人」

车险属于财产险，赔付对象是车辆损失本身，不存在人身险意义上的
「被保险人 / 受益人」。这两列是照着人身险模板带过来的，业务上永远不会填，
留着只会让表单多出两个无用输入框、并误导录入者。

上线前已确认 vehicle_insurances 无任何行使用过这两列，直接删除。

Revision ID: d9e0f1a2b3c4
Revises: b6d7e8f9a0c1
Create Date: 2026-11-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d9e0f1a2b3c4"
down_revision = "b6d7e8f9a0c1"
branch_labels = None
depends_on = None

TABLE = "vehicle_insurances"


def upgrade() -> None:
    op.drop_column(TABLE, "insured_person")
    op.drop_column(TABLE, "beneficiary")


def downgrade() -> None:
    op.add_column(TABLE, sa.Column("insured_person", sa.String(64), nullable=True))
    op.add_column(TABLE, sa.Column("beneficiary", sa.String(64), nullable=True))
