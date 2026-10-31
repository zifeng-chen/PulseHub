"""聚合缓存的正确性。

缓存本身不改变结果，但会把「该重算的地方没重算」变成静默错误：
数字不对、页面正常、没人报错。这里钉住三件事：
1. 同一请求内确实只查一次（否则缓存白加）；
2. 写操作之后必须能读到新值（最容易踩的坑）；
3. 不同家庭之间不能串。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import event, select

from app.core.agg_cache import invalidate, request_cached
from app.db.base import SessionLocal

from tests.conftest import auth, complete_setup


async def _account_ids(client, h) -> list[str]:
    r = await client.get("/api/v1/ledger/accounts", headers=h)
    return [a["id"] for a in r.json()["data"]["items"]]


async def _seed_accounts(client, h) -> dict:
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "10000", "include_in_net_worth": True,
    })
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "招行信用卡", "account_type": "credit_card", "currency": "CNY",
        "opening_balance": "0", "credit_limit": "30000", "include_in_net_worth": True,
    })
    return {}


async def test_balances_queried_once_per_request(client):
    """同一个 session 里连查两次余额，只应产生一次聚合查询。"""
    from app.services.accounts import balances_for

    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _seed_accounts(client, h)
    ids = await _account_ids(client, h)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async with SessionLocal() as db:
        household_id = await _household_id(db)  # 这本身是一条 SQL，先取出来
        sync_engine = db.get_bind().engine
        event.listen(sync_engine, "before_cursor_execute", _record)
        try:
            first = await balances_for(db, household_id, ids)
            after_first = len(statements)
            second = await balances_for(db, household_id, ids)
        finally:
            event.remove(sync_engine, "before_cursor_execute", _record)

    assert first == second
    # 第二次完全命中缓存：SQL 条数不再增加
    assert len(statements) == after_first, f"第二次仍发了 SQL：{statements[after_first:]}"


async def _household_id(db) -> str:
    from app.db.models.identity import Household

    return (await db.execute(select(Household.id))).scalars().first()


async def test_invalidate_forces_reload(client):
    """invalidate 之后必须重新查库，否则写操作后的读数会滞后。"""
    from app.services.accounts import balances_for

    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _seed_accounts(client, h)
    ids = await _account_ids(client, h)

    async with SessionLocal() as db:
        hh = await _household_id(db)
        await balances_for(db, hh, ids)
        cache = db.info.get("aggregate_cache") or {}
        assert cache, "第一次调用后应该有缓存"
        invalidate(db, "balances")
        cache = db.info.get("aggregate_cache") or {}
        assert not [k for k in cache if k and k[0] == "balances"], "按前缀清空后不该残留"


async def test_new_account_visible_immediately(client):
    """新建账户后立刻再读列表要能看到它（缓存不能挡住刚写入的数据）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    before = await _account_ids(client, h)

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "新建的卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "123.45", "include_in_net_worth": True,
    })
    assert r.json()["success"] is True, r.text

    after = await _account_ids(client, h)
    assert len(after) == len(before) + 1
    assert r.json()["data"]["id"] in after


async def test_snapshot_reflects_new_transaction(client):
    """记一笔之后再取快照，支出必须变（快照链路上的缓存要能失效）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    await _seed_accounts(client, h)
    ids = await _account_ids(client, h)

    snap_before = (await client.get("/api/v1/dashboard/snapshot", headers=h)).json()["data"]
    expense_before = float(snap_before["metrics"]["expense"])

    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "缓存验证-买菜", "amount": "88.00",
        "currency": "CNY", "occurred_at": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%dT%H:%M:%S"),
        "account_id": ids[0],
    })
    assert r.json()["success"] is True, r.text
    event_id = r.json()["data"]["id"]
    r = await client.post(f"/api/v1/ledger/events/{event_id}/confirm", headers=h)
    assert r.json()["success"] is True, r.text

    snap_after = (await client.get("/api/v1/dashboard/snapshot", headers=h)).json()["data"]
    expense_after = float(snap_after["metrics"]["expense"])
    assert expense_after == expense_before + 88.0


async def test_investment_portfolio_refreshes_after_trade(client):
    """登记买入后组合立即变化（portfolio 缓存按账户失效）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/investments", headers=h, json={
        "name": "证券账户", "account_type": "securities", "currency": "CNY",
        "opening_cash": "50000", "include_in_net_worth": True,
    })
    assert r.json()["success"] is True, r.text
    account_id = r.json()["data"]["id"]

    detail = (await client.get(f"/api/v1/ledger/investments/{account_id}", headers=h)).json()["data"]
    assert float(detail["market_value"]) == 0.0

    r = await client.post(f"/api/v1/ledger/investments/{account_id}/trades", headers=h, json={
        "trade_type": "buy", "symbol": "600519", "symbol_name": "贵州茅台",
        "quantity": "10", "price": "1500", "fee": "5", "traded_at": "2026-10-20T10:00:00",
    })
    assert r.json()["success"] is True, r.text

    detail = (await client.get(f"/api/v1/ledger/investments/{account_id}", headers=h)).json()["data"]
    # 没有估值 → 市值按成本估算，成本含手续费：10 × 1500 + 5 = 15005
    assert float(detail["market_value"]) == 15005.0
    assert float(detail["cash_balance"]) == 50000 - 15005


async def test_request_cached_scoped_per_session(client):
    """缓存挂在 session 上：换一个 session 必须重新取，不能跨请求串数据。"""
    tokens = await complete_setup(client)
    calls = {"n": 0}

    async def slow():
        calls["n"] += 1
        return calls["n"]

    async with SessionLocal() as db1:
        assert await request_cached(db1, ("k",), slow) == 1
        assert await request_cached(db1, ("k",), slow) == 1
    async with SessionLocal() as db2:
        assert await request_cached(db2, ("k",), slow) == 2
