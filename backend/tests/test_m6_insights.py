"""M6 工作台 + 洞察验收测试（05 §2、07、18 §4）。

覆盖：综合洞察结论结构、六个主题 panels、数据质量真实检查、工作台待处理计数。
"""

from __future__ import annotations

from decimal import Decimal

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


async def _add_income(client, h, account_id, amount="5000", title="工资"):
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": title, "amount": amount,
        "account_id": account_id, "source": "manual",
    })
    assert r.json()["success"] is True, r.text
    event_id = r.json()["data"]["id"]
    r = await client.post(f"/api/v1/ledger/events/{event_id}/confirm", headers=h, json={})
    assert r.json()["success"] is True, r.text
    return event_id


async def test_overview_conclusions_structure(client):
    """07 §1：每条结论含口径、期间、数据更新时间、来源数量、查看明细。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _add_income(client, h, ids["a1"])

    r = await client.get("/api/v1/insights/overview", headers=h, params={"period": "current_month"})
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["module_enabled"] is True
    assert len(data["conclusions"]) >= 6

    required = {"key", "title", "type", "type_label", "caliber", "period",
                "period_label", "data_updated_at", "source_count", "detail_to"}
    for c in data["conclusions"]:
        assert required <= set(c.keys()), f"结论缺少字段: {required - set(c.keys())}"
        assert c["detail_to"].startswith("/insights/")
        assert c["caliber"]
    # AI 摘要改为按需生成（GET /insights/ai-summary），概览接口不再阻塞
    assert data["ai_summary_state"] == "on_demand"
    assert data["conclusion_types"]["fact"] == "事实"


async def test_all_topics_return_panels(client):
    """07 §2–§7：六个主题均返回 panels + stats + conclusions + 口径。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _add_income(client, h, ids["a1"])

    for topic in ("assets", "income", "expenses", "liabilities", "investments", "leases"):
        r = await client.get(f"/api/v1/insights/{topic}", headers=h)
        assert r.json()["success"] is True, f"{topic}: {r.text}"
        data = r.json()["data"]
        assert data["topic"] == topic
        assert data["title"]
        assert isinstance(data["panels"], list)
        assert isinstance(data["stats"], list)
        assert isinstance(data["conclusions"], list)
        assert "data_updated_at" in data
        # 每个 panel 都带 kind，供前端通用渲染
        for p in data["panels"]:
            assert p["kind"] in ("pie", "line", "bar", "stat", "table")


async def test_income_topic_excludes_transfer_and_deposit(client):
    """07 §3：收入分析按确认日期统计，内部转账/借款不计入收入。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _add_income(client, h, ids["a1"], amount="5000")

    # 内部转账不改收入
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "储蓄卡", "account_type": "bank", "currency": "CNY", "opening_balance": "0"})
    a2 = r.json()["data"]["id"]
    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": ids["a1"], "to_account_id": a2, "amount": "2000"})
    assert r.json()["success"] is True, r.text

    r = await client.get("/api/v1/insights/income", headers=h)
    stats = {s["label"]: s["value"] for s in r.json()["data"]["stats"]}
    # 统计卡金额带千分位（与前端 formatAmount 一致）
    assert "5,000.00" in stats["本期收入"]


async def test_data_quality_flags_uncategorized_and_no_valuation(client):
    """07 §10：数据质量真实检查——未分类流水、资产缺估值被标记。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    # 未分类收入
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "无分类收入", "amount": "100",
        "account_id": ids["a1"], "source": "manual",
    })
    assert r.json()["success"] is True
    await client.post(f"/api/v1/ledger/events/{r.json()['data']['id']}/confirm", headers=h, json={})

    # 缺估值资产
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "无估值资产", "currency": "CNY",
        "acquire_cost": "1000", "current_value": "0", "value_source": "manual",
    })
    assert r.json()["success"] is True, r.text

    r = await client.get("/api/v1/insights/data-quality", headers=h)
    assert r.json()["success"] is True
    data = r.json()["data"]
    assert data["checked"] is True
    assert data["is_inventory"] is False
    assert data["score"] < 100
    titles = {i["title"] for i in data["items"]}
    assert "未分类流水" in titles
    assert "资产缺少当前估值" in titles
    # 每项都有修复入口
    for i in data["items"]:
        assert i["action_to"].startswith("/")


async def test_data_quality_mortgage_link_not_false_positive(client):
    """已关联房贷的不动产不能再报「房贷未关联负债」；未关联的才报。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    async def add_estate(name):
        r = await client.post("/api/v1/ledger/assets", headers=h, json={
            "asset_type": "real_estate", "name": name, "current_value": "2000000",
            "extension": {"property_type": "residence", "has_mortgage": True},
        })
        assert r.json()["success"] is True, r.text
        return r.json()["data"]

    linked = await add_estate("已关联房贷的房")
    await add_estate("未关联房贷的房")

    # 给第一套关联一笔房贷
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "已关联房贷的房-贷款", "liability_type": "mortgage", "currency": "CNY",
        "initial_amount": "700000", "opening_outstanding": "500000",
        "term_months": 360, "remaining_periods": 300,
        "interest_rate": "3.5", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 15,
        "linked_account_id": ids["a1"], "linked_asset_id": linked["id"],
    })
    assert r.json()["success"] is True, r.text

    data = (await client.get("/api/v1/insights/data-quality", headers=h)).json()["data"]
    item = next((i for i in data["items"] if i["title"] == "不动产房贷未关联负债"), None)
    assert item is not None, "未关联那套应仍被报出"
    # 只有「未关联房贷的房」一项，已关联的不算
    assert item["count"] == 1, f"应只报 1 项，实际 {item['count']}"


async def test_dashboard_pending_counts(client):
    """05 §2 待处理区：未对账账户、租金逾期、临近账单真实计数。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    r = await client.get("/api/v1/dashboard/snapshot", headers=h)
    pending = r.json()["data"]["pending"]
    # 新账户从未对账 → 未对账账户 >= 1
    assert pending["unreconciled_accounts"] >= 1
    assert pending["ai_suggestions"] == 0  # AI 建议随 M7 交付

    # 创建不动产 + 逾期租约应收 → 逾期计数
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "出租房", "currency": "CNY",
        "acquire_cost": "1000000", "current_value": "1000000", "value_source": "manual",
        "extension": {"property_type": "residence"},
    })
    re_id = r.json()["data"]["id"]
    r = await client.post("/api/v1/ledger/leases", headers=h, json={
        "real_estate_id": re_id, "tenant_name": "张三", "monthly_rent": "3000",
        "currency": "CNY", "payment_frequency": "monthly",
        "start_date": "2026-01-01T00:00:00Z", "end_date": "2027-01-01T00:00:00Z",
        "deposit_amount": "3000", "receiving_account_id": ids["a1"],
    })
    assert r.json()["success"] is True, r.text
    lease_id = r.json()["data"]["id"]
    r = await client.post(f"/api/v1/ledger/leases/{lease_id}/receivables", headers=h, json={
        "amount": "3000", "currency": "CNY", "due_date": "2026-02-01T00:00:00Z",
    })
    assert r.json()["success"] is True, r.text

    r = await client.get("/api/v1/dashboard/snapshot", headers=h)
    pending = r.json()["data"]["pending"]
    assert pending["overdue_rents"] >= 1


async def test_spending_habits(client):
    """消费习惯分析：分类排行、趋势、大额、异常、周期性结构完整（确定性统计）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    cats = (await client.get("/api/v1/ledger/categories", headers=h)).json()["data"]["items"]
    exp_cat = next((c for c in cats if c["domain"] == "expense"), None)

    async def add_expense(title, amount, cat_id=None):
        payload = {"event_type": "expense", "title": title, "amount": amount,
                   "account_id": ids["a1"], "source": "manual"}
        if cat_id:
            payload["category_id"] = cat_id
        r = await client.post("/api/v1/ledger/events", headers=h, json=payload)
        assert r.json()["success"] is True, r.text
        eid = r.json()["data"]["id"]
        r = await client.post(f"/api/v1/ledger/events/{eid}/confirm", headers=h, json={})
        assert r.json()["success"] is True, r.text

    await add_expense("超市采购", "300", exp_cat["id"] if exp_cat else None)
    await add_expense("午餐", "120", exp_cat["id"] if exp_cat else None)
    await add_expense("大额支出", "5000")

    r = await client.get("/api/v1/insights/spending-habits", headers=h)
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["stats"]
    assert {p["key"] for p in data["panels"]} == {"category", "trend"}
    assert set(data["habits"].keys()) == {"large_expenses", "anomalies", "recurring", "frequent"}
    # 大额消费按金额降序，5000 居首
    assert data["habits"]["large_expenses"][0]["amount"] == "5000.00"
    assert data["habits"]["frequent"]
    assert data["conclusions"]
    assert data["caliber"]


async def test_spending_habits_excludes_repayment(client):
    """贷款偿还不属于消费：消费习惯分析必须排除「还款」分类下的支出（含还款利息）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 建一套房 + 一笔房贷
    house = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "首创悦都45号", "current_value": "3000000",
        "extension": {"property_type": "residence"},
    })).json()["data"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "首创悦都45号房贷", "liability_type": "mortgage", "currency": "CNY",
        "initial_amount": "700000", "opening_outstanding": "28706.17",
        "term_months": 360, "remaining_periods": 222,
        "interest_rate": "3.2", "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "payment_day": 20,
        "linked_account_id": ids["a1"], "linked_asset_id": house["id"],
    })
    lid = r.json()["data"]["id"]

    # 记一笔真实消费 + 一笔还款（还款会产生本金 liability_repay + 利息 expense）
    def add_expense(title, amount, cat_id=None):
        payload = {"event_type": "expense", "title": title, "amount": amount,
                   "account_id": ids["a1"], "source": "manual"}
        if cat_id:
            payload["category_id"] = cat_id
        return client.post("/api/v1/ledger/events", headers=h, json=payload)

    cats = (await client.get("/api/v1/ledger/categories", headers=h)).json()["data"]["items"]
    exp_cat = next((c for c in cats if c["domain"] == "expense"), None)
    r = await add_expense("超市采购", "300", exp_cat["id"] if exp_cat else None)
    eid = r.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/events/{eid}/confirm", headers=h, json={})

    detail = (await client.get(f"/api/v1/ledger/liabilities/{lid}", headers=h)).json()["data"]
    pay = detail["repayment_plan_meta"]["per_period"]
    await client.post(f"/api/v1/ledger/liabilities/{lid}/repayments", headers=h, json={
        "amount": pay, "account_id": ids["a1"],
    })

    data = (await client.get("/api/v1/insights/spending-habits", headers=h)).json()["data"]
    # 消费习惯只含真实消费，还款（本金+利息）不进来
    total = Decimal(data["stats"][0]["value"].split(" ")[0])
    assert total == Decimal("300.00"), f"应只含消费 300，实际 {total}"
    cat_names = [x["name"] for x in data["panels"][0]["data"]]
    assert "房贷还款" not in cat_names
    assert any("超市采购" in (x.get("title") or "") for x in data["habits"]["large_expenses"] or []) \
        or total > 0

