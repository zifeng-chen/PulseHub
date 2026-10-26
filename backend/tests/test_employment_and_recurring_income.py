"""周期固定收入 + 工作档案 的测试。

覆盖：
- 固定收入 CRUD + settle_due 自动落收入事件（方向 in）
- 工作档案 CRUD + 每成员唯一
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import auth, complete_setup


@pytest.mark.asyncio
async def test_recurring_income_crud(client: AsyncClient):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    # 建一笔固定收入
    r = await client.post("/api/v1/ledger/recurring-incomes", headers=h, json={
        "title": "工资", "amount": "20000", "frequency": "monthly",
        "day_of_month": 10, "category_id": None, "account_id": None,
    })
    assert r.status_code == 200, r.text
    item = r.json()["data"]
    income_id = item["id"]
    assert item["amount"] == "20000.00"
    assert item["status"] == "active"

    # 列表
    r = await client.get("/api/v1/ledger/recurring-incomes", headers=h)
    assert r.status_code == 200
    assert len(r.json()["data"]["items"]) == 1

    # 编辑
    r = await client.put(f"/api/v1/ledger/recurring-incomes/{income_id}", headers=h, json={"amount": "25000"})
    assert r.status_code == 200
    assert r.json()["data"]["amount"] == "25000.00"

    # 暂停/恢复
    r = await client.post(f"/api/v1/ledger/recurring-incomes/{income_id}/pause", headers=h)
    assert r.json()["data"]["status"] == "paused"
    r = await client.post(f"/api/v1/ledger/recurring-incomes/{income_id}/resume", headers=h)
    assert r.json()["data"]["status"] == "active"

    # 删除
    r = await client.delete(f"/api/v1/ledger/recurring-incomes/{income_id}", headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_settle_due_creates_income_event(client: AsyncClient):
    from datetime import timedelta

    from sqlalchemy import select

    await complete_setup(client)

    from app.core.context import CurrentUser
    from app.core.ids import new_id
    from app.core.timeutil import local_now
    from app.db.base import SessionLocal
    from app.db.models.business import BusinessEvent, RecurringIncome
    from app.db.models.identity import Household, User
    from app.services import recurring_income

    async with SessionLocal() as db:
        household = (await db.execute(select(Household).limit(1))).scalars().first()
        user = (await db.execute(select(User).limit(1))).scalars().first()
        due = local_now() - timedelta(days=1)
        db.add(RecurringIncome(
            id=new_id(), household_id=household.id, title="工资",
            amount=20000, frequency="monthly", day_of_month=10,
            next_due_date=due, status="active",
        ))
        await db.commit()

        cu = CurrentUser(user_id=user.id, username=user.username,
                         display_name=user.display_name or user.username,
                         is_system_admin=True, household_id=household.id)
        created = await recurring_income.settle_due(db, cu)
        await db.commit()
        assert created == 1

        ev = (await db.execute(
            select(BusinessEvent).where(
                BusinessEvent.household_id == household.id,
                BusinessEvent.event_type == "income",
                BusinessEvent.title == "工资",
            )
        )).scalars().first()
        assert ev is not None
        assert ev.status == "confirmed"


@pytest.mark.asyncio
async def test_employment_profile_crud_and_unique(client: AsyncClient):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models.identity import HouseholdMember

    async with SessionLocal() as db:
        member = (await db.execute(select(HouseholdMember).limit(1))).scalars().first()
        member_id = member.id

    # 创建
    r = await client.post("/api/v1/ledger/employment", headers=h, json={
        "member_id": member_id, "employer": "某公司", "employment_status": "employed",
        "social_insurance_base": "10000", "housing_fund_base": "10000",
    })
    assert r.status_code == 200, r.text
    profile = r.json()["data"]
    pid = profile["id"]
    assert profile["employment_status_label"] == "在职"

    # 同一成员再建 → 409
    r = await client.post("/api/v1/ledger/employment", headers=h, json={
        "member_id": member_id, "employer": "另一公司", "employment_status": "employed",
    })
    assert r.status_code == 409

    # 编辑
    r = await client.put(f"/api/v1/ledger/employment/{pid}", headers=h, json={"employment_status": "unemployed"})
    assert r.json()["data"]["employment_status_label"] == "失业"

    # 列表
    r = await client.get("/api/v1/ledger/employment", headers=h)
    assert len(r.json()["data"]["items"]) == 1

    # 删除
    r = await client.delete(f"/api/v1/ledger/employment/{pid}", headers=h)
    assert r.status_code == 200
