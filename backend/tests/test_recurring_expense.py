"""周期固定支出（话费月租等）的回归测试。"""

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


async def test_create_and_list_recurring_expense(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/recurring-expenses", headers=h, json={
        "title": "话费月租", "amount": "58", "frequency": "monthly",
        "day_of_month": 15, "account_id": ids["a1"], "currency": "CNY",
    })
    assert r.json()["success"] is True, r.text
    item = r.json()["data"]
    assert item["title"] == "话费月租"
    assert item["amount"] == "58.00"
    assert item["frequency_label"] == "按月"
    assert item["status"] == "active"
    assert item["next_due_date"], "新建时应自动推算下一个到期日"

    rows = (await client.get("/api/v1/ledger/recurring-expenses", headers=h)).json()["data"]["items"]
    assert any(x["id"] == item["id"] for x in rows)


async def test_recurring_expense_settles_and_advances(client):
    from datetime import timedelta
    from app.core.timeutil import local_now, local_iso

    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/recurring-expenses", headers=h, json={
        "title": "话费月租", "amount": "58", "frequency": "monthly",
        "day_of_month": 15, "account_id": ids["a1"],
    })
    item = r.json()["data"]

    # 把到期日改到过去，模拟到期
    past = (local_now() - timedelta(days=1)).isoformat()
    r = await client.put(f"/api/v1/ledger/recurring-expenses/{item['id']}", headers=h,
                         json={"next_due_date": past})
    assert r.json()["success"] is True, r.text

    from app.services import recurring as recurring_service
    from app.db.base import SessionLocal
    from app.core.context import CurrentUser

    async with SessionLocal() as db:
        # 直接调结算（定时循环在测试环境不启动）
        from app.services import due_notify
        # 找一个 user 对象
        from sqlalchemy import select
        from app.db.models.identity import HouseholdMember, User
        member = (await db.execute(select(HouseholdMember).where(HouseholdMember.status == "active")
                                   .order_by(HouseholdMember.joined_at))).scalars().first()
        user_row = (await db.execute(select(User).where(User.id == member.user_id))).scalars().first()
        user = CurrentUser(user_id=user_row.id, username=user_row.username,
                           display_name=user_row.display_name, is_system_admin=True,
                           household_id=member.household_id, role=member.role)
        created = await recurring_service.settle_due(db, user)
        assert created == 1, f"应结算 1 笔，实际 {created}"
        await db.commit()

    # 重新读取：到期日应推进到下个月，状态仍 active
    rows = (await client.get("/api/v1/ledger/recurring-expenses", headers=h)).json()["data"]["items"]
    updated = next(x for x in rows if x["id"] == item["id"])
    from datetime import datetime
    next_due = datetime.fromisoformat(updated["next_due_date"].replace("Z", "+00:00"))
    assert next_due > local_now(), "到期日应推进到未来"

    # 流水里应出现这笔支出
    events = (await client.get("/api/v1/ledger/events", headers=h,
                               params={"page_size": 50})).json()["data"]["items"]
    assert any(e["title"] == "话费月租" and e["event_type"] == "expense" for e in events)


async def test_pause_and_delete_recurring(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/recurring-expenses", headers=h, json={
        "title": "视频会员", "amount": "25", "frequency": "monthly", "day_of_month": 1,
    })
    item = r.json()["data"]

    r = await client.post(f"/api/v1/ledger/recurring-expenses/{item['id']}/pause", headers=h)
    assert r.json()["data"]["status"] == "paused"

    r = await client.post(f"/api/v1/ledger/recurring-expenses/{item['id']}/resume", headers=h)
    assert r.json()["data"]["status"] == "active"

    r = await client.delete(f"/api/v1/ledger/recurring-expenses/{item['id']}", headers=h)
    assert r.json()["success"] is True

    rows = (await client.get("/api/v1/ledger/recurring-expenses", headers=h)).json()["data"]["items"]
    assert all(x["id"] != item["id"] for x in rows)


async def test_recurring_validation(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/recurring-expenses", headers=h, json={
        "title": "", "amount": "0", "frequency": "monthly",
    })
    assert r.json()["success"] is False
    assert "title" in r.json()["error"]["field_errors"]
    assert "amount" in r.json()["error"]["field_errors"]
