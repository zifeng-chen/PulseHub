"""账户型信用卡账单：自动汇总、去重、还款冲减，以及「本期应还」进入偿债覆盖。"""

from __future__ import annotations

import pytest

from tests.conftest import auth, complete_setup


async def _card(client, headers, *, bill_day=5, due_day=23, opening="-5000"):
    r = await client.post("/api/v1/ledger/accounts", headers=headers, json={
        "name": "测试信用卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": opening, "credit_limit": "50000",
        "card_last4": "0616", "bill_day": bill_day, "due_day": due_day,
    })
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _savings(client, headers, amount="20000"):
    r = await client.post("/api/v1/ledger/accounts", headers=headers, json={
        "name": "测试储蓄卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": amount, "card_last4": "2963",
    })
    return r.json()["data"]


async def _expense(client, headers, account_id, amount, occurred_at):
    r = await client.post("/api/v1/ledger/events", headers=headers, json={
        "event_type": "expense", "title": "信用卡消费", "amount": amount,
        "account_id": account_id, "occurred_at": occurred_at,
    })
    assert r.status_code == 200, r.text
    ev = r.json()["data"]
    await client.post(f"/api/v1/ledger/events/{ev['id']}/confirm", headers=headers, json={})
    return ev


@pytest.mark.asyncio
async def test_bill_auto_sums_period_spend_and_is_unique(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    card = await _card(client, h, opening="0")

    await _expense(client, h, card["id"], "3000", "2026-09-10T12:00:00")
    await _expense(client, h, card["id"], "2000", "2026-10-10T12:00:00")  # 下一个周期

    r = await client.post(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h, json={
        "bill_date": "2026-10-05",
    })
    assert r.status_code == 200, r.text
    bill = r.json()["data"]
    # 周期 09-05 ~ 10-05，只算 9 月那笔 3000；到期日 = 10-23
    assert bill["total_amount"] == "3000.00", bill
    assert bill["unpaid_amount"] == "3000.00", bill
    assert bill["due_date"].startswith("2026-10-23"), bill
    assert bill["period_start"].startswith("2026-09-05"), bill

    # 同一期重复生成要挡住
    dup = await client.post(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h, json={
        "bill_date": "2026-10-05",
    })
    assert dup.status_code == 409, dup.text


@pytest.mark.asyncio
async def test_transfer_to_card_reduces_bill_paid_amount(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    card = await _card(client, h, opening="0")
    bank = await _savings(client, h)

    await _expense(client, h, card["id"], "3000", "2026-09-10T12:00:00")
    await client.post(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h,
                      json={"bill_date": "2026-10-05"})

    # 还款 1000：内部转账到信用卡
    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": bank["id"], "to_account_id": card["id"], "amount": "1000",
    })
    assert r.status_code == 200, r.text

    bills = (await client.get(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h)).json()["data"]["items"]
    assert bills[0]["paid_amount"] == "1000.00", bills
    assert bills[0]["unpaid_amount"] == "2000.00", bills

    # 多还的部分不进账单（上限 = 账单金额）
    await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": bank["id"], "to_account_id": card["id"], "amount": "5000",
    })
    bills = (await client.get(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h)).json()["data"]["items"]
    assert bills[0]["paid_amount"] == "3000.00", bills
    assert bills[0]["unpaid_amount"] == "0.00", bills


@pytest.mark.asyncio
async def test_debt_service_uses_current_bill_not_total_outstanding(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    card = await _card(client, h, opening="0")

    await _expense(client, h, card["id"], "3000", "2026-09-10T12:00:00")
    await _expense(client, h, card["id"], "2000", "2026-10-10T12:00:00")  # 未出账

    snap = (await client.get("/api/v1/dashboard/snapshot", headers=h)).json()["data"]
    # 没有账单时按欠款兜底（否则覆盖倍数会虚高）
    assert snap["debt_service"]["credit_card_due"] == "5000.00", snap["debt_service"]
    assert snap["debt_service"]["credit_card_estimated_count"] == 1, snap["debt_service"]

    await client.post(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h,
                      json={"bill_date": "2026-10-05"})
    snap = (await client.get("/api/v1/dashboard/snapshot", headers=h)).json()["data"]
    debt = snap["debt_service"]
    # 本期应还 = 账单 3000；未出账 2000 单列，不进入待还合计
    assert debt["credit_card_due"] == "3000.00", debt
    assert debt["credit_card_outstanding"] == "5000.00", debt
    assert debt["credit_card_unbilled"] == "2000.00", debt
    assert debt["credit_card_billed_count"] == 1, debt
    assert debt["credit_card_estimated_count"] == 0, debt


@pytest.mark.asyncio
async def test_bill_requires_credit_account(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    bank = await _savings(client, h)

    r = await client.post(f"/api/v1/ledger/accounts/{bank['id']}/card-bills", headers=h, json={})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_reversed_transfer_releases_bill_paid_amount(client):
    """冲销还款时账单的已还金额要退回，不能出现「账单已还但钱回来了」。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    card = await _card(client, h, opening="0")
    bank = await _savings(client, h)

    await _expense(client, h, card["id"], "3000", "2026-09-10T12:00:00")
    await client.post(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h,
                      json={"bill_date": "2026-10-05"})
    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": bank["id"], "to_account_id": card["id"], "amount": "1000",
    })
    group_id = r.json()["data"]["id"]

    bills = (await client.get(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h)).json()["data"]["items"]
    assert bills[0]["paid_amount"] == "1000.00", bills

    rev = await client.post(f"/api/v1/ledger/transfers/{group_id}/reverse", headers=h,
                            json={"reason": "测试冲销"})
    assert rev.status_code == 200, rev.text
    bills = (await client.get(f"/api/v1/ledger/accounts/{card['id']}/card-bills", headers=h)).json()["data"]["items"]
    assert bills[0]["paid_amount"] == "0.00", bills
