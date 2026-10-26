"""补齐本地 SQLite 库的结构（只用于开发/测试库，不碰生产 MySQL）。

背景：本地 `data/*.db` 是历史用 `create_all` 建的，没有 alembic 版本记录。
代码演进后新增的列 / 表不会自动出现，运行时就会报
`no such column: precious_metals.brand_code` 这类错误，页面直接「加载失败」。

做法：
1. 按 ORM 模型对比实际表结构，**只补缺失**（不删不改已有列，不动数据）；
2. 缺表用 `metadata.create_all(tables=[...])` 建；
3. 缺列用 `ALTER TABLE ADD COLUMN`，NOT NULL 列自动带默认值（SQLite 限制）。

用法：
    python scripts/ensure_local_schema.py            # 处理 data/ 下所有 .db
    python scripts/ensure_local_schema.py --dry-run  # 只报告不执行
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("FAM_DATABASE_URL", "sqlite+aiosqlite:///./data/_e2e.db")

from sqlalchemy import create_engine  # noqa: E402

import app.db.models  # noqa: E402,F401  注册全部模型
from app.db.base import Base  # noqa: E402


def _sqlite_type(column) -> str:
    """ORM 列类型 → SQLite 类型名。"""
    try:
        return column.type.compile(dialect=create_engine("sqlite://").dialect)
    except Exception:  # noqa: BLE001
        return "TEXT"


def _default_literal(column) -> str | None:
    """NOT NULL 列的默认值字面量（SQLite 要求 ADD COLUMN 时给出）。"""
    if column.server_default is not None:
        text = str(getattr(column.server_default, "arg", "") or "")
        if text and not text.startswith("("):
            return text if text.replace(".", "").isdigit() else f"'{text}'"
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return f"'{value}'"
    # 兜底：按类型给零值，避免 ALTER 失败
    type_name = _sqlite_type(column).upper()
    if any(k in type_name for k in ("INT", "NUMERIC", "DECIMAL", "FLOAT", "REAL")):
        return "0"
    return "'" + ("by_weight" if column.name == "pricing_mode" else "") + "'"


def ensure_db(db_path: Path, dry_run: bool = False) -> tuple[int, int]:
    """补齐单个库。返回 (补的列数, 补的表数)。"""
    if not db_path.exists():
        print(f"  跳过（不存在）：{db_path}")
        return 0, 0

    con = sqlite3.connect(db_path)
    added_cols = 0
    added_tables = 0
    try:
        for table in Base.metadata.sorted_tables:
            existing_cols = {r[1] for r in con.execute(f"PRAGMA table_info({table.name})").fetchall()}
            if not existing_cols:
                print(f"  [缺表] {table.name}")
                if not dry_run:
                    engine = create_engine(f"sqlite:///{db_path}")
                    Base.metadata.create_all(engine, tables=[table])
                    engine.dispose()
                added_tables += 1
                continue

            for column in table.columns:
                if column.name in existing_cols:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {column.name} {_sqlite_type(column)}'
                if not column.nullable:
                    ddl += f" NOT NULL DEFAULT {_default_literal(column)}"
                print(f"  [缺列] {table.name}.{column.name}")
                if not dry_run:
                    con.execute(ddl)
                added_cols += 1
        if not dry_run:
            con.commit()
    finally:
        con.close()
    return added_cols, added_tables


def main() -> int:
    parser = argparse.ArgumentParser(description="补齐本地 SQLite 库结构")
    parser.add_argument("--dry-run", action="store_true", help="只报告不修改")
    args = parser.parse_args()

    data_dir = BACKEND_DIR / "data"
    dbs = sorted(data_dir.glob("*.db"))
    if not dbs:
        print("data/ 下没有 .db 文件")
        return 0

    total_cols = total_tables = 0
    for db in dbs:
        print(f"\n=== {db.name} ===")
        cols, tables = ensure_db(db, dry_run=args.dry_run)
        print(f"  {'需补' if args.dry_run else '已补'}：{cols} 列 / {tables} 表")
        total_cols += cols
        total_tables += tables

    print(f"\n合计：{total_cols} 列 / {total_tables} 表{'（dry-run，未修改）' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
