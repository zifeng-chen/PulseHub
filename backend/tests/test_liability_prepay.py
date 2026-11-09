"""提前还款执行：两种方案都要能真正落账并重排计划。

用户场景：这个月想再还 10 万，选「缩短年限」或「减少月供」，
确认后要入流水，并且后续还款计划跟着变。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import auth, complete_setup


async def _make_loan(client, h, name: str) -> dict:
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": f"{name}-还款卡", "account_type": "bank", "currency": "CNY", "opening_balance": "500000",
    })
    assert r.json()["success"] is True, r.text
    account = r.json()["data"]

    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": name, "liability_type": "personal", "currency": "CNY",
        "initial_amount": "500000", "opening_outstanding": "300000",
        "monthly_payment": "2000", "interest_rate": "3.0",
        "repayment_method": "equal_installment", "term_months": 360,
        "remaining_periods": 300, "payment_day": 9,
        "next_payment_date": "2026-12-09", "linked_account_id": account["id"],
    })
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


@pytest.mark.asyncio
async def test_prepay_shorten_term(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    loan = await _make_loan(client, h, "缩短年限贷")

    r = await client.post(f"/api/v1/ledger/liabilities/{loan['id']}/prepay", headers=h, json={
        "amount": "100000", "strategy": "shorten_term", "occurred_at": "2026-11-09T10:00:00",
    })
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["periods"] < 300, data          # 期数减少
    assert Decimal(str(data["monthly_payment"])) == Decimal("2000")  # 月供不变
    assert Decimal(str(data["new_outstanding"])) == Decimal("200000")

    r = await client.get(f"/api/v1/ledger/liabilities/{loan['id']}", headers=h)
    detail = r.json()["data"]
    assert detail["remaining_periods"] == data["periods"]
    assert Decimal(str(detail["outstanding"])) == Decimal("200000")
    # 还款日不动，还款记录里有一笔本金 10 万
    assert str(detail["next_payment_date"]).startswith("2026-12-09"), detail["next_payment_date"]
    reps = detail["repayments"]
    assert len(reps) == 1
    assert Decimal(str(reps[0]["amount"])) == Decimal("100000")
    assert Decimal(str(reps[0]["interest"])) == Decimal("0")


@pytest.mark.asyncio
async def test_prepay_reduce_payment(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    loan = await _make_loan(client, h, "减少月供贷")

    r = await client.post(f"/api/v1/ledger/liabilities/{loan['id']}/prepay", headers=h, json={
        "amount": "100000", "strategy": "reduce_payment", "occurred_at": "2026-11-09T10:00:00",
    })
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["periods"] == 300, data                      # 期数不变
    assert Decimal(str(data["monthly_payment"])) < Decimal("2000")

    r = await client.get(f"/api/v1/ledger/liabilities/{loan['id']}", headers=h)
    detail = r.json()["data"]
    assert detail["remaining_periods"] == 300
    assert Decimal(str(detail["monthly_payment"])) == Decimal(str(data["monthly_payment"]))
    assert Decimal(str(detail["outstanding"])) == Decimal("200000")


@pytest.mark.asyncio
async def test_prepay_rejects_over_outstanding(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    loan = await _make_loan(client, h, "超额贷")

    r = await client.post(f"/api/v1/ledger/liabilities/{loan['id']}/prepay", headers=h, json={
        "amount": "400000", "strategy": "shorten_term",
    })
    assert r.json()["success"] is False
    assert "剩余本金" in r.json()["error"]["message"]
