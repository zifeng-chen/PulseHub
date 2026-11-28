"""账单抵扣（抵扣金 / 积分）：减欠款但不计入收支。

边界必须守住：
- 抵扣金额不能超过未还；
- 抵扣后账户欠款同步减少（净资产变化），但支出统计不变；
- 撤销后账单与欠款原样退回。
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.context import CurrentUser
from app.core.ids import new_id
from app.core.timeutil import local_now
from app.db.base import SessionLocal
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.finance import AccountCardBill
from app.db.models.identity import User
from app.services import stats as stats_service
from tests.conftest import auth, complete_setup


async def _setup_card(client, headers):
    r = await client.post("/api/v1/ledger/accounts", headers=headers, json={
        "name": "测试信用卡", "account_type": "bank", "currency": "CNY",
        "credit_limit": "10000", "bill_day": 5, "due_day": 23,
    })
    account = r.json()["data"]

    async with SessionLocal() as db:
        acc = (await db.execute(select(Account).where(Account.id == account["id"]))).scalars().first()
        user_row = (await db.execute(select(User).where(User.username == "admin"))).scalars().first()
        user = CurrentUser(
            user_id=user_row.id, username=user_row.username, display_name=user_row.display_name,
            is_system_admin=True, household_id=acc.household_id, role="admin",
        )
        now = local_now()
        # 一笔 1000 的消费：账户欠款 -1000
        event = BusinessEvent(
            id=new_id(), household_id=acc.household_id,
            event_no=f"EV{new_id()[:8].upper()}", event_type="expense", status="confirmed",
            occurred_at=now, confirmed_at=now,
            amount=Decimal("1000"), currency="CNY",
            original_amount=Decimal("1000"), original_currency="CNY",
            base_currency="CNY", conversion_status="same_currency",
            account_id=acc.id, title="测试消费", source="manual", note="测试",
            version=1, created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(event)
        await db.flush()
        db.add(EventMovement(
            id=new_id(), household_id=acc.household_id, event_id=event.id,
            target_type="account", target_id=acc.id, direction="out",
            amount=Decimal("1000"), currency="CNY",
            created_by=user.user_id, updated_by=user.user_id,
        ))
        # 一张 1000 的账单
        bill = AccountCardBill(
            id=new_id(), household_id=acc.household_id, account_id=acc.id,
            bill_date=now, due_date=now, total_amount=Decimal("1000"),
            paid_amount=Decimal("0"), offset_amount=Decimal("0"), currency="CNY",
        )
        db.add(bill)
        await db.commit()
        return user, acc, bill


async def _stats(db, user):
    snap = await stats_service.dashboard_snapshot(db, user, "current_month")
    return snap


@pytest.mark.asyncio
async def test_offset_reduces_debt_but_not_expense(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    user, acc, bill = await _setup_card(client, h)

    r = await client.post(
        f"/api/v1/ledger/accounts/{acc.id}/card-bills/{bill.id}/offsets",
        headers=h, json={"amount": "500", "offset_type": "credit", "note": "活动抵扣金"},
    )
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["bill"]["offset_amount"] == "500.00"
    assert data["bill"]["unpaid_amount"] == "500.00"

    async with SessionLocal() as db:
        snap = await _stats(db, user)
        # 欠款 1000 → 500（账户余额变成 -500）
        assert snap["net_worth"]["accounts"] == "-500.00", snap["net_worth"]["accounts"]
        # 抵扣不是支出：本期支出仍是 1000
        assert snap["metrics"]["expense"] == "1000.00", snap["metrics"]["expense"]

        ev = (await db.execute(
            select(BusinessEvent).where(BusinessEvent.event_type == "card_offset")
        )).scalars().first()
        assert ev is not None and ev.status == "confirmed"


@pytest.mark.asyncio
async def test_offset_rejects_over_unpaid(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    user, acc, bill = await _setup_card(client, h)

    r = await client.post(
        f"/api/v1/ledger/accounts/{acc.id}/card-bills/{bill.id}/offsets",
        headers=h, json={"amount": "1200", "offset_type": "credit"},
    )
    assert r.json()["success"] is False
    assert "超过未还" in r.text


@pytest.mark.asyncio
async def test_offset_can_be_released(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    user, acc, bill = await _setup_card(client, h)

    r = await client.post(
        f"/api/v1/ledger/accounts/{acc.id}/card-bills/{bill.id}/offsets",
        headers=h, json={"amount": "500", "offset_type": "points", "points": 599},
    )
    offset_id = r.json()["data"]["offset"]["id"]

    r = await client.delete(f"/api/v1/ledger/card-bill-offsets/{offset_id}", headers=h)
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["bill"]["unpaid_amount"] == "1000.00"

    async with SessionLocal() as db:
        snap = await _stats(db, user)
        # 欠款回到 1000，支出仍是 1000
        assert snap["net_worth"]["accounts"] == "-1000.00", snap["net_worth"]["accounts"]
        assert snap["metrics"]["expense"] == "1000.00"
