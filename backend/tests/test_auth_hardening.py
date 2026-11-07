"""登录安全加固的回归测试。

这几条单独看都不复杂，但一旦回退就是「限流形同虚设」「改密止不了血」，
所以用测试钉住：
- IPv6 按 /64 聚合计数（否则换地址就重置窗口）
- 失败锁定按次数递增（固定 5 分钟挡不住慢速爆破）
- 登录失败写入审计（只记成功等于丢掉入侵线索）
- 改密吊销全部会话（否则旧 refresh token 还能续期）
"""
from __future__ import annotations

from app.core.config import settings
from app.core.netutil import rate_limit_key
from app.services.auth import _lock_minutes
from tests.conftest import ADMIN, auth, complete_setup


def test_ipv6_buckets_aggregate_to_64_prefix():
    a = rate_limit_key("240e:3a9:5605:14c0:1111:2222:3333:4444")
    b = rate_limit_key("240e:3a9:5605:14c0:aaaa:bbbb:cccc:dddd")
    c = rate_limit_key("240e:3a9:5605:14c1::1")
    assert a == b, "同一 /64 内换地址必须共用同一个限流桶"
    assert a != c


def test_ipv4_bucket_keeps_single_address():
    assert rate_limit_key("203.0.113.7") == "203.0.113.7"
    assert rate_limit_key("203.0.113.7") != rate_limit_key("203.0.113.8")


def test_ipv4_mapped_ipv6_falls_back_to_v4():
    assert rate_limit_key("::ffff:203.0.113.7") == "203.0.113.7"


def test_lock_minutes_escalate_then_cap(monkeypatch):
    monkeypatch.setattr(settings, "login_max_failures", 5)
    monkeypatch.setattr(settings, "login_lock_minutes", 5)
    monkeypatch.setattr(settings, "login_lock_max_minutes", 60)

    assert _lock_minutes(5) == 5      # 刚触发
    assert _lock_minutes(9) == 5      # 同一档
    assert _lock_minutes(10) == 10    # 第二次锁
    assert _lock_minutes(15) == 20
    assert _lock_minutes(20) == 40
    assert _lock_minutes(60) == 60    # 封顶


async def test_failed_login_is_audited(client):
    tokens = await complete_setup(client)
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert r.status_code == 401

    r = await client.get("/api/v1/audit", headers=auth(tokens["access"]), params={"action": "auth.login_failed"})
    assert r.json()["success"] is True, r.text
    items = r.json()["data"]["items"]
    assert items, "登录失败必须落审计"
    assert items[0]["action"] == "auth.login_failed"


async def test_unknown_user_failure_is_indistinguishable(client):
    """账号不存在也返回同样的 401 文案，且不抛 500。"""
    await complete_setup(client)
    r = await client.post("/api/v1/auth/login", json={"username": "no-such-user", "password": "whatever"})
    assert r.status_code == 401
    body = r.json()
    assert body["success"] is False
    assert "账号或密码" in body["error"]["message"] or "登录失败" in body["error"]["message"]


async def test_change_password_revokes_every_session(client):
    first = await complete_setup(client)
    r = await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    )
    second = r.json()["data"]

    new_password = "PulseHub@2026#Strong"
    r = await client.post(
        "/api/v1/auth/change-password",
        headers=auth(first["access"]),
        json={"old_password": ADMIN["password"], "new_password": new_password},
    )
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["revoked_sessions"] >= 1

    # 两个旧的 refresh token 都必须失效，否则改密码挡不住已泄露的令牌
    for token in (first["refresh"], second["refresh_token"]):
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert r.status_code == 401
