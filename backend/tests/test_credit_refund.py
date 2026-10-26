"""信用卡余额口径与退款。

两条业务规则：
1. 信用卡没有「余额」，只有额度与欠款——已用额度必须由余额派生，不能是手工字段；
2. 退款不是收入：它退回原账户、冲减支出统计，并减少信用卡欠款。
"""

from __future__ import annotations

from decimal import Decimal

from tests.conftest import auth, complete_setup


async def _card(client, h, used: str = "10000", limit: str = "50000"):
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "测试信用卡", "account_type": "bank", "currency": "CNY",
        "credit_limit": limit, "credit_used": used,
    })
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


async def test_credit_card_used_is_derived_from_balance(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    card = await _card(client, h, used="10000", limit="50000")
    # 「已用额度」转成期初欠款：余额为 -10000，而不是 0
    assert card["is_credit"] is True
    assert Decimal(card["balance"]) == Decimal("-10000")
    assert card["credit_used"] == "10000.00"
    assert card["available_credit"] == "40000.00"

    # 消费 500 → 欠款 10500
    ev = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "信用卡消费", "amount": "500",
        "account_id": card["id"],
    })
    assert ev.json()["success"] is True, ev.text
    ev_id = ev.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/events/{ev_id}/confirm", headers=h, json={"version": 1})

    detail = (await client.get(f"/api/v1/ledger/accounts/{card['id']}", headers=h)).json()["data"]
    assert detail["credit_used"] == "10500.00"
    assert detail["available_credit"] == "39500.00"


async def test_refund_returns_to_card_and_cannot_exceed(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    card = await _card(client, h, used="0")
    ev = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "信用卡消费 500", "amount": "500",
        "account_id": card["id"],
    })
    ev_id = ev.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/events/{ev_id}/confirm", headers=h, json={"version": 1})

    # 部分退款 200：欠款减少到 300
    ref = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "refund", "title": "退款 200", "amount": "200",
        "account_id": card["id"], "refund_of": ev_id,
    })
    assert ref.json()["success"] is True, ref.text
    assert ref.json()["data"]["refund_of"] == ev_id
    ref_id = ref.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/events/{ref_id}/confirm", headers=h, json={"version": 1})

    detail = (await client.get(f"/api/v1/ledger/accounts/{card['id']}", headers=h)).json()["data"]
    assert detail["credit_used"] == "300.00", detail

    # 再退 400 超过可退 300 → 拒绝
    bad = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "refund", "title": "超额退款", "amount": "400",
        "account_id": card["id"], "refund_of": ev_id,
    })
    assert bad.status_code == 422, bad.text
    assert "amount" in bad.json()["error"]["field_errors"]

    # 可以退剩余 300
    ok = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "refund", "title": "退剩余", "amount": "300",
        "account_id": card["id"], "refund_of": ev_id,
    })
    assert ok.json()["success"] is True, ok.text


async def test_refund_requires_expense_and_same_account(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    card = await _card(client, h, used="0")
    other = (await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "储蓄卡", "account_type": "bank", "currency": "CNY", "opening_balance": "1000",
    })).json()["data"]

    # 对收入退款 → 拒绝
    inc = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "工资", "amount": "1000", "account_id": other["id"],
    })
    inc_id = inc.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/events/{inc_id}/confirm", headers=h, json={"version": 1})

    bad = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "refund", "title": "退工资", "amount": "100",
        "account_id": other["id"], "refund_of": inc_id,
    })
    assert bad.status_code == 422, bad.text

    # 退款账户与原支出不一致 → 拒绝
    exp = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "消费", "amount": "200", "account_id": other["id"],
    })
    exp_id = exp.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/events/{exp_id}/confirm", headers=h, json={"version": 1})

    wrong = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "refund", "title": "退到别的卡", "amount": "100",
        "account_id": card["id"], "refund_of": exp_id,
    })
    assert wrong.status_code == 422, wrong.text
    assert "account_id" in wrong.json()["error"]["field_errors"]

    # 缺少 refund_of → 拒绝
    missing = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "refund", "title": "无来源退款", "amount": "100",
        "account_id": other["id"],
    })
    assert missing.status_code == 422, missing.text
    assert "refund_of" in missing.json()["error"]["field_errors"]
