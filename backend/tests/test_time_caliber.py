"""时间口径回归（北京时间 / 本地墙钟）。

约定（app/core/timeutil.py）：
- 业务时间（occurred_at / acquire_date / due_date / start_date …）存**本地墙钟 naive**，
  对外序列化不带时区后缀；
- 系统时间（created_at / confirmed_at / sent_at …）存 UTC，对外带 +00:00；
- 家庭时区必须与业务时区同一 UTC 偏移，否则跨天/跨月统计会错位。
"""

from __future__ import annotations

import re

from tests.conftest import auth, complete_setup

# 业务时间字段在接口里不得带时区后缀（带了说明按 UTC 序列化，前端会多 8 小时）
_LOCAL_SUFFIX = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True,
    })
    return {"headers": h, "a1": r.json()["data"]["id"]}


def _assert_local_wall_clock(value: str | None, label: str) -> None:
    assert value, f"{label} 不应为空"
    assert not _LOCAL_SUFFIX.search(value), f"{label} 应按本地墙钟序列化（不带时区），实际 {value}"


async def test_business_time_serialized_as_local_wall_clock(client):
    """资产 / 负债 / 租约的业务时间都不带时区后缀。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    asset = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "vehicle", "name": "测试车", "acquire_date": "2026-03-05",
        "current_value": "120000", "extension": {"brand": "大众"},
    })).json()["data"]
    _assert_local_wall_clock(asset["acquire_date"], "asset.acquire_date")
    _assert_local_wall_clock(asset["last_valuation_date"], "asset.last_valuation_date")

    liability = (await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "消费贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "50000", "opening_outstanding": "40000",
        "term_months": 24, "remaining_periods": 18,
        "interest_rate": "4.5", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 10,
        "start_date": "2026-01-10", "linked_account_id": ids["a1"],
    })).json()["data"]
    _assert_local_wall_clock(liability["start_date"], "liability.start_date")
    _assert_local_wall_clock(liability["next_payment_date"], "liability.next_payment_date")

    account = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]
    if account.get("opening_date"):
        _assert_local_wall_clock(account["opening_date"], "account.opening_date")


async def test_system_time_serialized_as_utc(client):
    """系统时间（创建/确认）保留 UTC 后缀，与业务时间区分开。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "时间口径测试", "amount": "10",
        "account_id": ids["a1"], "occurred_at": "2026-10-20T15:30:00", "source": "manual",
    })
    eid = r.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/events/{eid}/confirm", headers=h, json={})
    event = (await client.get(f"/api/v1/ledger/events/{eid}", headers=h)).json()["data"]

    # 业务时间：本地墙钟原样回显
    assert event["occurred_at"] == "2026-10-20T15:30:00"
    # 系统时间：UTC（带 +00:00）
    assert event["created_at"].endswith("+00:00"), event["created_at"]


async def test_asset_acquisition_event_uses_local_wall_clock(client):
    """资产购置事件的发生时间是业务时间：不填时应落在本地当天，而不是 UTC 日期。"""
    from datetime import datetime

    from app.core.timeutil import local_now

    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "购置时间测试", "acquire_cost": "100",
        "current_value": "100", "acquisition": {"mode": "confirm",
                                                "account_id": ids["a1"], "amount": "100"},
    })).json()["data"]

    events = (await client.get("/api/v1/ledger/events", headers=h,
                               params={"page_size": 20})).json()["data"]["items"]
    ev = next(e for e in events if e["event_type"] == "asset_acquire"
              and "购置时间测试" in (e["title"] or ""))
    occurred = datetime.fromisoformat(ev["occurred_at"])
    assert occurred.date() == local_now().date(), f"购置事件日期应为本地今天，实际 {occurred}"


async def test_timezone_must_match_business_offset(client):
    """家庭时区与业务时区偏移不一致（如 UTC）时必须被拒绝，避免统计错位。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.put("/api/v1/settings/household", headers=h, json={"timezone": "UTC"})
    assert r.json()["success"] is False, r.text
    assert "timezone" in r.json()["error"]["field_errors"]

    # 同一偏移的时区可以保存
    ok = await client.put("/api/v1/settings/household", headers=h, json={"timezone": "Asia/Hong_Kong"})
    assert ok.json()["success"] is True, ok.text


# ---------------- 类型显示口径（与时间无关，但同属「统一口径」回归） ----------------

async def test_event_type_shows_flow_direction_and_category(client):
    """列表的「类型」统一按收/支方向返回，还款事件不再出现「还款」这种混杂值。"""
    from app.services.events import flow_direction

    ids = await _bootstrap(client)
    h = ids["headers"]

    # 收入：方向=收入，分类=工资收入
    cats = (await client.get("/api/v1/ledger/categories", headers=h)).json()["data"]["items"]
    salary = next(c for c in cats if c["name"] == "工资收入")
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "工资", "amount": "1000",
        "account_id": ids["a1"], "category_id": salary["id"], "source": "manual",
    })
    await client.post(f"/api/v1/ledger/events/{r.json()['data']['id']}/confirm", headers=h, json={})

    items = (await client.get("/api/v1/ledger/events", headers=h)).json()["data"]["items"]
    income_row = next(i for i in items if i["title"] == "工资")
    assert income_row["flow_direction"] == "income"
    assert income_row["flow_direction_label"] == "收入"
    assert income_row["category_name"] == "工资收入"

    # 还款：方向=支出，分类=房贷还款（不再显示「还款」）
    liability = (await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "测试房贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "600000", "opening_outstanding": "500000",
        "term_months": 360, "remaining_periods": 300, "interest_rate": "3.5",
        "repayment_method": "equal_installment", "payment_frequency": "monthly",
        "payment_day": 15, "linked_account_id": ids["a1"],
    })).json()["data"]
    repay = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments",
                              headers=h, json={
        "amount": "3000", "account_id": ids["a1"], "occurred_at": "2026-10-15T10:00:00",
    })
    assert repay.json()["success"] is True, repay.text
    items = (await client.get("/api/v1/ledger/events", headers=h,
                              params={"page_size": 20})).json()["data"]["items"]
    repay_rows = [i for i in items if i["event_type"] in ("liability_repay", "expense")
                  and i.get("repayment")]
    assert repay_rows, "应能查到还款事件"
    for row in repay_rows:
        assert row["flow_direction"] == "expense"
        assert row["flow_direction_label"] == "支出"

    # 所有事件类型都必须映射到既定方向，不允许出现未覆盖的英文/混杂值。
    # 关键：回款/借款到账/变现这类「钱进来了但不是收入」必须归 inflow，不能标成收入。
    allowed = {"income", "expense", "transfer", "inflow", "outflow", "neutral"}
    for et, expected in [
        ("income", "income"), ("rent_received", "income"),
        ("expense", "expense"), ("refund", "expense"), ("liability_repay", "expense"),
        ("receivable_repay", "inflow"), ("liability_draw", "inflow"),
        ("deposit_receive", "inflow"), ("asset_dispose", "inflow"),
        ("investment_sell", "inflow"),
        ("asset_acquire", "outflow"), ("investment_buy", "outflow"),
        ("deposit_return", "outflow"),
        ("transfer", "transfer"), ("rent_receivable", "neutral"),
    ]:
        direction, label = flow_direction(et)
        assert direction == expected, f"{et} 方向应为 {expected}，实际 {direction}"
        assert direction in allowed and label


# ---------------- 分类层级顺序 ----------------

async def test_categories_ordered_by_tree(client):
    """分类列表必须「一级 → 其子分类 → 下一个一级」，不能父级夹在子级中间。

    之前只按 sort_order 排序，子分类的 sort_order 与父级无关，
    分类管理页出现「餐饮」夹在「早餐/打车」里的乱序，用户看不出层级。
    """
    from tests.conftest import auth, complete_setup

    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    items = (await client.get("/api/v1/settings/categories", headers=h)).json()["data"]["items"]

    for domain in {c["domain"] for c in items}:
        rows = [c for c in items if c["domain"] == domain]
        last_root = None
        for c in rows:
            if not c["parent_id"]:
                last_root = c["id"]
            else:
                assert c["parent_id"] == last_root, (
                    f"{domain} 域分类顺序错乱：{c['name']} 的父级不是紧邻的上一个一级分类"
                )
