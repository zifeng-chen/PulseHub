"""负债与信用卡扩展模型（06 §6/§7、15 §3.2/§5.2、10 §1）。

结构（与 02 §2 关系图一致）：
    liabilities（统一负债主表，含房贷/车贷/普通负债/信用卡）
      ├── mortgages      房贷扩展（合同、利率调整周期、首期还款日、提前还款规则）
      ├── vehicle_loans  车贷扩展（合同、首期还款日）
      └── credit_cards   信用卡扩展（额度、账单日、还款日、卡号）
            └── credit_card_bills 账单

口径：
- 剩余本金 = 初始金额 + Σ(已确认负债资金变动 in) − Σ(out)，由确定性服务计算；
- 房贷必须关联具体不动产、车贷必须关联具体车辆（15 §3.2、15 §5.2）；
- 还款的本金、利息与费用由负债模块统一计算，资产页面不重复录入第二套贷款数据。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class Liability(Base, BaseColumns):
    """统一负债主表（06 §6：债务名称、类型、剩余本金、利率、下次还款日、月度应还、状态）。"""

    __tablename__ = "liabilities"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    liability_type: Mapped[str] = mapped_column(String(32), index=True, default="personal")
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 关联分类（08 §3「负债」分类域）
    creditor: Mapped[str | None] = mapped_column(String(128), nullable=True)   # 债权机构
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    initial_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)   # 贷款金额 / 合同金额
    # 建档时点的剩余本金：为空表示「尚未还过」，从初始金额起算；
    # 录入存量老贷款时填当前剩余，进步后的剩余本金从它开始累加（不再倒扣历史还款）
    opening_outstanding: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    interest_rate: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)  # 年利率（百分数）
    rate_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rate_adjustment_cycle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    repayment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)    # 贷款年限（月，合同总期限）
    # 剩余期数：存量贷款录当前还剩多少期；为空表示按合同总期限从建档日起算
    remaining_periods: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 是否纳入家庭净资产：负债是负项，关闭后该笔欠款不计入净值（如公司贷款）
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_frequency: Mapped[str] = mapped_column(String(32), default="monthly")  # 还款频率
    payment_day: Mapped[int | None] = mapped_column(Integer, nullable=True)    # 每月还款日
    monthly_payment: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)  # 每期应还
    first_payment_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_payment_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    linked_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LiabilityRepayment(Base, BaseColumns):
    """还款记录（06 §6：登记还款输入总额和账户，系统按本金/利息/费用拆分，用户可调整但需说明原因）。"""

    __tablename__ = "liability_repayments"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    liability_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)      # 还款总额
    principal: Mapped[float] = mapped_column(Numeric(20, 4), default=0)   # 本金
    interest: Mapped[float] = mapped_column(Numeric(20, 4), default=0)    # 利息
    fee: Mapped[float] = mapped_column(Numeric(20, 4), default=0)         # 费用
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bill_id: Mapped[str | None] = mapped_column(String(32), nullable=True)      # 信用卡账单
    principal_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interest_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)   # 调整拆分原因
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Mortgage(Base, BaseColumns):
    """房贷扩展（15 §3.2）。其余字段由 liabilities 主表与关联不动产承载，不重复建第二套数据。"""

    __tablename__ = "mortgages"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    liability_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    contract_no: Mapped[str | None] = mapped_column(String(64), nullable=True)         # 贷款合同编号
    # 贷款方式：commercial 商业 / provident_fund 公积金 / combined 组合
    loan_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rate_adjustment_cycle: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 利率调整周期
    prepayment_rules: Mapped[str | None] = mapped_column(Text, nullable=True)          # 提前还款规则
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class VehicleLoan(Base, BaseColumns):
    """车贷扩展（15 §5.2）。"""

    __tablename__ = "vehicle_loans"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    liability_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    contract_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 贷款方式：commercial 商业 / provident_fund 公积金 / combined 组合 / other 其他
    loan_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CreditCard(Base, BaseColumns):
    """信用卡（06 §7：额度、已用额度、可用额度、账单日、还款日）。"""

    __tablename__ = "credit_cards"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    liability_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    issuer: Mapped[str | None] = mapped_column(String(128), nullable=True)       # 发卡机构
    card_no: Mapped[str | None] = mapped_column(String(32), nullable=True)       # 卡号（敏感，掩码）
    credit_limit: Mapped[float] = mapped_column(Numeric(20, 4), default=0)       # 额度
    bill_day: Mapped[int | None] = mapped_column(Integer, nullable=True)         # 账单日
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)          # 还款日
    linked_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 默认还款账户
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CreditCardBill(Base, BaseColumns):
    """信用卡账单（06 §7：区分消费明细、账单形成、还款和费用）。"""

    __tablename__ = "credit_card_bills"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    card_id: Mapped[str] = mapped_column(String(32), index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bill_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)        # 本期账单
    minimum_payment: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)  # 最低还款
    paid_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)         # 已还金额
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class InvestmentAccount(Base, BaseColumns):
    """投资账户（06 §8：账户余额、持仓市值、成本、损益）。"""

    __tablename__ = "investment_accounts"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    account_type: Mapped[str] = mapped_column(String(32), default="securities")
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 关联分类（08 §3「投资」分类域）
    institution: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    opening_cash: Mapped[float] = mapped_column(Numeric(20, 4), default=0)   # 账户内初始可用资金
    linked_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 关联资金账户
    # 是否纳入家庭净资产：关闭后该账户市值不计入净值（如代持/测试账户）
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class InvestmentTrade(Base, BaseColumns):
    """投资交易（06 §8：买入、卖出、分红、费用、数量、单价、日期、账户）。"""

    __tablename__ = "investment_trades"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    trade_type: Mapped[str] = mapped_column(String(16), default="buy")
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)   # 标的代码
    symbol_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)    # 成交金额（不含费用）
    fee: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    traded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class InvestmentValuation(Base, BaseColumns):
    """投资估值（06 §8：估值可以手工录入；显示估值时间和数据来源）。"""

    __tablename__ = "investment_valuations"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    symbol_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    price: Mapped[float] = mapped_column(Numeric(20, 6), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    valued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "Liability", "LiabilityRepayment", "Mortgage", "VehicleLoan",
    "CreditCard", "CreditCardBill",
    "InvestmentAccount", "InvestmentTrade", "InvestmentValuation",
]
