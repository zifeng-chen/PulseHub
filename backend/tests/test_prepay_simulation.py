"""提前还款测算（GitHub family-financial-planner 的「提前还贷」模型，落地版）。

覆盖三类关键边界：
1. 正常等额本息：缩短年限 < 原期数、减少月供 < 原月供，且都能省息；
2. 未填剩余期数/贷款年限：拒绝测算（不能拿「展示用默认 12 期」硬算）；
3. 月供 × 期数不足以还清本金（数据不自洽）：拒绝测算并提示核对。
"""

from __future__ import annotations

import pytest

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True,
    })
    return {"headers": h, "account_id": r.json()["data"]["id"]}


async def _create(client, headers, **overrides) -> dict:
    payload = {
        "name": "消费贷", "liability_type": "consumer_loan", "currency": "CNY",
        "initial_amount": "1000000", "opening_outstanding": "800000",
        "interest_rate": "4.9", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 10,
        "term_months": 360, "remaining_periods": 240, "monthly_payment": "5235",
    }
    payload.update(overrides)
    r = await client.post("/api/v1/ledger/liabilities", headers=headers, json=payload)
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


@pytest.mark.asyncio
async def test_prepay_simulation_normal(client):
    ids = await _bootstrap(client)
    liability = await _create(client, ids["headers"])

    r = await client.get(
        f"/api/v1/ledger/liabilities/{liability['id']}/prepay-simulation?amount=100000",
        headers=ids["headers"],
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    assert data["amount"] == "100000.00"
    assert data["new_outstanding"] == "700000.00"
    # 缩短年限：期数变少、月供不变、省息为正
    assert data["shorten_term"]["available"] is True
    assert data["shorten_term"]["periods"] < data["base"]["periods"]
    assert data["shorten_term"]["monthly_payment"] == data["base"]["monthly_payment"]
    assert float(data["shorten_term"]["saved_interest"]) > 0
    # 减少月供：期数不变、月供下降、省息为正
    assert data["reduce_payment"]["available"] is True
    assert data["reduce_payment"]["periods"] == data["base"]["periods"]
    assert float(data["reduce_payment"]["monthly_payment"]) < float(data["base"]["monthly_payment"])
    assert float(data["reduce_payment"]["saved_interest"]) > 0
    assert float(data["reduce_payment"]["monthly_reduction"]) > 0


@pytest.mark.asyncio
async def test_prepay_requires_periods(client):
    """没填剩余期数也没填贷款年限：不能拿默认 12 期硬算。"""
    ids = await _bootstrap(client)
    liability = await _create(client, ids["headers"], term_months=None, remaining_periods=None)

    r = await client.get(
        f"/api/v1/ledger/liabilities/{liability['id']}/prepay-simulation?amount=50000",
        headers=ids["headers"],
    )
    body = r.json()
    assert body["success"] is False
    assert "剩余期数" in (body.get("error", {}).get("message") or "")


@pytest.mark.asyncio
async def test_prepay_rejects_inconsistent_data(client):
    """月供 × 期数 < 剩余本金（如只填了利息）：拒绝测算并提示核对。"""
    ids = await _bootstrap(client)
    liability = await _create(
        client, ids["headers"],
        opening_outstanding="330000", term_months=None, remaining_periods=12,
        monthly_payment="2096.45",
    )

    r = await client.get(
        f"/api/v1/ledger/liabilities/{liability['id']}/prepay-simulation?amount=30000",
        headers=ids["headers"],
    )
    body = r.json()
    assert body["success"] is False
    assert "不自洽" in (body.get("error", {}).get("message") or "")


@pytest.mark.asyncio
async def test_prepay_rejects_overpay(client):
    ids = await _bootstrap(client)
    liability = await _create(client, ids["headers"])

    r = await client.get(
        f"/api/v1/ledger/liabilities/{liability['id']}/prepay-simulation?amount=900000",
        headers=ids["headers"],
    )
    body = r.json()
    assert body["success"] is False
    assert "不能大于剩余本金" in (body.get("error", {}).get("message") or "")
