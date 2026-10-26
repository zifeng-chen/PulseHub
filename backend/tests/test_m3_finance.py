"""M3 负债与信用卡 + 投资验收测试（06 §6/§7/§8、15 §3.2/§5.2、18 §4）。

覆盖：负债剩余本金、还款拆分与调整原因、本金不计支出、利息费用计支出、
借款到账、房贷/车贷关联约束、信用卡额度与账单、还款不重复计消费、
投资账户余额与持仓成本、卖出约束、已实现/未实现损益、手工估值、
净资产并入负债与投资、二次确认令牌。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


async def _create_liability(client, headers, payload: dict) -> dict:
    r = await client.post("/api/v1/ledger/liabilities", headers=headers, json=payload)
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


async def _metrics(client, headers) -> dict:
    r = await client.get("/api/v1/ledger/summary", headers=headers)
    return r.json()["data"]


# ---------------- 负债基础与还款拆分 ----------------

async def test_liability_create_and_outstanding(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    liability = await _create_liability(client, h, {
        "name": "亲友借款", "liability_type": "personal", "creditor": "亲友",
        "currency": "CNY", "initial_amount": "20000", "interest_rate": "12",
        "repayment_method": "equal_installment", "payment_frequency": "monthly",
        "payment_day": 15, "term_months": 12, "monthly_payment": "1800",
        "linked_account_id": ids["a1"],
    })
    assert liability["liability_type_label"] == "亲友借款"
    assert liability["outstanding"] == "20000.00"
    assert liability["status"] == "active"
    assert liability["next_payment_date"] is not None

    r = await client.get("/api/v1/ledger/liabilities", headers=h)
    data = r.json()["data"]
    assert data["summary"]["total_outstanding"] == "20000.00"
    assert data["summary"]["monthly_due"] == "1800.00"
    assert len(data["summary"]["upcoming"]) == 1


async def test_repayment_split_principal_not_expense(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    liability = await _create_liability(client, h, {
        "name": "消费贷", "liability_type": "personal", "initial_amount": "20000",
        "interest_rate": "12", "payment_frequency": "monthly",
    })
    before = await _metrics(client, h)

    # 系统建议利息 = 剩余本金 × 周期利率 = 20000 × 1% = 200；本金 4800
    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments",
                          headers=h, json={"amount": "5000", "account_id": ids["a1"]})
    assert r.json()["success"] is True, r.text
    repayment = r.json()["data"]
    assert repayment["interest"] == "200.00"
    assert repayment["principal"] == "4800.00"

    # 账户减少总额、负债减少本金
    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "95000.00"
    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h)
    assert r.json()["data"]["outstanding"] == "15200.00"

    # 支出增加 本金 4800 + 利息 200（还款本金也是现金流出，计入支出）
    after = await _metrics(client, h)
    delta_expense = float(after["metrics"]["expense"]) - float(before["metrics"]["expense"])
    assert abs(delta_expense - 5000) < 0.01, delta_expense
    assert after["metrics"]["income"] == before["metrics"]["income"]

    # 利息费用明细
    detail = (await client.get(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h)).json()["data"]
    assert detail["interest_summary"]["total_interest"] == "200.00"
    assert len(detail["repayments"]) == 1
    assert len(detail["interest_events"]) == 1


async def test_repayment_adjustment_requires_reason(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    liability = await _create_liability(client, h, {
        "name": "车贷", "liability_type": "personal", "initial_amount": "10000",
        "interest_rate": "6", "payment_frequency": "monthly",
    })
    # 调整拆分为本金 5000 / 利息 0（与建议不一致）但没有原因
    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments",
                          headers=h, json={"amount": "5000", "principal": "5000",
                                           "interest": "0", "account_id": ids["a1"]})
    assert r.status_code == 422
    assert "reason" in r.json()["error"]["field_errors"]

    # 拆分不匹配总额
    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments",
                          headers=h, json={"amount": "5000", "principal": "4000",
                                           "interest": "200", "reason": "手算", "account_id": ids["a1"]})
    assert r.status_code == 422
    assert "amount" in r.json()["error"]["field_errors"]

    # 本金超过剩余本金
    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments",
                          headers=h, json={"amount": "20000", "principal": "20000",
                                           "interest": "0", "reason": "提前结清",
                                           "account_id": ids["a1"]})
    assert r.status_code == 422
    assert "principal" in r.json()["error"]["field_errors"]

    # 带原因的合法调整
    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments",
                          headers=h, json={"amount": "5000", "principal": "5000",
                                           "interest": "0", "reason": "本期免息",
                                           "account_id": ids["a1"]})
    assert r.json()["success"] is True, r.text
    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h)
    assert r.json()["data"]["outstanding"] == "5000.00"


async def test_liability_draw_not_income(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    liability = await _create_liability(client, h, {
        "name": "临时周转", "liability_type": "personal", "initial_amount": "0",
    })
    before = await _metrics(client, h)
    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/draws", headers=h,
                          json={"amount": "30000", "account_id": ids["a1"]})
    assert r.json()["success"] is True, r.text
    assert len(r.json()["data"]["movements"]) == 2

    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "130000.00"
    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h)
    assert r.json()["data"]["outstanding"] == "30000.00"
    after = await _metrics(client, h)
    assert after["metrics"]["income"] == before["metrics"]["income"]  # 借款不算收入


async def test_liability_settle_and_delete_guard(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    liability = await _create_liability(client, h, {
        "name": "小额借款", "liability_type": "personal", "initial_amount": "1000",
        "interest_rate": "0", "payment_frequency": "monthly",
    })
    # 有剩余本金不能删除（即使带令牌）
    r = await client.post("/api/v1/settings/confirm-tokens", headers=h, json={
        "action": "liability.delete", "object_type": "liability", "object_id": liability["id"]})
    token = r.json()["data"]["token"]
    r = await client.delete(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h,
                            params={"reason": "测试", "confirm_token": token})
    assert r.status_code == 409

    # 结清
    r = await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments",
                          headers=h, json={"amount": "1000", "account_id": ids["a1"]})
    assert r.json()["success"] is True, r.text
    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h)
    assert r.json()["data"]["status"] == "settled"
    assert r.json()["data"]["settled"] is True

    # 结清后可软删除
    r = await client.post("/api/v1/settings/confirm-tokens", headers=h, json={
        "action": "liability.delete", "object_type": "liability", "object_id": liability["id"]})
    token = r.json()["data"]["token"]
    r = await client.delete(f"/api/v1/ledger/liabilities/{liability['id']}", headers=h,
                            params={"reason": "已结清归档", "confirm_token": token})
    assert r.json()["success"] is True, r.text


# ---------------- 房贷 / 车贷关联约束 ----------------

async def test_mortgage_must_link_real_estate(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "房贷", "liability_type": "mortgage", "initial_amount": "1000000"})
    assert r.status_code == 422
    assert "linked_asset_id" in r.json()["error"]["field_errors"]

    house = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "自住房", "current_value": "2000000",
        "extension": {"property_type": "residence"},
    })).json()["data"]
    vehicle = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "vehicle", "name": "家用车", "current_value": "150000",
        "extension": {"brand": "示例牌"},
    })).json()["data"]

    # 房贷不能关联车辆
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "错挂房贷", "liability_type": "mortgage", "initial_amount": "1000000",
        "linked_asset_id": vehicle["id"]})
    assert r.status_code == 422
    assert "linked_asset_id" in r.json()["error"]["field_errors"]

    liability = await _create_liability(client, h, {
        "name": "房贷", "liability_type": "mortgage", "creditor": "某银行",
        "initial_amount": "1000000", "interest_rate": "4.2", "rate_type": "lpr_plus",
        "repayment_method": "equal_installment", "term_months": 240,
        "linked_asset_id": house["id"], "linked_account_id": ids["a1"],
        "extension": {"loan_method": "combined", "contract_no": "HT-2026-001",
                      "rate_adjustment_cycle": "yearly",
                      "prepayment_rules": "满一年后可提前还款，无违约金"},
    })
    assert liability["extension"]["contract_no"] == "HT-2026-001"
    # 贷款方式：组合贷，且带中文标签给前端直接展示
    assert liability["extension"]["loan_method"] == "combined"
    assert liability["extension"]["loan_method_label"] == "组合贷款"

    # 资产详情显示房贷摘要（15 §3.2）
    r = await client.get(f"/api/v1/ledger/assets/{house['id']}", headers=h)
    related = r.json()["data"]["related"]["liabilities"]
    assert related["module_enabled"] is True
    assert len(related["items"]) == 1
    assert related["items"][0]["name"] == "房贷"

    # 车贷必须关联车辆
    loan = await _create_liability(client, h, {
        "name": "车贷", "liability_type": "vehicle_loan", "initial_amount": "100000",
        "linked_asset_id": vehicle["id"],
        "extension": {"contract_no": "CAR-2026-01"},
    })
    assert loan["extension"]["contract_no"] == "CAR-2026-01"

    # 还款计划可以测算
    r = await client.get(f"/api/v1/ledger/liabilities/{liability['id']}/repayment-plan", headers=h)
    plan = r.json()["data"]
    assert plan["periods"] == 240
    assert len(plan["schedule"]) == 240
    assert plan["schedule"][0]["principal"] != "0.00"


# ---------------- 信用卡 ----------------

async def test_credit_card_limit_expense_bill_repayment(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "信用卡", "liability_type": "credit_card", "creditor": "某银行",
        "extension": {"credit_limit": "0"}})
    assert r.status_code == 422
    assert "extension.credit_limit" in r.json()["error"]["field_errors"]

    card_liability = await _create_liability(client, h, {
        "name": "信用卡", "liability_type": "credit_card", "creditor": "某银行",
        "extension": {"credit_limit": "50000", "bill_day": 5, "due_day": 25,
                      "card_no": "6222021234567890"},
    })
    assert card_liability["card"]["used_amount"] == "0.00"
    assert card_liability["card"]["available_amount"] == "50000.00"
    assert "*" in card_liability["card"]["card_no"]

    r = await client.get("/api/v1/ledger/credit-cards", headers=h)
    card = r.json()["data"]["items"][0]
    card_id = card["card"]["id"]

    before = await _metrics(client, h)
    # 记一笔消费 1200：不涉及银行账户，已用额度增加
    r = await client.post(f"/api/v1/ledger/credit-cards/{card_id}/expenses", headers=h,
                          json={"amount": "1200", "title": "超市购物"})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["movements"][0]["target_type"] == "liability"

    r = await client.get(f"/api/v1/ledger/credit-cards/{card_id}", headers=h)
    detail = r.json()["data"]
    assert detail["card"]["used_amount"] == "1200.00"
    assert detail["card"]["available_amount"] == "48800.00"
    assert len(detail["activity"]) == 1

    after = await _metrics(client, h)
    delta_expense = float(after["metrics"]["expense"]) - float(before["metrics"]["expense"])
    assert abs(delta_expense - 1200) < 0.01

    # 账单
    due = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
    r = await client.post(f"/api/v1/ledger/credit-cards/{card_id}/bills", headers=h, json={
        "total_amount": "1200", "minimum_payment": "120", "due_date": due,
        "period_start": "2026-10-01", "period_end": "2026-10-31"})
    bill = r.json()["data"]
    assert bill["status"] == "pending"
    assert bill["remaining_amount"] == "1200.00"

    # 还款（无利息）：不重复计消费
    r = await client.post(f"/api/v1/ledger/liabilities/{card_liability['id']}/repayments",
                          headers=h, json={"amount": "1200", "account_id": ids["a1"],
                                           "bill_id": bill["id"]})
    assert r.json()["success"] is True, r.text
    repayment = r.json()["data"]
    assert repayment["interest"] == "0.00"
    assert repayment["principal"] == "1200.00"

    after2 = await _metrics(client, h)
    delta_expense2 = float(after2["metrics"]["expense"]) - float(before["metrics"]["expense"])
    assert abs(delta_expense2 - 1200) < 0.01, "还款不重复计消费"

    r = await client.get(f"/api/v1/ledger/credit-cards/{card_id}", headers=h)
    detail = r.json()["data"]
    assert detail["card"]["used_amount"] == "0.00"
    assert detail["card"]["available_amount"] == "50000.00"
    # 账单状态派生为已还清
    paid = next(b for b in detail["bills"] if b["id"] == bill["id"])
    assert paid["status"] == "paid"
    assert paid["paid_amount"] == "1200.00"


# ---------------- 投资 ----------------

async def test_investment_positions_and_pnl(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/investments", headers=h, json={
        "name": "证券账户", "account_type": "securities", "institution": "某券商",
        "currency": "CNY", "opening_cash": "50000"})
    account = r.json()["data"]
    assert account["cash_balance"] == "50000.00"
    aid = account["id"]

    # 买入 1000 × 10，费用 5
    r = await client.post(f"/api/v1/ledger/investments/{aid}/trades", headers=h, json={
        "trade_type": "buy", "symbol": "000001", "symbol_name": "示例股票",
        "quantity": "1000", "price": "10", "fee": "5"})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["amount"] == "10000.00"

    r = await client.get(f"/api/v1/ledger/investments/{aid}/positions", headers=h)
    positions = r.json()["data"]["items"]
    assert positions[0]["quantity"] == "1000.000000"
    assert positions[0]["cost"] == "10005.00"
    assert positions[0]["market_value_is_estimated"] is True  # 无估值时按成本

    # 手工估值 12 → 市值与未实现损益
    r = await client.post(f"/api/v1/ledger/investments/{aid}/valuations", headers=h, json={
        "symbol": "000001", "price": "12", "source": "manual"})
    assert r.json()["success"] is True, r.text

    r = await client.get(f"/api/v1/ledger/investments/{aid}", headers=h)
    detail = r.json()["data"]
    assert detail["cash_balance"] == "39995.00"
    assert detail["market_value"] == "12000.00"
    assert detail["unrealized_pnl"] == "1995.00"
    assert detail["total_value"] == "51995.00"

    # 卖出 500 × 15，费用 3 → 已实现损益 = 7497 − 500 × 10.005 = 2494.5
    r = await client.post(f"/api/v1/ledger/investments/{aid}/trades", headers=h, json={
        "trade_type": "sell", "symbol": "000001", "quantity": "500", "price": "15", "fee": "3"})
    assert r.json()["success"] is True, r.text
    r = await client.get(f"/api/v1/ledger/investments/{aid}/positions", headers=h)
    position = next(p for p in r.json()["data"]["items"] if p["symbol"] == "000001")
    assert position["quantity"] == "500.000000"
    assert position["avg_cost"] == "10.005000"
    assert position["realized_pnl"] == "2494.50"
    assert position["cost"] == "5002.50"
    assert position["market_value"] == "6000.00"
    assert position["unrealized_pnl"] == "997.50"
    assert position["weight"] == "100.00"

    # 卖出不能超过持仓
    r = await client.post(f"/api/v1/ledger/investments/{aid}/trades", headers=h, json={
        "trade_type": "sell", "symbol": "000001", "quantity": "1000", "price": "15"})
    assert r.status_code == 422
    assert "quantity" in r.json()["error"]["field_errors"]

    # 分红与费用
    r = await client.post(f"/api/v1/ledger/investments/{aid}/trades", headers=h, json={
        "trade_type": "dividend", "symbol": "000001", "amount": "200"})
    assert r.json()["success"] is True
    r = await client.post(f"/api/v1/ledger/investments/{aid}/trades", headers=h, json={
        "trade_type": "fee", "amount": "50", "note": "年度账户管理费"})
    assert r.json()["success"] is True

    r = await client.get(f"/api/v1/ledger/investments/{aid}", headers=h)
    detail = r.json()["data"]
    # 现金 = 50000 − 10005 + 7497 + 200 − 50 = 47642
    assert detail["cash_balance"] == "47642.00"
    assert detail["realized_pnl"] == "2644.50"   # 2494.5 + 分红 200 − 账户级费用 50

    # 删除有持仓的账户被拦截
    r = await client.post("/api/v1/settings/confirm-tokens", headers=h, json={
        "action": "investment.delete", "object_type": "investment_account", "object_id": aid})
    token = r.json()["data"]["token"]
    r = await client.delete(f"/api/v1/ledger/investments/{aid}", headers=h,
                            params={"reason": "测试", "confirm_token": token})
    assert r.status_code == 409


async def test_net_worth_includes_liabilities_and_investments(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    # 资产 100000 + 负债 40000 + 投资市值 30000
    await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "其他资产", "current_value": "100000",
    })
    liability = await _create_liability(client, h, {
        "name": "借款", "liability_type": "personal", "initial_amount": "40000",
    })
    r = await client.post("/api/v1/ledger/investments", headers=h, json={
        "name": "基金账户", "account_type": "fund", "opening_cash": "0"})
    inv_id = r.json()["data"]["id"]
    await client.post(f"/api/v1/ledger/investments/{inv_id}/trades", headers=h, json={
        "trade_type": "buy", "symbol": "F001", "quantity": "3000", "price": "10"})
    await client.post(f"/api/v1/ledger/investments/{inv_id}/valuations", headers=h, json={
        "symbol": "F001", "price": "10"})

    data = await _metrics(client, h)
    nw = data["net_worth"]
    assert nw["accounts"] == "100000.00"
    assert nw["assets"] == "100000.00"
    assert nw["investments"] == "30000.00"
    assert nw["total_liabilities"] == "40000.00"
    assert nw["net_worth"] == "190000.00"      # 100000 + 100000 + 30000 − 40000
    assert nw["included_liabilities"] == 1

    # 关闭「投资估值纳入净资产」开关后不再计入
    r = await client.put("/api/v1/settings/finance", headers=h, json={
        "investment_valuation_in_net_worth": False})
    assert r.json()["success"] is True, r.text
    data = await _metrics(client, h)
    assert data["net_worth"]["investments"] == "0.00"
    assert data["net_worth"]["net_worth"] == "160000.00"

    # 还款后负债减少、净资产回升
    r = await client.put("/api/v1/settings/finance", headers=h, json={
        "investment_valuation_in_net_worth": True})
    assert r.json()["success"] is True
    await client.post(f"/api/v1/ledger/liabilities/{liability['id']}/repayments", headers=h,
                      json={"amount": "40000", "account_id": ids["a1"]})
    data = await _metrics(client, h)
    assert data["net_worth"]["total_liabilities"] == "0.00"


async def test_liability_summary_by_type_and_trend(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _create_liability(client, h, {
        "name": "借款", "liability_type": "personal", "initial_amount": "20000",
    })
    # 房贷缺少关联资产 → 拦截（15 §3.2）
    block = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "房贷", "liability_type": "mortgage", "initial_amount": "800000"})
    assert block.status_code == 422
    assert "linked_asset_id" in block.json()["error"]["field_errors"]

    r = await client.get("/api/v1/ledger/liabilities", headers=h)
    summary = r.json()["data"]["summary"]
    assert summary["count"] >= 1
    assert any(item["liability_type"] == "personal" for item in summary["by_type"])

    r = await client.get("/api/v1/ledger/summary", headers=h)
    charts = r.json()["data"]["charts"]
    assert len(charts["liability_trend"]) == 6
    assert charts["liability_structure"]


async def test_liability_and_investment_category(client):
    """08 §3：负债/投资接入对应分类域，可关联分类、回填标签并按分类筛选。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    lcats = (await client.get("/api/v1/settings/categories?domain=liability", headers=h)).json()["data"]["items"]
    icats = (await client.get("/api/v1/settings/categories?domain=investment", headers=h)).json()["data"]["items"]
    # 信用卡不属于债务，负债域已移除「信用卡」分类
    assert len(lcats) >= 4 and len(icats) >= 5
    assert not any(c["name"] == "信用卡" for c in lcats)
    ltarget = next((c for c in lcats if c["name"] == "其他负债"), lcats[0])
    itarget = next((c for c in icats if c["name"] == "其他投资"), icats[0])

    # 负债关联分类
    liability = await _create_liability(client, h, {
        "name": "消费贷", "liability_type": "personal", "initial_amount": "30000",
        "category_id": ltarget["id"],
    })
    assert liability["category_id"] == ltarget["id"]
    assert liability["category_label"] == ltarget["name"]

    # 投资账户关联分类
    r = await client.post("/api/v1/ledger/investments", headers=h, json={
        "name": "证券账户", "account_type": "securities", "category_id": itarget["id"],
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["category_id"] == itarget["id"]
    assert r.json()["data"]["category_label"] == itarget["name"]

    # 列表按分类筛选
    r = await client.get(f"/api/v1/ledger/liabilities?category_id={ltarget['id']}", headers=h)
    assert any(i["id"] == liability["id"] for i in r.json()["data"]["items"])
    r = await client.get(f"/api/v1/ledger/investments?category_id={itarget['id']}", headers=h)
    assert any(i["category_id"] == itarget["id"] for i in r.json()["data"]["items"])


# ---------------- 负债 ↔ 资产 双向关联 ----------------

async def test_mortgage_syncs_asset_backlink(client):
    """建房贷后，资产侧 must 反指这笔负债：详情「关联负债」不能显示未关联。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    house = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "首创悦都45号", "current_value": "3000000",
        "extension": {"property_type": "residence", "has_mortgage": False},
    })).json()["data"]

    liability = await _create_liability(client, h, {
        "name": "首创悦都45号房贷", "liability_type": "mortgage",
        "initial_amount": "700000", "opening_outstanding": "28706.17",
        "linked_asset_id": house["id"],
    })

    detail = (await client.get(f"/api/v1/ledger/assets/{house['id']}", headers=h)).json()["data"]
    assert detail["linked_liability_id"] == liability["id"], "资产侧没有反指负债"
    assert detail["extension"]["has_mortgage"] is True, "资产上的「是否有房贷」没同步"
    assert detail["related"]["liabilities"]["linked_liability_id"] == liability["id"]


async def test_vehicle_loan_syncs_asset_backlink(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    car = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "vehicle", "name": "家用车", "current_value": "150000",
        "extension": {"brand": "示例牌"},
    })).json()["data"]

    liability = await _create_liability(client, h, {
        "name": "家用车贷款", "liability_type": "vehicle_loan",
        "initial_amount": "100000", "linked_asset_id": car["id"],
    })

    detail = (await client.get(f"/api/v1/ledger/assets/{car['id']}", headers=h)).json()["data"]
    assert detail["linked_liability_id"] == liability["id"]
    assert detail["extension"]["has_loan"] is True


async def test_bank_wm_principal_counts_toward_net_worth(client):
    """只登记本金、没有买入交易的银行理财账户，也要计入投资总额与净资产。

    回归：以前只统计持仓市值，这类账户在总览里一直显示 0。
    """
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/investments", headers=h, json={
        "name": "周利宝", "account_type": "bank_wm", "currency": "CNY",
        "opening_cash": "32597.21"})
    assert r.json()["success"] is True, r.text

    summary = (await client.get("/api/v1/dashboard/snapshot", headers=h)).json()["data"]
    investments = summary["summaries"]["investments"]
    assert investments["market_value"] == "0.00"
    assert investments["cash"] == "32597.21"
    assert investments["total_value"] == "32597.21"
    assert summary["net_worth"]["investments"] == "32597.21"

    names = [item["symbol_name"] for item in summary["charts"]["investment_structure"]]
    assert "现金 / 未投资本金" in names
