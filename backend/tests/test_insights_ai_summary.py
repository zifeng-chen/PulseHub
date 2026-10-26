"""洞察页 AI 综合摘要。

此前 overview 里写死 ai_summary_state=module_unavailable，AI 接通了也永远显示不可用。
现在单独走 GET /insights/ai-summary，按需生成且不阻塞结论列表。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def test_ai_summary_ready(client, monkeypatch):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    async def fake_answer(*args, **kwargs):
        return {"answer": "本月支出下降 5%，现金流健康。", "model_type": "local",
                "provider": None, "masked": False}

    monkeypatch.setattr("app.services.ai._model_answer", fake_answer)

    resp = await client.get("/api/v1/insights/ai-summary", headers=h)
    assert resp.json()["success"] is True, resp.text
    data = resp.json()["data"]
    assert data["state"] == "ready"
    assert "现金流健康" in data["summary"]
    assert data["model_type"] == "local"


async def test_ai_summary_degrades_gracefully(client, monkeypatch):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    async def boom(*args, **kwargs):
        raise RuntimeError("ConnectionRefusedError: ollama not running")

    monkeypatch.setattr("app.services.ai._model_answer", boom)

    resp = await client.get("/api/v1/insights/ai-summary", headers=h)
    data = resp.json()["data"]
    assert data["state"] == "unavailable"
    assert data["summary"] is None
    assert "确定性" in data["hint"]


async def test_overview_marks_summary_on_demand(client):
    """概览接口本身不再声称「模块未交付」。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    data = (await client.get("/api/v1/insights/overview", headers=h)).json()["data"]
    assert data["ai_summary_state"] == "on_demand"
    assert "模块" not in data["ai_summary_hint"]
