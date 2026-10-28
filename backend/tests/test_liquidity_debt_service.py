"""流动资金与偿债覆盖口径（用户明确要求）：

- 流动资金 = 现金类账户 + 投资账户现金，**不扣除**信用卡待还；
- 够不够还，由「偿债覆盖 = 流动资金 ÷（信用卡待还 + 未来 30 天负债应还）」回答；
- 不纳入净资产的账户不进流动资金；信用类账户的溢缴款仍算现金。
"""

from __future__ import annotations

import pytest

from tests.conftest import auth, complete_setup


async def _snapshot(client, headers):
    r = await client.get("/api/v1/dashboard/snapshot", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.asyncio
async def test_liquidity_does_not_deduct_credit_card_due(client):
    """流动资金只算手头现金：信用卡欠款单列为待还，不冲减流动资金。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "招商储蓄卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "10000", "card_last4": "2963",
    })
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "招商信用卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "-3000", "credit_limit": "50000", "card_last4": "0616",
    })
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "现金", "account_type": "cash", "currency": "CNY", "opening_balance": "500",
    })
    # 不纳入净资产的账户不算流动资金（公司的钱不是家里的钱）
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "公司备用金", "account_type": "bank", "currency": "CNY",
        "opening_balance": "2000", "include_in_net_worth": False,
    })
    await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "亲友借款", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "50000", "opening_outstanding": "40000", "monthly_payment": "2000",
    })

    snap = await _snapshot(client, h)
    liq = snap["liquidity"]
    debt = snap["debt_service"]

    # 现金类账户 = 10000 + 500；信用卡 -3000 不进这里（以前会被冲减成 7500）
    assert liq["cash_accounts"] == "10500.00", liq
    assert liq["account_cash"] == "10500.00", liq
    assert liq["value"] == "10500.00", liq

    # 待还单列，覆盖倍数回答「够不够还」
    assert debt["credit_card_due"] == "3000.00", debt
    assert debt["liability_due_30d"] == "2000.00", debt
    assert debt["total_due"] == "5000.00", debt
    assert debt["coverage_value"] == "5500.00", debt
    assert debt["coverage_ratio"] == "2.10", debt
    assert debt["coverage_level"] == "ok", debt

    # 净资产口径不变：信用卡欠款仍作为负项扣减净资产
    nw = snap["net_worth"]
    assert nw["accounts"] == "7500.00", nw  # 10000 - 3000 + 500（公司备用金不计）


@pytest.mark.asyncio
async def test_coverage_level_bad_when_cash_below_due(client):
    """现金少于待还时覆盖不足，但不能把待还从流动资金里扣掉。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "储蓄卡", "account_type": "bank", "currency": "CNY", "opening_balance": "1000",
    })
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "信用卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "-30000", "credit_limit": "50000",
    })

    snap = await _snapshot(client, h)
    liq = snap["liquidity"]
    debt = snap["debt_service"]

    assert liq["value"] == "1000.00", liq
    assert debt["credit_card_due"] == "30000.00", debt
    assert debt["coverage_ratio"] == "0.03", debt
    assert debt["coverage_level"] == "bad", debt
    assert debt["coverage_value"] == "-29000.00", debt


@pytest.mark.asyncio
async def test_no_due_marks_coverage_none(client):
    """没有信用卡待还也没有月供时，覆盖倍数不编造数字。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "储蓄卡", "account_type": "bank", "currency": "CNY", "opening_balance": "5000",
    })

    snap = await _snapshot(client, h)
    debt = snap["debt_service"]
    assert debt["total_due"] == "0.00", debt
    assert debt["coverage_ratio"] is None, debt
    assert debt["coverage_level"] == "none", debt
