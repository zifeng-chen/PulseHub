"""测试夹具：使用独立临时数据库，保证用例互不污染。"""

from __future__ import annotations

import os
import tempfile

TMP_DIR = tempfile.mkdtemp(prefix="fam_test_")
os.environ["FAM_APP_ENV"] = "testing"
os.environ["FAM_DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP_DIR}/test.db"
os.environ["FAM_ATTACHMENT_DIR"] = f"{TMP_DIR}/attachments"
os.environ["FAM_BACKUP_DIR"] = f"{TMP_DIR}/backups"
os.environ["FAM_JWT_SECRET"] = "test-secret-key"
os.environ["FAM_LOGIN_MAX_FAILURES"] = "3"

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db.base import Base, SessionLocal, create_all, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.permissions import seed_role_permissions  # noqa: E402

ADMIN = {"display_name": "系统管理员", "username": "admin", "password": "Fam@2026#Strong",
         "confirm_password": "Fam@2026#Strong", "recovery_email": "admin@example.com"}


@pytest_asyncio.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await create_all()
    async with SessionLocal() as db:
        await seed_role_permissions(db)
        await db.commit()
    app.state.maintenance = {"active": False, "reason": None, "started_at": None}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def complete_setup(client: AsyncClient) -> dict:
    """走完首次启动引导并返回登录令牌。"""
    r = await client.post("/api/v1/setup/database",
                          json={"use_embedded_dev_database": True, "host": "127.0.0.1", "port": 3306,
                                "database_name": "fam", "username": "root", "password": "x",
                                "ssl_enabled": False, "connect_timeout": 5})
    assert r.json()["success"] is True, r.text
    r = await client.post("/api/v1/setup/admin", json=ADMIN)
    assert r.json()["success"] is True, r.text
    r = await client.post("/api/v1/setup/household",
                          json={"name": "测试家庭", "base_currency": "CNY",
                                "timezone": "Asia/Shanghai", "is_multi_member": True})
    assert r.json()["success"] is True, r.text
    await client.post("/api/v1/setup/finance", json={"asset_purchase_separate": True,
                                                     "investment_valuation_in_net_worth": True})
    r = await client.post("/api/v1/setup/complete")
    assert r.json()["success"] is True, r.text
    r = await client.post("/api/v1/auth/login",
                          json={"username": ADMIN["username"], "password": ADMIN["password"]})
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    return {"access": data["access_token"], "refresh": data["refresh_token"], "session_id": data["session_id"],
            "user": data["user"]}


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
