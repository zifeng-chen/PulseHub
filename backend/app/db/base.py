"""数据库基座：SQLAlchemy 2.x Async 引擎与会话（01 §5、10 §3）。

- 生产 / 产品首次启动向导固定 MySQL 8.x（aiomysql / PyMySQL）。
- 本地开发与自动化测试默认 SQLite（aiosqlite）作为可运行底座。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings
from app.core.ids import new_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class BaseColumns:
    """所有业务实体共有字段（10 §3、23 §2）。"""

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


_connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    future=True,
    pool_pre_ping=not settings.database_url.startswith("sqlite"),
    connect_args=_connect_args,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """仅用于本地开发/测试快速建表；生产走 Alembic 迁移。"""
    from app.db import models  # noqa: F401  确保模型已注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
