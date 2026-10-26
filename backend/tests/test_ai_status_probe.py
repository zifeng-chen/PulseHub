"""AI 状态必须是真实的连通性探测，而不是"配置了就算已连接"。

背景：曾出现设置页显示"已连接"、实际 Ollama 没启动的情况，
用户看到的是"调用失败：网络异常"。现在 status() 会真的打一次 /models。
"""

from __future__ import annotations

from app.adapters.providers import _PROBE_CACHE, _probe_endpoint
from app.db.base import SessionLocal
from app.db.models.config import LocalAIConfig
from tests.conftest import auth, complete_setup


async def _add_local_config(base_url: str, enabled: bool = True) -> None:
    async with SessionLocal() as db:
        db.add(LocalAIConfig(
            id="cfg-local-ai", protocol="openai_compatible", base_url=base_url,
            model="test-model", timeout=10, context_length=4096, stream=False,
            enabled=enabled,
        ))
        await db.commit()


async def test_probe_reports_unreachable_endpoint():
    """端口上没有服务时必须判定为不可达。"""
    _PROBE_CACHE.clear()
    ok, note, models = await _probe_endpoint("http://127.0.0.1:1/v1", None, "test|unreachable")
    assert ok is False
    assert note  # 具体原因：ConnectionRefusedError 等
    assert models == []


async def test_local_ai_status_unreachable_instead_of_connected(client):
    """已配置且启用但服务没起 → 状态应是 unreachable，不能是 connected。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _add_local_config("http://127.0.0.1:1/v1")
    _PROBE_CACHE.clear()

    resp = await client.get("/api/v1/settings/local-ai", headers=h)
    assert resp.json()["success"] is True, resp.text
    adapter = resp.json()["data"]["adapter"]
    assert adapter["status"] == "unreachable", adapter
    assert adapter["status_label"] == "不可达"
    assert adapter["extra"]["reachable"] is False


async def test_local_ai_status_disabled_stays_disabled(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _add_local_config("http://127.0.0.1:1/v1", enabled=False)

    resp = await client.get("/api/v1/settings/local-ai", headers=h)
    adapter = resp.json()["data"]["adapter"]
    assert adapter["status"] == "disabled"


async def test_local_ai_status_flags_missing_model(client, monkeypatch):
    """服务通但模型没拉取，也不能算「已连接」。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _add_local_config("http://127.0.0.1:11434/v1")
    _PROBE_CACHE.clear()

    async def fake_probe(base_url, key, cache_key):
        return True, "ok", ["some-other-model"]

    monkeypatch.setattr("app.adapters.providers._probe_endpoint", fake_probe)

    resp = await client.get("/api/v1/settings/local-ai", headers=h)
    adapter = resp.json()["data"]["adapter"]
    assert adapter["status"] == "unreachable", adapter
    assert "不存在" in adapter["detail"]
    assert adapter["extra"]["model_missing"] is True
