"""安全回归：审计出来的几个口子，改坏了不会报错，只会悄悄泄露。

覆盖：
1. 已初始化后 `/setup/summary` 匿名访问不得返回管理员账号与家庭名；
2. `/settings/system` 不能把审计记录捎带给没有 audit_view 的成员；
3. 没有家庭归属的账号查审计/备份时必须拿到空结果（而不是全部家庭的数据）；
4. 修改成员角色要按枚举校验，不能写入 owner 之类的任意值。
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.base import SessionLocal
from tests.conftest import auth, complete_setup

MEMBER = {"username": "viewer", "display_name": "只读成员",
          "password": "Viewer@2026#1", "role": "readonly"}


async def _readonly_headers(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/settings/members", headers=h, json=MEMBER)
    assert r.json()["success"] is True, r.text
    r = await client.post("/api/v1/auth/login",
                          json={"username": MEMBER["username"], "password": MEMBER["password"]})
    return auth(r.json()["data"]["access_token"])


async def test_setup_summary_hides_identity_when_anonymous(client):
    """已初始化后，未登录访问摘要不能看到管理员账号与家庭名。"""
    await complete_setup(client)

    r = await client.get("/api/v1/setup/summary")
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["initialized"] is True
    assert data["admin"] is None, "匿名访问不应拿到管理员账号"
    assert data["household"] is None, "匿名访问不应拿到家庭名"
    assert data.get("redacted") is True


async def test_setup_summary_full_for_logged_in(client):
    """登录之后仍要能看到完整摘要（设置页要用）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    data = (await client.get("/api/v1/setup/summary", headers=h)).json()["data"]
    assert data["admin"] is not None
    assert data["household"] is not None
    assert not data.get("redacted")


async def test_setup_summary_full_during_wizard(client):
    """未初始化时（向导阶段）必须给出完整信息，否则完成页拿不到管理员与家庭。"""
    from app.api.v1.setup import summary as summary_endpoint  # noqa: F401  仅确认可导入

    r = await client.post("/api/v1/setup/database",
                          json={"use_embedded_dev_database": True, "host": "127.0.0.1", "port": 3306,
                                "database_name": "fam", "username": "root", "password": "x",
                                "ssl_enabled": False, "connect_timeout": 5})
    assert r.json()["success"] is True, r.text
    r = await client.post("/api/v1/setup/admin", json={
        "display_name": "系统管理员", "username": "admin", "password": "Fam@2026#Strong",
        "confirm_password": "Fam@2026#Strong", "recovery_email": "admin@example.com"})
    assert r.json()["success"] is True, r.text
    r = await client.post("/api/v1/setup/household",
                          json={"name": "我家", "base_currency": "CNY", "timezone": "Asia/Shanghai",
                                "is_multi_member": True})
    assert r.json()["success"] is True, r.text

    data = (await client.get("/api/v1/setup/summary")).json()["data"]
    assert data["initialized"] is False
    assert data["admin"]["username"] == "admin"
    assert data["household"]["name"] == "我家"


async def test_system_endpoint_hides_audit_for_readonly(client):
    """只读成员打开「系统状态」看不到审计记录。"""
    h = await _readonly_headers(client)
    data = (await client.get("/api/v1/settings/system", headers=h)).json()["data"]
    assert data["recent_audit"] == []
    assert data["audit_total"] is None


async def test_system_endpoint_shows_audit_for_owner(client):
    """所有者能正常看到审计摘要（不能为了收紧而把功能关掉）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    data = (await client.get("/api/v1/settings/system", headers=h)).json()["data"]
    assert isinstance(data["recent_audit"], list)
    assert data["audit_total"] is not None


async def test_audit_queries_return_nothing_without_household(client):
    """没有家庭归属时，审计列表与详情都必须返回空，而不是全部家庭的数据。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    # 先制造一条真实审计（登录本身就会写一条）
    r = await client.get("/api/v1/audit", headers=h)
    items = r.json()["data"]["items"]
    assert items, "应当已有审计记录"
    log_id = items[0]["id"]

    from app.services import audit as audit_service

    async with SessionLocal() as db:
        rows, total = await audit_service.list_audit_logs(db, household_id=None)
        assert rows == [] and total == 0
        log, changes = await audit_service.audit_detail(db, log_id, household_id=None)
        assert log is None and changes == []


async def test_backup_list_requires_household(client):
    """无家庭归属时不列出任何备份。"""
    from app.services import backup as backup_service

    tokens = await complete_setup(client)
    assert await backup_service_backups(backup_service) == []


async def backup_service_backups(service) -> list:
    async with SessionLocal() as db:
        return await service.list_backups(db, None)


async def test_update_member_rejects_unknown_role(client):
    """改成员角色必须按枚举校验，不能写任意字符串（提权口子）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/settings/members", headers=h, json=MEMBER)
    member_id = r.json()["data"]["id"]

    r = await client.patch(f"/api/v1/settings/members/{member_id}", headers=h,
                           json={"role": "superuser"})
    assert r.json()["success"] is False
    assert r.status_code in (400, 422)

    # 合法角色仍然可以改
    r = await client.patch(f"/api/v1/settings/members/{member_id}", headers=h,
                           json={"role": "member"})
    assert r.json()["success"] is True, r.text

    async with SessionLocal() as db:
        from app.db.models.identity import HouseholdMember

        member = (await db.execute(
            select(HouseholdMember).where(HouseholdMember.id == member_id)
        )).scalars().first()
        assert member.role == "member"
