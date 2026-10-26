"""锁屏解锁：只校验当前用户密码，不签发新会话。"""

from __future__ import annotations

from tests.conftest import ADMIN, auth, complete_setup


async def test_verify_password_ok(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    resp = await client.post("/api/v1/auth/verify-password", headers=h,
                             json={"password": ADMIN["password"]})
    assert resp.json()["success"] is True, resp.text
    assert resp.json()["data"]["verified"] is True


async def test_verify_password_wrong(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    resp = await client.post("/api/v1/auth/verify-password", headers=h,
                             json={"password": "definitely-wrong"})
    assert resp.status_code == 401
    assert "密码不正确" in resp.json()["error"]["message"]


async def test_verify_password_requires_login(client):
    """未登录不能调用解锁接口。"""
    resp = await client.post("/api/v1/auth/verify-password", json={"password": "x"})
    assert resp.status_code == 401
