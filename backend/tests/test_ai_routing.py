"""AI 问答路由：确定性回答只用于纯数据查询，模型选择必须生效。

故障现场：不论问什么都用确定性模板回答，切换本地/第三方模型也没有变化。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


def test_routing_rules():
    from app.services.ai import _detect_topic, _should_use_deterministic

    # 纯数据查询：auto 模式走确定性
    assert _should_use_deterministic("我有几个账户？", _detect_topic("我有几个账户？"), "auto")
    assert _should_use_deterministic("这个月支出多少", _detect_topic("这个月支出多少"), "auto")

    # 显式选择模型：一律交给模型
    assert not _should_use_deterministic("我有几个账户？", "accounts", "local")
    assert not _should_use_deterministic("我有几个账户？", "accounts", "third_party")

    # 分析/建议类问题：不能拿统计模板硬答
    assert not _should_use_deterministic("帮我分析一下我的消费习惯", "expenses", "auto")
    assert not _should_use_deterministic("为什么这个月支出这么高", "expenses", "auto")
    assert not _should_use_deterministic("我该怎么安排还款", "liabilities", "auto")

    # 长问句即使命中关键词，也交给模型
    q = "我这个月信用卡和房贷加起来大概还了多少以及下一步应该怎么安排现金流"
    assert not _should_use_deterministic(q, "liabilities", "auto")


async def test_explicit_model_is_not_intercepted_by_deterministic(client):
    """选了第三方模型，就问模型：没配置时应报「第三方未连接」，而不是给统计答案。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "测试储蓄卡", "account_type": "bank", "currency": "CNY", "opening_balance": "1000",
    })

    resp = await client.post("/api/v1/ai/chat", headers=h, json={
        "question": "我有几个账户", "model_type": "third_party",
    })
    data = resp.json()["data"]
    assert data["state"] == "model_unavailable", data
    assert "第三方模型未连接" in data["message"]


async def test_analytic_question_goes_to_model_in_auto_mode(client):
    """auto 模式下，分析类问题不走确定性模板。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    resp = await client.post("/api/v1/ai/chat", headers=h, json={
        "question": "帮我分析一下我的消费习惯，有什么可以优化的？",
    })
    data = resp.json()["data"]
    assert data["state"] == "model_unavailable", data
    assert data["trust"]["model_type"] != "deterministic"


async def test_conversation_history_is_passed_to_model(client, monkeypatch):
    """同一会话的追问要带上历史，模型才能接上下文。"""
    from app.services import ai as ai_service

    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    captured: list[list[dict]] = []

    async def fake_model_answer(db, user, question, context, ctx, model_type="auto", history=None):
        captured.append(history or [])
        return {"answer": f"收到：{question}", "model_type": "local", "provider": None}

    monkeypatch.setattr(ai_service, "_model_answer", fake_model_answer)

    r1 = await client.post("/api/v1/ai/conversations", headers=h, json={"context": "脉枢台"})
    conv_id = r1.json()["data"]["id"]
    await client.post("/api/v1/ai/messages", headers=h, json={
        "conversation_id": conv_id, "question": "帮我分析一下这个月的开支", "model_type": "local",
    })
    await client.post("/api/v1/ai/messages", headers=h, json={
        "conversation_id": conv_id, "question": "那上个月呢？", "model_type": "local",
    })

    assert len(captured) == 2
    assert captured[0] == []          # 第一轮无历史
    assert any(m["content"] == "帮我分析一下这个月的开支" for m in captured[1])
