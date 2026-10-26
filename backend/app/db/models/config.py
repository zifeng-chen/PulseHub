"""配置域模型（10 §1：配置组 + 能力提供商配置）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class HouseholdSetting(Base, BaseColumns):
    """财务口径与家庭级设置（04 §3 步骤四、08 §4）。"""

    __tablename__ = "household_settings"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    base_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    default_period: Mapped[str] = mapped_column(String(32), default="current_month")
    asset_purchase_separate: Mapped[bool] = mapped_column(Boolean, default=True)
    investment_valuation_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    net_worth_asset_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    bill_display_mode: Mapped[str] = mapped_column(String(32), default="day_of_month")
    default_date_range: Mapped[str] = mapped_column(String(32), default="last_6_months")
    amount_precision: Mapped[int] = mapped_column(Integer, default=2)


class Category(Base, BaseColumns):
    """分类（08 §3：六个分类域 + 父子 + AI 可推荐）。"""

    __tablename__ = "categories"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)  # income/expense/asset/liability/investment/lease
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_recommendable: Mapped[bool] = mapped_column(Boolean, default=True)
    applicable_event_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 适用范围：按分类域解释。账户域存「适用的账户类型」code（逗号分隔，如 "bank,e_wallet"）；
    # 为空表示通用（所有类型都可见）。
    applies_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 字段档案：账户域用于决定表单显示哪些专属字段
    # credit_card（信用卡：额度/有效期/账单日/还款日/卡号）、credit_line（消费信贷：额度/账单日/还款日）、
    # loan_line（借呗类借款：额度/利率/还款日）、savings_current（活期：利率）、
    # savings_fixed（定期：利率/存期/到期日）、debit_card（借记卡：卡号）、
    # wallet（电子钱包：开户人）、cash（现金）、空=按账户类型兜底。
    attribute_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)


class UserPreference(Base, BaseColumns):
    """个人偏好（03 §2.1 主题选择写入个人偏好）。"""

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String(32), index=True)
    theme: Mapped[str] = mapped_column(String(16), default="system")  # light/dark/system
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    reduced_motion: Mapped[bool] = mapped_column(Boolean, default=False)
    density: Mapped[str] = mapped_column(String(16), default="comfortable")


class ExchangeRate(Base, BaseColumns):
    """汇率（01 §4、22 §4：没有有效汇率不得默认 1）。"""

    __tablename__ = "exchange_rates"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    base_currency: Mapped[str] = mapped_column(String(8))
    quote_currency: Mapped[str] = mapped_column(String(8))
    rate: Mapped[float] = mapped_column(Numeric(20, 8))
    rate_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class SchemaVersion(Base, BaseColumns):
    __tablename__ = "schema_versions"

    version: Mapped[str] = mapped_column(String(64), index=True)
    app_version: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeploymentConfig(Base, BaseColumns):
    """部署配置（首次启动写入；口令只存密文）。"""

    __tablename__ = "deployment_configs"

    db_type: Mapped[str] = mapped_column(String(16), default="mysql")
    host: Mapped[str | None] = mapped_column(String(128), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    connect_timeout: Mapped[int] = mapped_column(Integer, default=10)
    initialized: Mapped[bool] = mapped_column(Boolean, default=False)


class MaintenanceWindow(Base, BaseColumns):
    """维护模式（24 §9）。"""

    __tablename__ = "maintenance_windows"

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/closed
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SecretKeyVersion(Base, BaseColumns):
    __tablename__ = "secret_key_versions"

    version: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class LocalAIConfig(Base, BaseColumns):
    """本地 AI 配置（08 §7、09）。"""

    __tablename__ = "local_ai_configs"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    protocol: Mapped[str] = mapped_column(String(32), default="openai_compatible")
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_key_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, default=60)
    context_length: Mapped[int] = mapped_column(Integer, default=8192)
    stream: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_status: Mapped[str] = mapped_column(String(32), default="unconfigured")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ThirdPartyAIProvider(Base, BaseColumns):
    """第三方 AI API（08 §8、14 §5：默认全部关闭，独立于本地 AI）。"""

    __tablename__ = "third_party_ai_providers"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    interface_type: Mapped[str] = mapped_column(String(64), default="openai_compatible")
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    org_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, default=60)
    max_input_length: Mapped[int] = mapped_column(Integer, default=8000)
    max_output_length: Mapped[int] = mapped_column(Integer, default=2000)
    stream: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_attachment: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    last_status: Mapped[str] = mapped_column(String(32), default="unconfigured")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class OcrConfig(Base, BaseColumns):
    """百度 OCR 配置（08 §6）。密钥只存密文。"""

    __tablename__ = "ocr_configs"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="baidu")
    api_key_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_key_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_status: Mapped[str] = mapped_column(String(32), default="unconfigured")
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationChannel(Base, BaseColumns):
    """通知渠道（08 §5、08 §12：PushPlus，默认关闭）。"""

    __tablename__ = "notification_channels"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    channel_type: Mapped[str] = mapped_column(String(32), default="pushplus")
    token_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    type_switches: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="unconfigured")
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationRecord(Base, BaseColumns):
    """通知发送记录（08 §12：同步发送，结果立即写入，失败可重试，非后台任务）。"""

    __tablename__ = "notification_records"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    channel_type: Mapped[str] = mapped_column(String(32), default="pushplus")
    reminder_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PrivacyPolicy(Base, BaseColumns):
    """隐私与安全（08 §9：云端外发总开关默认关闭）。"""

    __tablename__ = "privacy_policies"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    file_retention_days: Mapped[int] = mapped_column(Integer, default=180)
    ai_session_retention_days: Mapped[int] = mapped_column(Integer, default=90)
    mask_sensitive: Mapped[bool] = mapped_column(Boolean, default=True)
    cloud_egress_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    external_call_audit: Mapped[bool] = mapped_column(Boolean, default=True)


class AIEvaluationCase(Base, BaseColumns):
    """AI 固定评测用例（24 §5）。"""

    __tablename__ = "ai_evaluation_cases"

    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), default="general")
    question: Mapped[str] = mapped_column(Text)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ImportBatch(Base, BaseColumns):
    """数据导入批次（24 §7：上传→校验→映射→预览→重复检查→草稿→批量确认）。"""

    __tablename__ = "import_batches"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_file_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/confirmed
    field_mapping: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    success_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportRecord(Base, BaseColumns):
    """数据导入记录（24 §7：每行一个记录，保留原始数据、错误与重复判断）。"""

    __tablename__ = "import_records"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    batch_id: Mapped[str] = mapped_column(String(32), index=True)
    row_no: Mapped[int] = mapped_column(Integer, default=0)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/confirmed/error/duplicate
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AIConversation(Base, BaseColumns):
    """AI 对话会话（09 §3：新建对话 / 历史对话窗口）。"""

    __tablename__ = "ai_conversations"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    context: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIMessage(Base, BaseColumns):
    """AI 对话消息（用户 / 助手），保留每轮的结论类型与可信度。"""

    __tablename__ = "ai_messages"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(16))            # user / assistant
    content: Mapped[str] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON 数组
    trust: Mapped[str | None] = mapped_column(Text, nullable=True)     # JSON 对象


class BeikeConfig(Base, BaseColumns):
    """贝壳开放平台配置（OAuth2.0：AppKey / AppSecret）。

    用途：房产估值（/api/assessPrice）。密钥只存密文；状态记录最近一次真实调用结果。
    """

    __tablename__ = "beike_configs"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    app_key_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_secret_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str] = mapped_column(String(255), default="https://gw-open.ke.com")
    default_city: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_city_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_status: Mapped[str] = mapped_column(String(32), default="unconfigured")
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
