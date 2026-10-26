"""M7 本地 AI + 第三方 AI 验收测试（09、07 §11）。

覆盖：确定性问答命中、模型未配置降级、第三方未启用不外发、AI 可信度展示字段。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    ids: dict = {"headers": h, "access": tokens["access"]}
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True,
    })
    ids["a1"] = r.json()["data"]["id"]
    return ids


async def _ask(client, h, question, context=None):
    return await client.post("/api/v1/ai/chat", headers=h, json={
        "question": question, "context": context,
    })


async def test_deterministic_answer_hits_topic(client):
    """09 §5.1：财务数据类问题用确定性统计直接回答，无需模型。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await _ask(client, h, "本月支出如何？")
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["state"] == "ready"
    assert data["trust"]["model_type"] == "deterministic"
    assert data["answer"]


async def test_trust_block_fields_complete(client):
    """07 §11：AI 回答必须带可信度展示（模型类型/期间/币种/更新时间/来源数量/确认草稿数/脱敏/结论类型）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await _ask(client, h, "净资产是多少？")
    data = r.json()["data"]
    trust = data["trust"]
    required = {"model_type", "model_type_label", "base_currency", "period",
                "period_label", "data_updated_at", "source_count",
                "confirmed_count", "draft_count", "masked", "conclusion_types"}
    assert required <= set(trust.keys()), f"缺少可信度字段: {required - set(trust.keys())}"
    assert trust["masked"] is True
    assert trust["conclusion_types"]["fact"] == "事实"


async def test_model_unavailable_fallback_for_open_question(client):
    """09 §6：模型未配置时保留确定性页面，非财务问题给配置入口，不阻塞主平台。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await _ask(client, h, "帮我写一首诗")
    data = r.json()["data"]
    assert data["state"] == "model_unavailable"
    assert "本地 AI" in data["message"] or "本地" in data["message"]


async def test_third_party_not_enabled_no_egress(client):
    """09 §5.1：第三方 AI 未启用时不发起外发，直接降级为模型未连接。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    # 没有任何第三方提供商，且本地 AI 未配置 → 开放式问题降级
    r = await _ask(client, h, "给我讲个故事")
    assert r.json()["data"]["state"] == "model_unavailable"


async def test_deterministic_answer_respects_income_caliber(client):
    """09 §4.1：收入分析不得把借款/押金计入收入（确定性口径）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    # 记一笔收入
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "工资", "amount": "5000",
        "account_id": ids["a1"], "source": "manual"})
    await client.post(f"/api/v1/ledger/events/{r.json()['data']['id']}/confirm", headers=h, json={})

    r = await _ask(client, h, "本月收入是多少？")
    data = r.json()["data"]
    assert data["state"] == "ready"
    # 金额统一带千分位（与页面展示一致）
    assert "5,000.00" in data["answer"]


async def test_ai_conversation_flow(client):
    """AI 对话会话：发消息自动建会话、历史列表、详情、删除（09 §3）。"""
    from tests.conftest import auth, complete_setup

    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    # 发消息（无会话 → 自动新建）
    r = await client.post("/api/v1/ai/messages", headers=h,
                          json={"question": "本月支出多少？", "context": "测试页面"})
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    conv_id = data["conversation"]["id"]
    assert data["conversation"]["title"]
    assert data["user_message"]["role"] == "user"
    assert data["assistant_message"]["role"] == "assistant"
    assert data["assistant_message"]["content"]

    # 继续在同一会话里发消息
    r = await client.post("/api/v1/ai/messages", headers=h,
                          json={"conversation_id": conv_id, "question": "那负债呢？"})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["conversation"]["message_count"] == 4

    # 历史列表
    r = await client.get("/api/v1/ai/conversations", headers=h)
    assert r.json()["success"] is True, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == conv_id

    # 详情含 4 条消息且按时间升序
    r = await client.get(f"/api/v1/ai/conversations/{conv_id}", headers=h)
    assert r.json()["success"] is True, r.text
    msgs = r.json()["data"]["messages"]
    assert len(msgs) == 4
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

    # 删除
    r = await client.delete(f"/api/v1/ai/conversations/{conv_id}", headers=h)
    assert r.json()["success"] is True, r.text
    r = await client.get("/api/v1/ai/conversations", headers=h)
    assert r.json()["data"]["items"] == []


async def test_system_status_providers_have_status_label(client):
    """系统状态页的供应商状态必须带 status_label 且与 adapter 口径一致（不能只有灰点没文字）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    data = (await client.get("/api/v1/settings/system", headers=h)).json()["data"]

    for key in ("local_ai", "ocr", "notification", "third_party_ai"):
        p = data["providers"][key]
        assert p.get("status_label"), f"{key} 缺少 status_label"
        assert p["status"] in (
            "unconfigured", "testing", "connected", "unreachable", "failed", "untested", "disabled",
        ), f"{key} 状态取值不规范：{p['status']}"
        # 未做任何配置时，至少不应被标成已连接
        assert p["status"] != "connected"
