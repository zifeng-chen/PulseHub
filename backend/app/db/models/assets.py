"""资产专项扩展表（15 §1：资产主表 + 类型扩展表 + 估值历史）。

M2 资产模块交付：估值历史、不动产、水电煤户号、车位、车辆、车险、贵金属。
房贷/车贷的余额、利息与本金由负债与信用卡模块统一管理（15 §3.2、15 §5.2），
本模块只保留 `has_mortgage` / `has_loan` 标记与关联入口，不重复建立第二套贷款数据。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class AssetValuation(Base, BaseColumns):
    """估值历史（15 §2、06 §2：新增估值、估值时间线）。"""

    __tablename__ = "asset_valuations"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    valued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    source_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class RealEstate(Base, BaseColumns):
    """不动产扩展（15 §3.1）。购买日期/总价、当前估值、持有人等共用字段在 assets 主表。"""

    __tablename__ = "real_estates"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    property_type: Mapped[str] = mapped_column(String(32), default="residence")
    title_no: Mapped[str | None] = mapped_column(String(64), nullable=True)        # 房产证编号（敏感）
    ownership_no: Mapped[str | None] = mapped_column(String(64), nullable=True)    # 不动产权证号（敏感）
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    building: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    floor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    room_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    building_area: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    inner_area: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    land_area: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    orientation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    layout: Mapped[str | None] = mapped_column(String(64), nullable=True)
    built_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renovation_cost: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    tax_cost: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    agency_fee: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    # 物业费单价（元/㎡·月）：用于估算每月物业费，与「房屋单价」不是一回事
    management_fee_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    # 参考单价（元/㎡）：按「建筑面积 × 参考单价」估算当前价值，是估值依据而非成交价
    unit_price: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    ownership_ratio: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    has_mortgage: Mapped[bool] = mapped_column(Boolean, default=False)  # 房贷明细由负债模块管理
    # M4 房产租赁摘要字段（15 §3.3：房产页只显示摘要与入口，租金/押金/逾期由租赁模块管理）
    has_lease: Mapped[bool] = mapped_column(Boolean, default=False)      # 是否出租
    current_lease_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 当前租约 ID
    vacancy_start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 空置开始日
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class UtilityAccount(Base, BaseColumns):
    """水电煤户号（15 §3.4：必须关联具体不动产，不能只挂在家庭或账户上）。"""

    __tablename__ = "utility_accounts"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    real_estate_id: Mapped[str] = mapped_column(String(32), index=True)
    utility_type: Mapped[str] = mapped_column(String(32), default="water")
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_no: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 户号（敏感）
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    billing_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing_cycle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    billing_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_bill_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bill_amount: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ParkingSpace(Base, BaseColumns):
    """车位扩展（15 §4）。一个车位最多关联一套不动产；一套不动产可有多个车位。"""

    __tablename__ = "parking_spaces"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    parking_type: Mapped[str] = mapped_column(String(32), default="ownership")
    certificate_no: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 产权证/使用权编号（敏感）
    parking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    area_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    area: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    management_fee: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    management_fee_cycle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    linked_real_estate_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Vehicle(Base, BaseColumns):
    """车辆扩展（15 §5.1）。VIN、车牌、发动机号按敏感字段掩码展示。"""

    __tablename__ = "vehicles"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    series: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vin: Mapped[str | None] = mapped_column(String(32), nullable=True)          # 敏感
    plate_no: Mapped[str | None] = mapped_column(String(32), nullable=True)     # 敏感
    engine_no: Mapped[str | None] = mapped_column(String(32), nullable=True)    # 敏感
    vehicle_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    energy_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    register_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mileage: Mapped[float | None] = mapped_column(Numeric(14, 1), nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    usage_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parking_space_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    has_loan: Mapped[bool] = mapped_column(Boolean, default=False)  # 车贷明细由负债模块管理
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class VehicleInsurance(Base, BaseColumns):
    """车辆保险（15 §5.3：一辆车可有多份保单，到期状态由提醒模块识别）。"""

    __tablename__ = "vehicle_insurances"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    insurance_type: Mapped[str] = mapped_column(String(32), default="compulsory")
    insurer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    insured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    premium: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    coverage: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    deductible: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    insured_person: Mapped[str | None] = mapped_column(String(64), nullable=True)
    beneficiary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    renewal_status: Mapped[str] = mapped_column(String(32), default="pending")
    attachment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remind_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PreciousMetal(Base, BaseColumns):
    """贵金属扩展（15 §6）。保管位置只作为自由文本字段，不建位置树。"""

    __tablename__ = "precious_metals"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    category: Mapped[str] = mapped_column(String(32), default="gold")
    form: Mapped[str | None] = mapped_column(String(32), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 品牌字典 code（metal_brands.BRANDS），用于购买渠道联动；brand 保留自由文本名称
    brand_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    certificate_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purchase_channel: Mapped[str | None] = mapped_column(String(128), nullable=True)
    purity: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    weight: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    unit: Mapped[str] = mapped_column(String(16), default="g")
    unit_price: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    processing_fee: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    # 计价方式：by_weight=克重计价（克价×克重+加工费）/ flat=一口价（按件标价）
    pricing_mode: Mapped[str] = mapped_column(String(16), default="by_weight")
    flat_price: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    tax_cost: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    current_unit_price: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    valuation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valuation_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    storage_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    batch_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssetReceivable(Base, BaseColumns):
    """借出款（应收）明细。

    借出不是支出：现金/账户减少的同时形成一笔应收资产，净资产不变。
    借款人身份证号属于敏感字段，默认掩码，只有审计查看权限可看完整值。
    """

    __tablename__ = "asset_receivables"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    borrower_name: Mapped[str] = mapped_column(String(64))
    # 敏感字段：落库为 Fernet 密文（长度约 120），序列化时才解密并掩码
    borrower_id_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lend_type: Mapped[str] = mapped_column(String(32), default="cash")
    # 登记方式：account=从账户出借（生成资金事件）/ existing=补录既有借出（只登记应收，不动账户）
    lent_mode: Mapped[str] = mapped_column(String(16), default="account")
    principal: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    lent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repayment_method: Mapped[str] = mapped_column(String(32), default="lump_sum")
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 利息约定：是否计息 + 年化利率（%）；已收利息由回款逐笔累计
    interest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interest_rate: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    interest_received: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    fee_bearer: Mapped[str] = mapped_column(String(16), default="borrower")
    fee_amount: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    source_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReceivablePeriod(Base, BaseColumns):
    """借出款分期计划（每期应还多少、哪个月还）。

    金额每期不同的分期，光看「总额 + 截止日」对不了账——用户需要知道
    「第 3 期该还 3,200，实际还了多少」。这里按用户自己的分期表逐期登记；
    回款不绑定具体期次，而是按顺序自动分摊（先冲最早未结清的期），
    这样一笔回款跨多期、多笔回款凑一期都能正确处理。
    """

    __tablename__ = "receivable_periods"
    __table_args__ = (
        Index("ix_receivable_periods_rec_no", "receivable_id", "period_no"),
    )

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    receivable_id: Mapped[str] = mapped_column(String(32), index=True)
    period_no: Mapped[int] = mapped_column(Integer, default=1)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    # 已分摊到这一期的本金（由回款自动累计，不由用户填）
    paid_amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReceivableRepayment(Base, BaseColumns):
    """借出款回款记录（支持部分回款）。

    每笔回款生成一个 receivable_repay 事件：资金账户增加、应收资产减少，
    不计入收入；全部收回后资产自动归档，净资产从「应收」变回「现金」。
    """

    __tablename__ = "receivable_repayments"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    receivable_id: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    # 一次回款里可分拆「本金」与「利息」：本金冲减应收，利息计入利息收入
    principal_part: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    interest_part: Mapped[float] = mapped_column(Numeric(20, 4), default=0)
    # 可选期次标注：金额不固定的分期由用户自己说「这是第几期」
    period_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 这笔回款当时是否真的进过账户：
    # False = 补录历史回款（钱早已收到并花掉），只冲减应收，不生成资金事件、不动当前账户
    affects_account: Mapped[bool] = mapped_column(Boolean, default=True)
    account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
