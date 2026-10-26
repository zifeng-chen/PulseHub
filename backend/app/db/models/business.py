"""业务域基座模型（10 §1：业务组的基座部分）。

M0 只建立可复用的领域基座表：账户、资产主表、业务事件、资金变动、附件。
专项扩展表（不动产/车位/车辆/贵金属/负债/投资/租赁等）在其所属模块交付时迁移。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class CurrencyFields:
    """多币种底层预留（22 §4.1）：原始金额、原始币种、换算金额、基础币种、汇率、汇率日期、来源、换算状态。"""

    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    original_amount: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    converted_amount: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    base_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    exchange_rate: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    rate_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rate_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversion_status: Mapped[str] = mapped_column(String(16), default="same_currency")


class Account(Base, BaseColumns, CurrencyFields):
    __tablename__ = "accounts"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    account_type: Mapped[str] = mapped_column(String(32), default="bank")
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 关联分类（08 §3「账户」分类域）
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)   # 卡号后四位（只存后四位，不留存完整卡号）
    holder_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 开户人 / 持有人（虚拟账户用）
    institution: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 信用类账户（信用卡 / 花呗 / 借呗 / 白条等）专属字段（06 §7）
    credit_limit: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)   # 信用/授信额度
    credit_used: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)    # 当前已用额度
    card_expiry: Mapped[str | None] = mapped_column(String(7), nullable=True)           # 卡片有效期 YYYY-MM
    bill_day: Mapped[int | None] = mapped_column(Integer, nullable=True)                # 账单日（1-28）
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)                 # 还款日（1-28）
    # 储蓄 / 借款类专属字段：年利率（%）、存期（月）、到期日
    interest_rate: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)   # 年利率，如 1.5000 表示 1.5%
    term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)             # 存期（月）
    maturity_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 到期日
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    opening_balance: Mapped[float | None] = mapped_column(Numeric(20, 4), default=0)
    opening_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Asset(Base, BaseColumns, CurrencyFields):
    """资产主表（15 §2：所有类型共有字段）。类型扩展表由后续模块迁移。"""

    __tablename__ = "assets"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    asset_type: Mapped[str] = mapped_column(String(32), index=True)  # real_estate/parking/vehicle/precious_metal/other
    sub_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 关联分类（08 §3「资产」分类域）
    owner_member_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    acquire_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquire_cost: Mapped[float | None] = mapped_column(Numeric(20, 4), default=0)
    current_value: Mapped[float | None] = mapped_column(Numeric(20, 4), default=0)
    value_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_valuation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    linked_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    linked_liability_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # M2 资产模块新增（06 §2 资产表单：折旧方式、维护日期）
    depreciation_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    maintenance_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    custom_attributes: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class BusinessEvent(Base, BaseColumns, CurrencyFields):
    """统一业务事件（23 §4）。确认前为草稿，不进入任何统计。"""

    __tablename__ = "business_events"
    # 列表与统计固定按「家庭 + 状态 + 发生时间区间」过滤并排序，单列索引会退化成回表 + filesort
    __table_args__ = (
        Index("ix_events_household_status_occurred", "household_id", "status", "occurred_at"),
    )

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    event_no: Mapped[str] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), default=0)
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    liability_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    investment_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual/ocr/ai/api
    attachment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    realized_gain: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)  # 资产处置已实现损益（15 §6.2）
    # 退款事件对应的原始支出事件：用于校验可退金额，并让"退款"能追溯到原消费
    refund_of: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class EventMovement(Base, BaseColumns):
    """资金变动（02 §2：事件 -> 资金变动 -> 账户/资产/负债/投资/房产）。"""

    __tablename__ = "event_movements"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    event_id: Mapped[str] = mapped_column(String(32), index=True)
    target_type: Mapped[str] = mapped_column(String(32))  # account/asset/liability/investment/property
    target_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(8), default="out")  # in/out
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Attachment(Base, BaseColumns):
    """附件（24 §4：绑定家庭与业务对象，访问重新校验权限）。"""

    __tablename__ = "attachments"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    object_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    object_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    material_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # M5 智能录入材料类型
    file_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    source_method: Mapped[str] = mapped_column(String(16), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TransferGroup(Base, BaseColumns, CurrencyFields):
    """转账组（06 §4、18 §4）。

    一笔内部转账由两条业务事件承载（转出 / 转入），本表把它们与手续费绑定为一个组，
    保证：转出账户与转入账户不能相同；内部转账只改变账户余额，不改变收入与支出统计。
    """

    __tablename__ = "transfer_groups"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    group_no: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    from_account_id: Mapped[str] = mapped_column(String(32), index=True)
    to_account_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    fee: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    out_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    in_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    fee_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class RecurringExpense(Base, BaseColumns):
    """周期固定支出（如话费月租、订阅、物业费）。

    金额固定、按周期重复，到期自动生成一笔已确认的支出事件，避免每月手记。
    """

    __tablename__ = "recurring_expenses"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    frequency: Mapped[str] = mapped_column(String(16), default="monthly")  # monthly/weekly/yearly
    # 每月几号扣款（monthly）；weekly 用周几（0=周一）；yearly 用月日（MM-DD）
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/paused/stopped
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class EmploymentProfile(Base, BaseColumns):
    """工作档案（每个家庭成员一份在职工作）。

    只记录「与钱相关」的工作维度，不做职业/简历管理：
    - 雇主、在职状态（用于 AI 现金流风险预警：失业/断档会触发韧性告警）
    - 社保公积金基数（作为隐性资产提示「真实薪酬包」，不落金额计算）
    工资/奖金/期权等现金流由 RecurringIncome 与投资模块表达，不在此表。
    """

    __tablename__ = "employment_profiles"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    member_id: Mapped[str] = mapped_column(String(32), index=True)  # HouseholdMember.id
    employer: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 雇主名称（脱敏展示）
    employment_status: Mapped[str] = mapped_column(String(16), default="employed")
    # employed / probation / unemployed / retired / freelance
    social_insurance_base: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)  # 社保基数（元/月）
    housing_fund_base: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)  # 公积金基数（元/月）
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class RecurringIncome(Base, BaseColumns):
    """周期固定收入（工资、养老金、房租固定收入等，金额固定、按周期重复）。

    对称 RecurringExpense：到期自动生成一笔已确认的**收入**事件，避免每月手记。
    关联成员（member_id），多成员家庭可区分各自收入来源。
    """

    __tablename__ = "recurring_incomes"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    frequency: Mapped[str] = mapped_column(String(16), default="monthly")  # monthly/weekly/yearly
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 每月几号发薪
    next_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/paused/stopped
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
