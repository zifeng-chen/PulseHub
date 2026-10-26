"""页面级 AI 分析接口（用户反馈：AI 按钮应直接出结果，而不是打开 AI 助手）。

测试重点不是模型输出质量（测试环境没有模型），而是：
1. 接口按 scope 正确聚合确定性事实；
2. 模型不可用时仍然返回事实 + 明确原因，前端不至于空白；
3. 非法 scope 被拦住。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    a1 = (await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "50000", "include_in_net_worth": True,
    })).json()["data"]["id"]
    # 一笔已确认支出，保证有真实现金流数据
    ev = (await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "超市", "amount": "300",
        "occurred_at": "2026-10-10T10:00:00", "account_id": a1, "source": "manual",
    })).json()["data"]
    await client.post(f"/api/v1/ledger/events/{ev['id']}/confirm", headers=h, json={})
    return {"headers": h, "a1": a1}


async def test_ai_analysis_cashflow_has_facts(client):
    """现金流分析：即使模型不可用，也要给出账户与月度现金流指标。"""
    ids = await _bootstrap(client)
    r = (await client.get("/api/v1/insights/ai-analysis", headers=ids["headers"],
                          params={"scope": "cashflow"})).json()
    assert r["success"] is True, r
    data = r["data"]
    assert data["scope"] == "cashflow"
    assert data["scope_label"] == "现金流"
    labels = [f["label"] for f in data["facts"]]
    assert "账户余额合计" in labels, labels
    assert any("流入" in x for x in labels), labels
    # 状态只能是 ready / unavailable，二者都必须带得回事实
    assert data["state"] in ("ready", "unavailable")
    if data["state"] == "unavailable":
        assert data["hint"], "不可用时必须说明原因"


async def test_ai_analysis_dashboard_scope(client):
    ids = await _bootstrap(client)
    data = (await client.get("/api/v1/insights/ai-analysis", headers=ids["headers"],
                             params={"scope": "dashboard"})).json()["data"]
    assert data["scope_label"] == "脉枢台"
    assert data["facts"], "脉枢台分析至少要有账户类指标"


async def test_ai_analysis_scope_is_validated(client):
    ids = await _bootstrap(client)
    r = (await client.get("/api/v1/insights/ai-analysis", headers=ids["headers"],
                          params={"scope": "not-a-scope"})).json()
    assert r["success"] is False
    assert r["error"]["code"] == "VALIDATION_ERROR"
