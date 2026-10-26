"""负债分类迁移（0029）：类型同名分类 → 用途分类，且不影响用户自建子分类。"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db.base import Base
from app.db import models as _models  # noqa: F401 - 确保全部模型注册到 metadata

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
    "a7b8c9d0e1f2_0029_liability_categories_loan_method.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_0029", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    Base.metadata.create_all(engine)
    return engine


def _add_category(conn, household_id: str, name: str, parent_id: str | None = None) -> str:
    cid = uuid.uuid4().hex
    conn.execute(text(
        "INSERT INTO categories (id, household_id, domain, parent_id, name, level, sort_order, "
        "status, is_system, ai_recommendable, created_at, updated_at) "
        "VALUES (:id, :hh, 'liability', :pid, :name, :level, 0, 'active', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": cid, "hh": household_id, "pid": parent_id, "name": name,
        "level": 2 if parent_id else 1})
    return cid


def _add_liability(conn, household_id: str, name: str, category_id: str) -> None:
    conn.execute(text(
        "INSERT INTO liabilities (id, household_id, name, liability_type, category_id, currency, "
        "initial_amount, payment_frequency, include_in_net_worth, status, version, created_at, updated_at) "
        "VALUES (:id, :hh, :name, 'mortgage', :cid, 'CNY', 1000000, 'monthly', 1, 'active', 1, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": uuid.uuid4().hex, "hh": household_id, "name": name, "cid": category_id})


def test_migration_remaps_categories_and_keeps_custom_children(tmp_path):
    migration = _load_migration()
    engine = _engine(tmp_path)
    household = "hh-mig"

    with engine.begin() as conn:
        old = {
            name: _add_category(conn, household, name)
            for name in ("房贷", "车贷", "消费贷", "其他负债")
        }
        # 用户自己在「房贷」下加过子分类 → 这个父分类不能被删
        _add_category(conn, household, "公积金部分", parent_id=old["房贷"])
        _add_liability(conn, household, "住房贷款", old["房贷"])
        _add_liability(conn, household, "车贷-本田", old["车贷"])
        _add_liability(conn, household, "信用贷", old["消费贷"])

        migration.migrate_categories(conn)
        migration.migrate_categories(conn)  # 幂等：再跑一次不能产生重复分类

        rows = conn.execute(text(
            "SELECT name, deleted_at FROM categories WHERE domain = 'liability' AND level = 1"
        )).fetchall()
        names = [r[0] for r in rows]
        alive = {r[0] for r in rows if r[1] is None}

        # 新的用途分类都在，且没有重复
        assert set(migration.NEW_CATEGORIES) <= alive
        assert len(names) == len(set(names))

        # 有子分类的「房贷」保留；其余旧分类被软删除
        assert "房贷" in alive
        assert {"车贷", "消费贷", "其他负债"}.isdisjoint(alive)

        mapped = dict(conn.execute(text(
            "SELECT l.name, c.name FROM liabilities l JOIN categories c ON c.id = l.category_id"
        )).fetchall())
        assert mapped["住房贷款"] == "购房"
        assert mapped["车贷-本田"] == "购车"
        assert mapped["信用贷"] == "日常周转"
