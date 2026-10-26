"""房产租赁扩展模型（06 §9、15 §3.3、10 §1）。

结构（与 02 §2 关系图一致）：
    real_estates（不动产，房产主对象）
      └── leases（租约，关联具体不动产）
            ├── lease_receivables（租金应收）
            └── lease_deposits（押金）

口径：
- 应收登记不改变账户余额；实收必须关联到账账户并计入收入；
- 押金单独显示为待退还资金，收取/退还都不计入收入或支出；
- 逾期由应付日与当前日期派生，不建立后台任务；
- 房产页只显示出租摘要与关联入口，租金/押金/逾期统一由本模块管理（15 §3.3）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class Lease(Base, BaseColumns):
    """租约（06 §9：房产、租客、租期、月租、付款周期、起租/到期、押金、收款账户、提醒规则）。"""

    __tablename__ = "leases"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    lease_no: Mapped[str] = mapped_column(String(32), index=True)
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 关联分类（08 §3「租赁」分类域）
    real_estate_id: Mapped[str] = mapped_column(String(32), index=True)   # 关联不动产（资产主表 id）
    tenant_name: Mapped[str] = mapped_column(String(128))                  # 租客名称
    tenant_contact: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 租客联系方式（敏感）
    lease_purpose: Mapped[str | None] = mapped_column(String(64), nullable=True)    # 租赁用途
    monthly_rent: Mapped[float] = mapped_column(Numeric(20, 4), default=0)          # 月租
    payment_frequency: Mapped[str] = mapped_column(String(32), default="monthly")   # 付款周期
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 起租日
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)     # 合同到期日
    deposit_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)          # 押金金额
    receiving_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 收款账户
    property_repair_bearer: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 物业/维修承担方
    reminder_days: Mapped[int | None] = mapped_column(Integer, nullable=True)         # 到期提醒规则（提前 N 天）
    # 历史字段：曾经用来控制房产是否计入净资产。
    # 现在统一由资产自身（不动产编辑页的「计入净资产」）决定，本字段保留但不参与计算。
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    # 押金是否已收（事实标记，不强制走收款流程）；押金是否计入净资产单独一个开关
    deposit_received: Mapped[bool] = mapped_column(Boolean, default=False)
    deposit_include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=False)
    # 续租来源：这条租约是从哪份租约续下来的（便于串起租期链）
    previous_lease_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)      # draft/active/expired/terminated
    attachment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LeaseReceivable(Base, BaseColumns):
    """租金应收（06 §9：应收登记不改变账户余额）。"""

    __tablename__ = "lease_receivables"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    lease_id: Mapped[str] = mapped_column(String(32), index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 账期起
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)    # 账期止
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)      # 应付日
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/paid（overdue 派生）
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # rent_receivable 事件
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LeaseDeposit(Base, BaseColumns):
    """押金（06 §9：押金单独显示为待退还资金）。"""

    __tablename__ = "lease_deposits"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    lease_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[str] = mapped_column(String(16), default="held", index=True)  # held/returned
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


__all__ = ["Lease", "LeaseReceivable", "LeaseDeposit"]


class LeaseSettlement(Base, BaseColumns):
    """退租结算单：押金 − 水电煤欠费 − 家电家具破损赔偿 = 应退押金。"""

    __tablename__ = "lease_settlements"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    lease_id: Mapped[str] = mapped_column(String(32), index=True)
    settle_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deposit_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)   # 押金快照
    utility_total: Mapped[float] = mapped_column(Numeric(20, 4), default=0)   # 水电煤结算合计
    damage_total: Mapped[float] = mapped_column(Numeric(20, 4), default=0)    # 破损赔偿合计
    refund_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)   # 应退押金
    status: Mapped[str] = mapped_column(String(16), default="settled")        # settled
    refund_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LeaseSettlementItem(Base, BaseColumns):
    """结算明细：水电煤一项、破损赔偿一项。"""

    __tablename__ = "lease_settlement_items"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    settlement_id: Mapped[str] = mapped_column(String(32), index=True)
    item_type: Mapped[str] = mapped_column(String(16), default="damage")      # utility / damage
    label: Mapped[str] = mapped_column(String(128))
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
