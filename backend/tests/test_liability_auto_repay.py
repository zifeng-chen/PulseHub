"""负债到期自动记账：到点生成一笔还款，且不会重复入账。

用户场景：商贷月供由银行自动扣，不想每月手动补登；公积金贷款同样由公积金账户冲还。
开关打开后由进程内定时循环结算，幂等靠「下次还款日顺延」保证。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.due_notify import run_recurring_once
from tests.conftest import auth, complete_setup


@pytest.mark.asyncio
async def test_auto_repay_settles_once_and_advances_due_date(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "还款卡", "account_type": "bank", "currency": "CNY", "opening_balance": "50000",
    })
    account = r.json()["data"]

    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "经营贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "120000", "opening_outstanding": "120000",
        "monthly_payment": "2000", "interest_rate": "4.8",
        "repayment_method": "equal_installment", "term_months": 60,
        "payment_day": 9, "next_payment_date": "2026-11-09",
        "linked_account_id": account["id"], "auto_repay": True,
    })
    assert r.json()["success"] is True, r.text
    liability = r.json()["data"]
    assert liability["auto_repay"] is True

    # 第一轮：到期（今天 2026-11-09）应生成一笔
    first = await run_recurring_once()
    assert first.get("created_repay", 0) >= 1, first

    # 第二轮：还款日已顺延到 12-09，不应重复入账
    second = await run_recurring_once()
    assert second.get("created_repay", 0) == 0, second

    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}/repayments", headers=h)
    items = r.json()["data"]["items"]
    assert len(items) == 1, items
    assert Decimal(str(items[0]["amount"])) == Decimal("2000")

    # 下一次还款日已顺延一期
    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h)
    data = r.json()["data"]
    assert str(data["next_payment_date"]).startswith("2026-12-09"), data["next_payment_date"]
    assert Decimal(str(data["outstanding"])) < Decimal("120000")


@pytest.mark.asyncio
async def test_auto_repay_skipped_without_account(client):
    """没配扣款账户时只提醒不记账，避免把钱记到不存在的账户上。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "无账户贷款", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "10000", "opening_outstanding": "10000",
        "monthly_payment": "500", "interest_rate": "3.6",
        "repayment_method": "equal_installment", "term_months": 24,
        "payment_day": 9, "next_payment_date": "2026-11-09",
        "auto_repay": True,
    })
    liability = r.json()["data"]

    result = await run_recurring_once()
    assert result.get("created_repay", 0) == 0, result

    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}/repayments", headers=h)
    assert r.json()["data"]["items"] == []
