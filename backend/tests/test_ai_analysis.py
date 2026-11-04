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


def test_parse_bullets_tolerates_format_drift():
    """小模型经常把「结论：」写成加粗、半角冒号、甚至漏掉冒号，解析不能因此丢结果。"""
    from app.services.ai_analysis import _parse_bullets

    cases = [
        "结论：手头有18万\n关注：有个月花超了\n建议：先把信用卡还了",
        "**结论：** 手头有18万\n**关注:** 有个月花超了\n**建议** 先把信用卡还了",
        "- 结论 手头有18万\n- 关注 有个月花超了\n- 建议 先把信用卡还了",
    ]
    for text in cases:
        bullets = _parse_bullets(text)
        assert [b["label"] for b in bullets] == ["结论", "关注", "建议"], (text, bullets)
        assert all(b["text"].strip() for b in bullets), bullets

    # 重复行只取第一条，避免同一标签出现两次
    dup = _parse_bullets("结论：第一句\n结论：第二句")
    assert [b["text"] for b in dup] == ["第一句"]


def test_plain_summary_speaks_like_family():
    """`_plain_summary` 是模型不可用时的兜底，必须说人话而不是甩术语。"""
    from app.services.ai_analysis import _plain_summary

    facts = [
        {"label": "净资产", "value": "860000.00 CNY"},
        {"label": "流动资金", "value": "128400.00 CNY"},
        {"label": "偿债覆盖", "value": "4.4 倍"},
        {"label": "生存跑道", "value": "5.7 个月"},
        {"label": "资产负债率", "value": "35.0%"},
    ]
    text = _plain_summary(facts, [{"title": "3 个账户从未对账", "detail": "x"}])
    assert text
    for jargon in ("净资产", "流动资金", "偿债覆盖", "生存跑道", "资产负债率"):
        assert jargon not in text, f"不该出现术语：{jargon}"
    assert "把欠的钱都扣掉" in text
    assert "3 个账户从未对账" in text
