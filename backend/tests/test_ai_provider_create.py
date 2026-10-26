"""第三方 AI 提供商的创建。

此前只有列表 / 更新 / 删除，没有 POST，导致界面永远停在「尚未配置」且无入口可配。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup

BODY = {
    "name": "DeepSeek",
    "interface_type": "openai_compatible",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-test-123456",
    "default_model": "deepseek-chat",
    "priority": 50,
}


async def test_create_provider_defaults_to_disabled(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    created = await client.post("/api/v1/settings/ai-providers", headers=h, json=BODY)
    assert created.json()["success"] is True, created.text
    assert created.json()["data"]["enabled"] is False

    rows = (await client.get("/api/v1/settings/ai-providers", headers=h)).json()["data"]["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "DeepSeek"
    assert row["base_url"] == "https://api.deepseek.com/v1"
    assert row["enabled"] is False
    # Key 只回掩码，且不应出现明文
    assert row["api_key_masked"]
    assert "sk-test-123456" not in str(row)


async def test_create_provider_validation(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    missing = await client.post("/api/v1/settings/ai-providers", headers=h, json={})
    assert missing.status_code == 422
    assert "name" in missing.json()["error"]["field_errors"]

    bad_url = await client.post("/api/v1/settings/ai-providers", headers=h,
                                json={**BODY, "base_url": "api.deepseek.com"})
    assert bad_url.status_code == 422
    assert "base_url" in bad_url.json()["error"]["field_errors"]

    first = await client.post("/api/v1/settings/ai-providers", headers=h, json=BODY)
    assert first.json()["success"] is True
    dup = await client.post("/api/v1/settings/ai-providers", headers=h, json=BODY)
    assert dup.status_code == 422
    assert "name" in dup.json()["error"]["field_errors"]
