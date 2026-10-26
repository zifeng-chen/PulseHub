"""0043：借出款支持「补录既有借出」与「利息约定」。

需求背景
--------
1. 之前借出去的钱不该再从现在的账户扣一次——需要区分「从账户出借」与「补录既有借出」，
   后者只登记应收，不生成资金事件（沿用 assets.acquisition.mode = none）。
2. 借出时可能约定利息（分期 / 一次还清 + 年化利率），回款时本金与利息要分开记：
   本金冲减应收（不计收入），利息计入利息收入。
3. 借款人身份证号改为必填（建借出款时校验，历史数据不回填、不阻断更新）。

字段
----
- asset_receivables.lent_mode        account / existing，默认 account（历史数据都是走账户的）
- asset_receivables.interest_enabled 是否约定利息，默认 0
- asset_receivables.interest_rate    年化利率（%），可空
- asset_receivables.interest_received 已收利息累计，默认 0
- receivable_repayments.principal_part / interest_part  单笔回款的本金/利息拆分
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8d4f2c17e05"
down_revision = "7c3e5a91b4d8"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # 历史数据都是「从账户出借」录的（当时强制走账户），补录功能上线前不存在 existing
    if not _has_column(bind, "asset_receivables", "lent_mode"):
        op.add_column("asset_receivables", sa.Column(
            "lent_mode", sa.String(16), nullable=False, server_default="account"))
    if not _has_column(bind, "asset_receivables", "interest_enabled"):
        op.add_column("asset_receivables", sa.Column(
            "interest_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if not _has_column(bind, "asset_receivables", "interest_rate"):
        op.add_column("asset_receivables", sa.Column(
            "interest_rate", sa.Numeric(9, 4), nullable=True))
    if not _has_column(bind, "asset_receivables", "interest_received"):
        op.add_column("asset_receivables", sa.Column(
            "interest_received", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")))

    # 历史回款没有拆分：全部视作本金
    if not _has_column(bind, "receivable_repayments", "principal_part"):
        op.add_column("receivable_repayments", sa.Column(
            "principal_part", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")))
        # 用 amount 回填，避免「本金分项为 0、金额却不为 0」的错账
        bind.execute(sa.text(
            "UPDATE receivable_repayments SET principal_part = amount WHERE principal_part = 0"))
    if not _has_column(bind, "receivable_repayments", "interest_part"):
        op.add_column("receivable_repayments", sa.Column(
            "interest_part", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    """只回退结构（数据无法还原：本金/利息拆分后不能可靠合并回去）。"""
    for column in ("interest_part", "principal_part"):
        if _has_column(op.get_bind(), "receivable_repayments", column):
            op.drop_column("receivable_repayments", column)
    for column in ("interest_received", "interest_rate", "interest_enabled", "lent_mode"):
        if _has_column(op.get_bind(), "asset_receivables", column):
            op.drop_column("asset_receivables", column)
