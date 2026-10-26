"""AI 回答不能再"装傻"：账户类问题必须用账本数据直接回答。

故障现场：问「我有几个账户」，模型答「无法确定账户数量…请登录 FAM 平台或联系客服」。
原因：没有 accounts 话题（落到模型）+ 上下文只喂了 4 条结论 + 提示词没说"你就在系统里"。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


def test_topic_detection_priority():
    from app.services.ai import _detect_topic

    assert _detect_topic("我有几个账户") == "accounts"
    assert _detect_topic("卡里还有多少余额") == "accounts"
    # 专业话题优先于「账户」
    assert _detect_topic("信用卡欠多少") == "liabilities"
    assert _detect_topic("净资产是多少") == "net_worth"


async def test_account_question_answered_from_ledger(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工商储蓄卡", "account_type": "bank", "currency": "CNY", "opening_balance": "12800",
    })
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "招商信用卡", "account_type": "bank", "currency": "CNY",
        "credit_limit": "50000", "credit_used": "3000",
    })

    resp = await client.post("/api/v1/ai/chat", headers=h, json={"question": "我有几个账户？"})
    assert resp.json()["success"] is True, resp.text
    data = resp.json()["data"]

    assert data["state"] == "ready"
    assert data["trust"]["model_type"] == "deterministic"
    assert "2 个账户" in data["answer"], data["answer"]
    assert "工商储蓄卡" in data["answer"]
    assert "招商信用卡" in data["answer"]
    assert "待还" in data["answer"]
    # 禁止「去平台查看 / 联系客服」的话术
    assert "客服" not in data["answer"]
    assert "登录 FAM" not in data["answer"]


async def test_account_question_without_accounts(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    resp = await client.post("/api/v1/ai/chat", headers=h, json={"question": "我有几个账户"})
    data = resp.json()["data"]
    assert data["state"] == "ready"
    assert "还没有登记任何账户" in data["answer"]
