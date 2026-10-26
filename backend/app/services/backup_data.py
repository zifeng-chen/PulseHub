"""备份的数据库导出 / 导入（跨 SQLite 与 MySQL 通用）。

为什么不用 mysqldump：
- 后端容器内不一定有 mysql 客户端；
- 同一套逻辑要能覆盖本地 SQLite 与生产 MySQL；
- 家庭财务数据量小，逐表 JSON 足够快，且便于校验与人工检查。

产物：`<backup_dir>/<backup_no>/database.json`
结构：`{ "表名": [ {列: 值, ...}, ... ], "__meta__": {...} }`
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.sqltypes import Date, DateTime, Numeric, Time

from app.db.base import Base

# 必须显式导入模型包：否则 Base.metadata 里没有表，
# 备份会导出一个只有 __meta__ 的空 database.json，恢复时则"什么也没做"却显示成功。
import app.db.models  # noqa: F401,E402

DATA_FILE = "database.json"
META_KEY = "__meta__"
# 不参与恢复的表：
# - alembic_version 由迁移工具维护；
# - backup_records / restore_records 是运维元数据，覆盖会把"备份清单"一起回滚成空。
SKIP_TABLES = {"alembic_version", "backup_records", "restore_records"}
# 只清空、不回填：恢复后应强制所有用户重新登录
CLEAR_ONLY_TABLES = {"user_sessions"}


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__t": "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {"__t": "date", "v": value.isoformat()}
    if isinstance(value, time):
        return {"__t": "time", "v": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__t": "decimal", "v": str(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"__t": "bytes", "v": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value: Any, column_type: Any) -> Any:
    if isinstance(value, dict) and "__t" in value:
        kind, raw = value["__t"], value["v"]
        if kind == "datetime":
            return datetime.fromisoformat(raw)
        if kind == "date":
            return date.fromisoformat(raw)
        if kind == "time":
            return time.fromisoformat(raw)
        if kind == "decimal":
            return Decimal(raw)
        if kind == "bytes":
            return base64.b64decode(raw)
    # 脏数据兜底：按列类型再尝试一次
    if isinstance(value, str):
        if isinstance(column_type, DateTime):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value
        if isinstance(column_type, Date):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        if isinstance(column_type, Numeric):
            try:
                return Decimal(value)
            except Exception:  # noqa: BLE001
                return value
    return value


async def dump_database(db: AsyncSession, target_dir: Path, migration_version: str = "") -> dict:
    """把全部业务表导出为 JSON，返回 {表名: 行数}。"""
    payload: dict[str, Any] = {META_KEY: {
        "exported_at": datetime.utcnow().isoformat(),
        "migration_version": migration_version,
    }}
    summary: dict[str, int] = {}

    for table in Base.metadata.sorted_tables:
        if table.name in SKIP_TABLES:
            continue
        rows = (await db.execute(table.select())).mappings().all()
        payload[table.name] = [{k: _encode(v) for k, v in dict(row).items()} for row in rows]
        summary[table.name] = len(rows)

    (target_dir / DATA_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return summary


def read_database_file(source_dir: Path) -> dict:
    path = source_dir / DATA_FILE
    if not path.is_file():
        raise FileNotFoundError(f"备份中没有 {DATA_FILE}：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


async def load_database(db: AsyncSession, source_dir: Path) -> dict:
    """用备份覆盖当前数据库，返回 {表名: 行数}。

    调用方负责：先做当前数据的快照、失败时回滚、恢复后校验。
    """
    payload = read_database_file(source_dir)
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"

    # 只有 MySQL 需要临时关外键（自引用分类等在删除阶段会冲突）；
    # SQLite 默认不强制外键，切换 PRAGMA 反而会打断当前事务。
    toggle_fk = dialect == "mysql"
    if toggle_fk:
        await db.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

    restored: dict[str, int] = {}
    try:
        # 先按反序清空，再按正序写入，避免外键中间态
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in SKIP_TABLES:
                continue
            await db.execute(table.delete())

        for table in Base.metadata.sorted_tables:
            if table.name in SKIP_TABLES:
                continue
            if table.name in CLEAR_ONLY_TABLES:
                restored[table.name] = 0  # 已清空，不回填
                continue
            rows = payload.get(table.name) or []
            if not rows:
                restored[table.name] = 0
                continue

            column_types = {c.name: c.type for c in table.columns}
            prepared = []
            for raw in rows:
                prepared.append({
                    k: _decode(v, column_types.get(k))
                    for k, v in raw.items()
                    if k in column_types  # 忽略备份里多出来的未知列
                })
            await db.execute(table.insert(), prepared)
            restored[table.name] = len(prepared)
    finally:
        if toggle_fk:
            await db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    return restored
