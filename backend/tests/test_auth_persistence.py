"""登录持久化：刷新令牌时长与轮换。

目标：手机上关掉浏览器/隔几天再打开，仍是登录状态。
- 默认（记住此设备）30 天，每次刷新滑动续期
- 未勾选则 1 天，避免公用设备长期留登录态
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.security import create_refresh_token, decode_token
from tests.conftest import complete_setup


def test_refresh_token_default_is_long_lived():
    token, expires_at = create_refresh_token("user-1", "sess-1")
    payload = decode_token(token)
    assert payload["typ"] == "refresh"
    remaining = expires_at.astimezone(timezone.utc) - datetime.now(timezone.utc)
    assert remaining.days >= 28  # 默认 30 天，留 2 天容差


def test_short_lived_when_not_remembered():
    _, long_expires = create_refresh_token("user-1", "sess-1")
    _, short_expires = create_refresh_token("user-1", "sess-1", days=1)
    assert (long_expires - short_expires).days >= 28


async def test_refresh_rotates_and_rejects_reuse(client):
    tokens = await complete_setup(client)
    first = tokens["refresh"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert resp.json()["success"] is True, resp.text
    data = resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"] and data["refresh_token"] != first

    # 旧令牌已被轮换，不能重复使用
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert reuse.status_code == 401
