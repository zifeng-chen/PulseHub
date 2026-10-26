"""Alembic 异步迁移环境（18 §2.1、22 §5.2）。

数据库 URL 统一从 FAM_DATABASE_URL 读取，避免迁移脚本硬编码连接信息。

注意：这里**不使用** config.set_main_option 注入 URL。
Alembic 的 ConfigParser 会对 % 做插值，一旦数据库口令含 % 就会直接抛
InterpolationSyntaxError 导致容器启动失败；因此改为直接把 settings.database_url
交给 create_async_engine，绕开配置文件插值。

升级前必须具备备份；迁移失败必须停止，不允许启动半升级系统。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db import models  # noqa: F401  注册全部模型

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_CONNECT_ARGS = {"connect_timeout": 10} if settings.database_url.startswith("mysql") else {}


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        connect_args=_CONNECT_ARGS,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
