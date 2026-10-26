"""防重复提交（10 §6、11 §4）：同一幂等键重复到达只创建一次。

场景：网络慢时用户双击「保存」，或前端超时重试——两次请求内容一致，
第二次必须返回同一条记录，而不是再记一笔。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True,
    })
    return {"headers": h, "a1": r.json()["data"]["id"]}


async def test_event_create_is_idempotent(client):
    """事件创建：同一幂等键重复提交只生成一条，且返回同一条。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    body = {
        "event_type": "expense", "title": "咖啡", "amount": "38",
        "occurred_at": "2026-10-20T09:00:00", "account_id": ids["a1"],
        "source": "manual", "idempotency_key": "双击测试-001",
    }
    first = (await client.post("/api/v1/ledger/events", headers=h, json=body)).json()
    assert first["success"] is True
    second = (await client.post("/api/v1/ledger/events", headers=h, json=body)).json()
    assert second["success"] is True
    assert second["data"]["id"] == first["data"]["id"], "重复提交应返回同一条记录"
    assert "已处理过" in second["message"]

    items = (await client.get("/api/v1/ledger/events", headers=h)).json()["data"]["items"]
    assert len([i for i in items if i["title"] == "咖啡"]) == 1, "不应生成第二条"


async def test_event_idempotency_key_conflict(client):
    """同一幂等键但内容不同 → 409，避免前端误复用键覆盖真实意图。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    base = {"event_type": "expense", "account_id": ids["a1"], "source": "manual",
            "idempotency_key": "复用键-001"}
    r1 = await client.post("/api/v1/ledger/events", headers=h,
                           json={**base, "title": "咖啡", "amount": "38"})
    assert r1.json()["success"] is True
    r2 = await client.post("/api/v1/ledger/events", headers=h,
                           json={**base, "title": "拿铁", "amount": "42"})
    assert r2.status_code == 409, r2.text


async def test_event_without_key_still_creates_two(client):
    """不传幂等键时保持原行为：两次提交就是两笔（同一杯咖啡记两次是用户自由）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    body = {"event_type": "expense", "title": "咖啡", "amount": "38",
            "account_id": ids["a1"], "source": "manual"}
    a = (await client.post("/api/v1/ledger/events", headers=h, json=body)).json()["data"]["id"]
    b = (await client.post("/api/v1/ledger/events", headers=h, json=body)).json()["data"]["id"]
    assert a != b


async def test_transfer_and_repayment_idempotent(client):
    """转账与负债还款同样受幂等保护。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "现金", "account_type": "cash", "currency": "CNY",
        "opening_balance": "1000", "include_in_net_worth": True})
    a2 = r.json()["data"]["id"]

    t_body = {"from_account_id": ids["a1"], "to_account_id": a2, "amount": "200",
              "occurred_at": "2026-10-20T10:00:00", "idempotency_key": "转账-幂等-001"}
    t1 = (await client.post("/api/v1/ledger/transfers", headers=h, json=t_body)).json()
    t2 = (await client.post("/api/v1/ledger/transfers", headers=h, json=t_body)).json()
    assert t1["data"]["id"] == t2["data"]["id"]
    rows = (await client.get("/api/v1/ledger/transfers", headers=h)).json()["data"]
    assert rows["total"] == 1

    li = (await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "消费贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "50000", "opening_outstanding": "40000",
        "term_months": 24, "remaining_periods": 18, "interest_rate": "4.5",
        "repayment_method": "equal_installment", "payment_frequency": "monthly",
        "payment_day": 10, "linked_account_id": ids["a1"],
    })).json()["data"]
    p_body = {"amount": "1000", "account_id": ids["a1"],
              "occurred_at": "2026-10-20T11:00:00", "idempotency_key": "还款-幂等-001"}
    p1 = (await client.post(f"/api/v1/ledger/liabilities/{li['id']}/repayments",
                            headers=h, json=p_body)).json()
    p2 = (await client.post(f"/api/v1/ledger/liabilities/{li['id']}/repayments",
                            headers=h, json=p_body)).json()
    assert p1["data"]["id"] == p2["data"]["id"], p2
    detail = (await client.get(f"/api/v1/ledger/liabilities/{li['id']}", headers=h)).json()["data"]
    assert len(detail["repayments"]) == 1
