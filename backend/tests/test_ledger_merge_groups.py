"""流水不重复：一次业务动作只显示一行。

还款会拆成本金/利息两条事件、转账会拆成转出/转入两条——这是数据模型需要，
但列表必须合并成一行，否则同一笔钱看起来记了两次。

这里用「同一次动作的两条事件必须共用 merge_key」来兜底：
以后新增拆分类型时，只要忘了在 attach_business_groups 里给 key，
这个测试就会失败，而不是等用户在流水里看到两行。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import auth, complete_setup


async def _events(client, h) -> list[dict]:
    r = await client.get("/api/v1/ledger/events", headers=h, params={"page_size": 100})
    assert r.json()["success"] is True, r.text
    return r.json()["data"]["items"]


def _assert_single_group(rows: list[dict], *, prefix: str) -> dict:
    """同一业务动作的两段事件必须共用同一个 merge_key。"""
    legs = [r for r in rows if (r.get("merge_key") or "").startswith(prefix)]
    assert len(legs) == 2, f"{prefix} 应有两段事件，实际 {len(legs)}：{[r['title'] for r in legs]}"
    keys = {r.get("merge_key") for r in legs}
    assert len(keys) == 1 and None not in keys, f"{prefix} 的 merge_key 不一致：{keys}"
    return legs[0]


@pytest.mark.asyncio
async def test_transfer_shows_as_one_row(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "余利宝", "account_type": "e_wallet", "currency": "CNY", "opening_balance": "10000",
    })
    src = r.json()["data"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "建行工资卡", "account_type": "bank", "currency": "CNY", "opening_balance": "0",
    })
    dst = r.json()["data"]

    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": src["id"], "to_account_id": dst["id"],
        "amount": "2998.70", "occurred_at": "2026-11-12T10:00:00",
    })
    assert r.json()["success"] is True, r.text

    rows = await _events(client, h)
    legs = _assert_single_group(rows, prefix="transfer:")
    legs = [x for x in rows if (x.get("merge_key") or "").startswith("transfer:")]
    # 合并后的标题应是 A → B，而不是各说一半
    titles = {x.get("merge_title") for x in legs}
    assert titles == {"内部转账 · 余利宝 → 建行工资卡"}, titles


@pytest.mark.asyncio
async def test_normal_repayment_shows_as_one_row(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "还款卡", "account_type": "bank", "currency": "CNY", "opening_balance": "50000",
    })
    account = r.json()["data"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "车贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "100000", "opening_outstanding": "80000",
        "monthly_payment": "2998.67", "interest_rate": "4.8",
        "repayment_method": "equal_installment", "term_months": 36,
    })
    loan = r.json()["data"]

    r = await client.post(f"/api/v1/ledger/liabilities/{loan['id']}/repayments", headers=h, json={
        "amount": "2998.67", "account_id": account["id"], "occurred_at": "2026-11-12T10:00:00",
    })
    assert r.json()["success"] is True, r.text

    rows = await _events(client, h)
    leg = _assert_single_group(rows, prefix="repay:")
    assert leg["merge_title"] == "还款 · 车贷", leg["merge_title"]
    assert leg["merge_direction"] == "expense"
    # 本金 + 利息两条各自的事件类型不同，但必须在同一组
    types = {x["event_type"] for x in rows if x.get("merge_key") == leg["merge_key"]}
    assert types == {"liability_repay", "expense"}, types


@pytest.mark.asyncio
async def test_provident_repayment_shows_as_one_row(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "公积金账户", "account_type": "provident_fund", "currency": "CNY", "opening_balance": "88989",
    })
    pf = r.json()["data"]
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "自住房", "currency": "CNY",
        "current_value": "1200000", "acquire_cost": "1200000",
        "extension": {"property_type": "residence"},
    })
    house = r.json()["data"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "65号", "liability_type": "mortgage", "currency": "CNY",
        "initial_amount": "500000", "opening_outstanding": "317475.23",
        "monthly_payment": "2096.45", "interest_rate": "2.6",
        "repayment_method": "equal_installment", "term_months": 336,
        "linked_asset_id": house["id"],
    })
    loan = r.json()["data"]

    r = await client.post(f"/api/v1/ledger/liabilities/{loan['id']}/repayments", headers=h, json={
        "amount": "2096.45", "account_id": pf["id"], "occurred_at": "2026-11-12T10:00:00",
    })
    assert r.json()["success"] is True, r.text

    rows = await _events(client, h)
    leg = _assert_single_group(rows, prefix="repay:")
    assert leg["merge_title"] == "公积金冲还 · 65号", leg["merge_title"]
    # 冲还不算个人支出，方向必须保持「资金转出」
    assert leg["merge_direction"] == "outflow", leg["merge_direction"]
    assert leg["merge_direction_label"] == "资金转出"


@pytest.mark.asyncio
async def test_no_unmerged_duplicates_in_ledger(client):
    """兜底：同一笔钱不允许出现两行都没有 merge_key 的重复记录。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "A 卡", "account_type": "bank", "currency": "CNY", "opening_balance": "10000",
    })
    a = r.json()["data"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "B 卡", "account_type": "bank", "currency": "CNY", "opening_balance": "0",
    })
    b = r.json()["data"]
    await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a["id"], "to_account_id": b["id"],
        "amount": "1000", "occurred_at": "2026-11-12T09:00:00",
    })

    rows = await _events(client, h)
    loose = [x for x in rows if not x.get("merge_key")]
    seen: dict[tuple, int] = {}
    for row in loose:
        key = (row["event_type"], row["amount"], (row["occurred_at"] or "")[:10])
        seen[key] = seen.get(key, 0) + 1
    dup = {k: v for k, v in seen.items() if v > 1}
    assert not dup, f"存在未合并的重复流水：{dup}"
    assert Decimal("1000") == Decimal(rows[0]["merge_amount"] or "1000")


@pytest.mark.asyncio
async def test_transfer_with_fee_shows_as_one_row(client):
    """带手续费的转账也是「一次动作」：手续费不能单独占一行。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "A 卡", "account_type": "bank", "currency": "CNY", "opening_balance": "10000",
    })
    a = r.json()["data"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "B 卡", "account_type": "bank", "currency": "CNY", "opening_balance": "0",
    })
    b = r.json()["data"]

    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a["id"], "to_account_id": b["id"],
        "amount": "1000", "fee": "2", "occurred_at": "2026-11-12T10:00:00",
    })
    assert r.json()["success"] is True, r.text

    rows = await _events(client, h)
    legs = [x for x in rows if (x.get("merge_key") or "").startswith("transfer:")]
    assert len(legs) == 3, [(x["event_type"], x["title"]) for x in legs]
    assert len({x["merge_key"] for x in legs}) == 1
    base = next(x for x in legs if x.get("merge_primary"))
    assert base["merge_title"].startswith("内部转账 · A 卡 → B 卡"), base["merge_title"]
