"""个人资料与自动提醒规则（用户反馈：不能改头像密码；提醒要自动发、可设周期）。"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _headers(client):
    tokens = await complete_setup(client)
    return auth(tokens["access"])


async def test_update_profile(client):
    h = await _headers(client)
    r = (await client.patch("/api/v1/auth/profile", headers=h, json={
        "display_name": "新昵称",
        "email": "me@example.com",
        "phone": "13800138000",
        "avatar": "preset:ocean",
    })).json()
    assert r["success"] is True, r
    assert r["data"]["display_name"] == "新昵称"
    assert r["data"]["avatar"] == "preset:ocean"

    me = (await client.get("/api/v1/auth/me", headers=h)).json()["data"]
    assert me["display_name"] == "新昵称"
    assert me.get("avatar") == "preset:ocean"


async def test_avatar_must_be_preset(client):
    h = await _headers(client)
    r = (await client.patch("/api/v1/auth/profile", headers=h, json={
        "display_name": "x", "avatar": "data:image/png;base64,AAAA",
    })).json()
    assert r["success"] is False
    assert "avatar" in r["error"]["field_errors"]


async def test_notification_auto_roundtrip(client):
    h = await _headers(client)
    saved = (await client.put("/api/v1/settings/notifications/auto", headers=h, json={
        "enabled": True, "days_before": 3, "times_per_day": 2,
        "max_per_period": 5, "hours": [9, 20],
    })).json()
    assert saved["success"] is True, saved
    assert saved["data"]["days_before"] == 3
    assert saved["data"]["hours"] == [9, 20]

    got = (await client.get("/api/v1/settings/notifications", headers=h)).json()["data"]
    assert got["auto"]["times_per_day"] == 2
    assert got["auto"]["max_per_period"] == 5
