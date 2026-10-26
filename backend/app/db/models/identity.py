"""身份域模型（10 §1：身份组）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class SystemConfig(Base, BaseColumns):
    """系统级键值配置（是否已初始化等）。"""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(32), default="system")


class User(Base, BaseColumns):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recovery_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Household(Base, BaseColumns):
    __tablename__ = "households"

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    is_multi_member: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class HouseholdMember(Base, BaseColumns):
    __tablename__ = "household_members"
    __table_args__ = (UniqueConstraint("household_id", "user_id", name="uq_member_hh_user"),)

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")  # owner/admin/member/readonly
    status: Mapped[str] = mapped_column(String(16), default="active")
    scope_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Base, BaseColumns):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RolePermission(Base, BaseColumns):
    """角色动作集合（23 §6：十类动作）。"""

    __tablename__ = "role_permissions"

    role_code: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(32))
    resource: Mapped[str] = mapped_column(String(64), default="*")
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)


class MemberScope(Base, BaseColumns):
    """成员对象范围（view 范围限制）。"""

    __tablename__ = "member_scopes"

    household_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    scope_type: Mapped[str] = mapped_column(String(32))  # member/account/asset...
    scope_ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    can_view: Mapped[bool] = mapped_column(Boolean, default=True)


class UserSession(Base, BaseColumns):
    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(String(32), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), index=True)
    device: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_summary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remember_device: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base, BaseColumns):
    """审计主记录（08 §10、20 §4）。只允许追加。"""

    __tablename__ = "audit_logs"

    request_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    household_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ip_summary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    related_object_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="success")  # success/failure
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    desensitized: Mapped[bool] = mapped_column(Boolean, default=True)
    before_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLogChange(Base, BaseColumns):
    """字段级前后差异（08 §10、20 §4）。"""

    __tablename__ = "audit_log_changes"

    audit_log_id: Mapped[str] = mapped_column(String(32), index=True)
    field_name: Mapped[str] = mapped_column(String(128))
    field_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)
