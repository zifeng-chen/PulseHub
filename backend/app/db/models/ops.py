"""运维基座模型：备份、恢复、幂等记录（18 §2.6、22 §1、10 §6）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseColumns


class BackupRecord(Base, BaseColumns):
    """备份记录（22 §1.2 元数据）。备份不是导出功能，用于灾备与版本升级恢复。"""

    __tablename__ = "backup_records"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    backup_no: Mapped[str] = mapped_column(String(32), index=True)
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    data_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    db_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    migration_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="created")  # created/verified/failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RestoreRecord(Base, BaseColumns):
    """恢复记录（22 §1.4 流程与审计）。"""

    __tablename__ = "restore_records"

    household_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    backup_no: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="preview")  # preview/running/success/failed/rolled_back
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confirm_phrase: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_performed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdempotencyRecord(Base, BaseColumns):
    """幂等键记录（10 §6：写接口必须支持幂等编号）。"""

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "idem_key", name="uq_idem_scope_key"),)

    scope: Mapped[str] = mapped_column(String(64), index=True)
    idem_key: Mapped[str] = mapped_column(String(128), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="completed")
