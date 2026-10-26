"""负债「贷款金额 / 剩余金额」与账户按类型汇总的回归测试。

对应 2026-10 的需求：
- 债务表单把「贷款金额」与「剩余金额」分开，存量老贷款可以按当前剩余入账；
- 账户页顶部要按账户类型给出数量与合计，因此 /ledger/summary 需要 by_type。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    ids: dict = {"headers": h, "access": tokens["access"]}
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True,
    })
    ids["a1"] = r.json()["data"]["id"]
    return ids


async def test_liability_opening_outstanding(client):
    """录入存量贷款：贷款金额 100 万，当前剩余 65 万，剩余本金就从 65 万起算。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "存量住房贷款", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "1000000", "opening_outstanding": "650000",
        "interest_rate": "3.6", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "term_months": 300, "payment_day": 5,
    })
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["initial_amount"] == "1000000.00"
    assert data["opening_outstanding"] == "650000.00"
    assert data["outstanding"] == "650000.00"

    # 列表与汇总同样按 65 万计算
    listed = (await client.get("/api/v1/ledger/liabilities", headers=h)).json()["data"]
    assert listed["summary"]["total_outstanding"] == "650000.00"


async def test_account_summary_by_type(client):
    """账户汇总要按类型给出数量与基础币种合计。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "现金", "account_type": "cash", "currency": "CNY",
        "opening_balance": "500", "include_in_net_worth": True,
    })

    summary = (await client.get("/api/v1/ledger/summary", headers=h)).json()["data"]
    accounts = summary["accounts"]
    assert accounts["count"] == 2
    assert accounts["base_currency"] == "CNY"
    assert accounts["base_total"] == "100500.00"

    by_type = {t["account_type"]: t for t in accounts["by_type"]}
    assert by_type["bank"]["count"] == 1
    assert by_type["bank"]["total"] == "100000.00"
    assert by_type["cash"]["count"] == 1
    assert by_type["cash"]["total"] == "500.00"
    assert by_type["bank"]["label"]


async def test_repayment_plan_uses_remaining_periods(client):
    """剩余期数决定计划长度与每期应还：剩余 36 期、总期数 60 期 → 计划从第 25 期开始。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "存量车贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "120000", "opening_outstanding": "60000",
        "term_months": 60, "remaining_periods": 36,
        "interest_rate": "4.8", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 10,
    })
    assert r.json()["success"] is True, r.text
    lid = r.json()["data"]["id"]

    detail = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    plan = detail["repayment_plan"]
    meta = detail["repayment_plan_meta"]
    assert meta["periods"] == 36
    assert meta["total_periods"] == 60
    assert meta["paid_periods"] == 24
    assert len(plan) == 36
    assert plan[0]["period"] == 25
    assert plan[-1]["period"] == 60
    assert plan[0]["status"] == "unpaid"
    assert meta["per_period"] == plan[0]["payment"]
    # 6 万 / 36 期 @4.8% 的等额本息月供约 1793，数量级必须对
    assert 1700 < float(meta["per_period"]) < 1900


async def test_repayment_advances_due_date_and_remaining_periods(client):
    """还款日当天还款后：下次还款日必须推到下一期，而不是停在今天。"""
    from datetime import date, datetime

    ids = await _bootstrap(client)
    h = ids["headers"]
    today = date.today()
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "今天到期的房贷", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "120000", "opening_outstanding": "60000",
        "term_months": 60, "remaining_periods": 36,
        "interest_rate": "4.8", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": today.day,
        "linked_account_id": ids["a1"],
    })
    lid = r.json()["data"]["id"]
    before = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    assert str(before["next_payment_date"]).startswith(today.isoformat())  # 到期日就是今天
    pay = before["repayment_plan_meta"]["per_period"]

    r = await client.post(f"/api/v1/ledger/liabilities/{lid}/repayments", headers=h, json={
        "amount": pay, "account_id": ids["a1"],
    })
    assert r.json()["success"] is True, r.text

    after = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    after_due = datetime.fromisoformat(str(after["next_payment_date"]).replace("Z", "+00:00")).date()
    assert after_due > today, f"下次还款日没有顺延：{after_due}"
    # 顺延一个还款周期（月度 → 下个月同日，月末收敛）
    expected_month = today.month + 1
    expected_year = today.year + (1 if expected_month > 12 else 0)
    expected_month = 1 if expected_month > 12 else expected_month
    assert after_due.year == expected_year and after_due.month == expected_month
    assert after["remaining_periods"] == 35
    assert float(after["outstanding"]) < 60000


async def test_repayment_interest_event_has_expense_category(client):
    """还款利息支出必须落到「还款」分类下，且按债务类型归到具体还款类别。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    # 先建一套房产，房贷必须关联不动产
    house = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "首创悦都45号", "current_value": "3000000",
        "extension": {"property_type": "residence"},
    })).json()["data"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "首创悦都45号", "liability_type": "mortgage", "currency": "CNY",
        "initial_amount": "30000", "opening_outstanding": "28706.17",
        "term_months": 360, "remaining_periods": 222,
        "interest_rate": "3.2", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 20,
        "linked_account_id": ids["a1"], "linked_asset_id": house["id"],
    })
    lid = r.json()["data"]["id"]

    detail = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    pay = detail["repayment_plan_meta"]["per_period"]
    r = await client.post(f"/api/v1/ledger/liabilities/{lid}/repayments", headers=h, json={
        "amount": pay, "account_id": ids["a1"],
    })
    assert r.json()["success"] is True, r.text

    detail = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    events = detail["interest_events"]
    assert events, "登记还款应产生利息支出事件"
    event = events[0]
    assert event["category_id"], "利息支出事件必须有分类"
    # 房贷的利息支出归到「还款 / 房贷还款」
    assert event["category_label"] == "房贷还款"

    cats = (await client.get("/api/v1/settings/categories?domain=expense", headers=h)).json()["data"]
    items = cats if isinstance(cats, list) else cats.get("items", [])
    by_id = {c["id"]: c for c in items}
    interest = by_id[event["category_id"]]
    assert interest["domain"] == "expense"
    parent = by_id[interest["parent_id"]]
    assert parent["name"] == "还款"


async def test_repayment_principal_has_same_category(client):
    """还款本金事件必须与利息同分类，消费分析里才是「一笔房贷还款」而非拆成两笔+未分类。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    house = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "首创悦都45号", "current_value": "3000000",
        "extension": {"property_type": "residence"},
    })).json()["data"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "首创悦都45号", "liability_type": "mortgage", "currency": "CNY",
        "initial_amount": "30000", "opening_outstanding": "28706.17",
        "term_months": 360, "remaining_periods": 222,
        "interest_rate": "3.2", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 20,
        "linked_account_id": ids["a1"], "linked_asset_id": house["id"],
    })
    lid = r.json()["data"]["id"]
    detail = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    pay = detail["repayment_plan_meta"]["per_period"]
    await client.post(f"/api/v1/ledger/liabilities/{lid}/repayments", headers=h, json={
        "amount": pay, "account_id": ids["a1"],
    })

    # 两条事件（本金 liability_repay + 利息 expense）都带分类，且相同
    events = (await client.get("/api/v1/ledger/events", headers=h,
                               params={"page_size": 50})).json()["data"]["items"]
    repay_events = [e for e in events if e.get("repayment")]
    assert len(repay_events) == 2
    assert all(e["category_id"] for e in repay_events), "本金与利息事件都必须有分类"
    cats = {e["category_id"] for e in repay_events}
    assert len(cats) == 1, "本金与利息应归同一个分类"
    # 分类名是「房贷还款」
    cat_ids = (await client.get("/api/v1/settings/categories?domain=expense", headers=h)).json()["data"]
    items = cat_ids if isinstance(cat_ids, list) else cat_ids.get("items", [])
    names = {c["id"]: c["name"] for c in items}
    assert names[next(iter(cats))] == "房贷还款"


async def test_new_household_has_expense_categories(client):
    """新家庭初始化时就带全支出分类（还款、通讯、利息与费用等）。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    resp = await client.get("/api/v1/settings/categories?domain=expense", headers=h)
    data = resp.json()["data"]
    items = data if isinstance(data, list) else data.get("items", [])
    roots = {c["name"]: c for c in items if not c["parent_id"]}
    by_parent = {}
    for c in items:
        by_parent.setdefault(c["parent_id"], set()).add(c["name"])

    assert "还款" in roots
    assert {"房贷还款", "车贷还款", "信用卡还款", "其他贷款还款"} <= by_parent.get(roots["还款"]["id"], set())
    assert "通讯" in roots
    assert "话费月租" in by_parent.get(roots["通讯"]["id"], set())
    assert "利息与费用" in roots
    assert {"贷款利息", "手续费"} <= by_parent.get(roots["利息与费用"]["id"], set())


async def test_repayment_events_share_one_repayment_in_transactions(client):
    """流水里同一笔还款的本金 / 利息两条事件带同一个还款单，供前端合并成一行。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "首创悦都45号", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "30000", "opening_outstanding": "28706.17",
        "term_months": 360, "remaining_periods": 222,
        "interest_rate": "3.2", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 20,
        "linked_account_id": ids["a1"],
    })
    lid = r.json()["data"]["id"]
    detail = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    pay = detail["repayment_plan_meta"]["per_period"]
    r = await client.post(f"/api/v1/ledger/liabilities/{lid}/repayments", headers=h, json={
        "amount": pay, "account_id": ids["a1"],
    })
    assert r.json()["success"] is True, r.text

    items = (await client.get("/api/v1/ledger/events", headers=h,
                              params={"page_size": 50})).json()["data"]["items"]
    linked = [i for i in items if i.get("repayment")]
    assert len(linked) == 2, f"同一笔还款应关联两条事件，实际 {len(linked)}"

    info = linked[0]["repayment"]
    assert linked[1]["repayment"]["id"] == info["id"]
    assert {i["repayment"]["leg"] for i in linked} == {"principal", "interest"}
    assert info["liability_name"] == "首创悦都45号"
    # 拆分合计 = 还款总额
    assert abs(
        float(info["principal"]) + float(info["interest"]) + float(info["fee"]) - float(info["amount"])
    ) < 0.01
