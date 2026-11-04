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

    # 短问句也不能凭「短」就判定成查数：这些是用户真实问过、曾被统计模板
    # 硬答的问题（问到「资产能坚持多久」，回了一屏资产总额）
    assert not _should_use_deterministic("资产能。坚持多久", "assets", "auto")
    assert not _should_use_deterministic("我的。资产后续可以去上海发展吗", "assets", "auto")
    assert not _should_use_deterministic("下月还款压力如何？", "liabilities", "auto")

    # 真正带查询措辞的短问句，仍然走快路（不必等模型）
    assert _should_use_deterministic("净资产是多少", _detect_topic("净资产是多少"), "auto")
    assert _should_use_deterministic("账户余额", _detect_topic("账户余额"), "auto")

    # 「多少」混在规划问句里时不算查数：问的是「还能撑几年」，不是「现在有多少」
    assert not _should_use_deterministic("我还有多少年能退休", "assets", "auto")
    assert not _should_use_deterministic("按现在的收入还要多少年才能还清房贷", "liabilities", "auto")
    assert not _should_use_deterministic("我手里的钱能撑多少个月", "assets", "auto")


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


async def test_model_unavailable_falls_back_to_deterministic(client):
    """模型没连上时，数据类问题用确定性统计兜底——别让用户只收到一句「未连接」。

    「本月支出如何」这类问题既不是分析题、也没有「多少」这种措辞，
    现在会先交给模型；模型不可用时才回落到统计模板。
    """
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    ev = (await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "买菜", "amount": "88",
        "currency": "CNY", "occurred_at": "2026-11-04T10:00:00",
    })).json()["data"]
    await client.post(f"/api/v1/ledger/events/{ev['id']}/confirm", headers=h,
                      json={"version": ev["version"]})

    resp = await client.post("/api/v1/ai/chat", headers=h, json={"question": "本月支出如何？"})
    data = resp.json()["data"]
    assert data["state"] == "ready", data
    assert data["trust"]["model_type"] == "deterministic"
    assert data["answer"]


async def test_analytic_question_is_not_answered_by_template(client):
    """分析类问题宁可如实说模型没连上，也不拿统计模板答非所问。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    resp = await client.post("/api/v1/ai/chat", headers=h, json={
        "question": "我的资产后续可以去上海发展吗",
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


async def test_analysis_brief_is_forwarded_as_context(client, monkeypatch):
    """从「接着问 AI 助手」进来时，刚生成的分析解读必须作为 context 传给模型。

    否则用户问「那怎么办」，模型完全不知道「那」指的是什么——
    等同于每次都要把结论复述一遍。
    """
    from app.services import ai as ai_service

    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    seen: list[str] = []

    async def fake_model_answer(db, user, question, context, ctx, model_type="auto",
                                history=None, **kwargs):
        seen.append(context or "")
        return {"answer": "好的", "model_type": "local", "provider": None}

    monkeypatch.setattr(ai_service, "_model_answer", fake_model_answer)

    brief = (
        "以下是我刚在「AI 现金流分析」里看到的结果，请基于它回答：\n"
        "结论：手头有18万，钱基本在储蓄卡里\n"
        "要注意：有个月花得比赚得多\n"
        "关键数据：账户余额合计 186420.00 CNY"
    )
    r = await client.post("/api/v1/ai/messages", headers=h, json={
        "question": "那怎么办？", "context": brief, "model_type": "local",
    })
    assert r.json()["success"] is True, r.text
    assert seen, "模型必须被调用"
    assert "手头有18万" in seen[0]
    assert "关键数据" in seen[0]


def test_trim_history_respects_budget():
    """历史按「字符预算」裁剪，而不是只限条数。

    8K 窗口下一条长回答就可能把 prompt 塞满，超出会被 Ollama 静默截断，
    表现为「聊几轮就忘事」。这里锁住三件事：保留最近的、不超预算、至少有最新一条。
    """
    from app.services.ai import (
        HISTORY_CHAR_BUDGET, HISTORY_MAX_MESSAGES, _trim_history,
    )

    # 1) 少量消息原样保留，顺序不变
    small = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert [m["content"] for m in _trim_history(small)] == ["a", "b"]

    # 2) 超过条数上限只留最近 8 条
    many = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    picked = _trim_history(many)
    assert len(picked) == HISTORY_MAX_MESSAGES
    assert picked[-1]["content"] == "m19"

    # 3) 字符预算：8 条各 800 字必然超预算，应收敛到预算内
    heavy = [{"role": "assistant", "content": "字" * 800} for _ in range(8)]
    picked = _trim_history(heavy)
    assert sum(len(m["content"]) for m in picked) <= HISTORY_CHAR_BUDGET
    assert len(picked) >= 1

    # 4) 最新一条即使超预算也要保留（否则这轮就没有上下文了）
    huge = [{"role": "assistant", "content": "字" * 5000}]
    assert len(_trim_history(huge)) == 1

    # 5) 空历史
    assert _trim_history(None) == []
