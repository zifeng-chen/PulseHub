"""公积金贷款冲还：月供从公积金账户扣，个人没有实际支出。

三条口径必须同时成立，缺一条就会让报表失真：
1. 公积金账户不计入流动资金（不能随时取）；
2. 从它扣月供时，账户余额与贷款本金照常减少；
3. 这笔还款不进个人收支统计（否则月结余被凭空扣掉一笔）。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import auth, complete_setup


async def _snapshot(client, h):
    r = await client.get("/api/v1/dashboard/snapshot", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _accounts(client, h) -> dict[str, dict]:
    r = await client.get("/api/v1/ledger/accounts", headers=h)
    assert r.json()["success"] is True, r.text
    return {a["name"]: a for a in r.json()["data"]["items"]}


@pytest.mark.asyncio
async def test_provident_fund_repayment_is_not_personal_expense(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "招商储蓄卡", "account_type": "bank", "currency": "CNY", "opening_balance": "20000",
    })
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "公积金账户", "account_type": "provident_fund", "currency": "CNY",
        "opening_balance": "50000",
    })
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "城东自住房", "currency": "CNY",
        "current_value": "1200000", "acquire_cost": "1200000",
        "extension": {"property_type": "residence"},
    })
    assert r.json()["success"] is True, r.text
    house_id = r.json()["data"]["id"]

    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "65号", "liability_type": "mortgage", "currency": "CNY",
        "initial_amount": "500000", "opening_outstanding": "300000",
        "monthly_payment": "2000", "interest_rate": "3.1",
        "linked_asset_id": house_id,
    })
    assert r.json()["success"] is True, r.text
    liability = r.json()["data"]

    before = await _snapshot(client, h)
    # 1) 公积金账户 50000 不在流动资金里，只有银行卡的 20000
    assert before["liquidity"]["cash_accounts"] == "20000.00", before["liquidity"]
    expense_before = Decimal(before["metrics"]["expense"])

    pf = (await _accounts(client, h))["公积金账户"]

    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments", headers=h, json={
        "amount": "2000", "account_id": pf["id"], "occurred_at": "2026-11-08T10:00:00",
    })
    assert r.json()["success"] is True, r.text

    # 2) 扣的是公积金账户，银行卡分文未动
    accs = await _accounts(client, h)
    assert accs["公积金账户"]["balance"] == "48000.00", accs["公积金账户"]
    assert accs["招商储蓄卡"]["balance"] == "20000.00", accs["招商储蓄卡"]

    # 3) 个人支出没有增加
    after = await _snapshot(client, h)
    assert Decimal(after["metrics"]["expense"]) == expense_before, after["metrics"]

    # 事件类型是「公积金冲还」，不是 expense / liability_repay
    r = await client.get("/api/v1/ledger/events", headers=h,
                         params={"event_type": "provident_fund_repay", "page_size": 20})
    items = r.json()["data"]["items"]
    assert items, "公积金冲还应生成独立事件"
    assert all(i["event_type"] == "provident_fund_repay" for i in items)

    # 还款记录仍然挂在负债下，金额可追溯
    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}/repayments", headers=h)
    assert r.json()["success"] is True, r.text
    assert len(r.json()["data"]["items"]) == 1
