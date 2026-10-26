"""12 §3 固定样例数据集与人工期望值（对账回归）。

建立一组固定的多成员/多币种样例数据（工资、消费、转账、资产估值、负债、异常数据），
并对核心统计（收入、支出、账户余额、资产、负债、净资产、待估值、未分类）断言人工期望值。
"""
from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _confirm(client, headers, event_id: str, version: int) -> None:
    r = await client.post(f"/api/v1/ledger/events/{event_id}/confirm", headers=headers,
                          json={"version": version})
    assert r.json()["success"] is True, r.text


async def _setup_sample(client) -> dict:
    """固定样例数据集。返回各对象 id 与 headers。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    ids: dict = {"headers": h}

    # 账户（期初）
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True})
    ids["a_salary"] = r.json()["data"]["id"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "现金", "account_type": "cash", "currency": "CNY",
        "opening_balance": "2000", "include_in_net_worth": True})
    ids["a_cash"] = r.json()["data"]["id"]

    # 收入：工资 20000
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "工资", "amount": "20000",
        "account_id": ids["a_salary"], "source": "manual"})
    await _confirm(client, h, r.json()["data"]["id"], r.json()["data"]["version"])

    # 支出：餐饮 500、交通 300、未分类 100（异常数据）
    for title, amt in (("餐饮", "500"), ("交通", "300"), ("未分类杂项", "100")):
        r = await client.post("/api/v1/ledger/events", headers=h, json={
            "event_type": "expense", "title": title, "amount": amt,
            "account_id": ids["a_salary"], "source": "manual"})
        await _confirm(client, h, r.json()["data"]["id"], r.json()["data"]["version"])

    # 内部转账：工资卡 → 现金 1000
    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": ids["a_salary"], "to_account_id": ids["a_cash"], "amount": "1000"})
    assert r.json()["success"] is True, r.text

    # 资产：房产（成本 2000000，估值 2100000）
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "自住房", "currency": "CNY",
        "acquire_cost": "2000000", "current_value": "2100000", "value_source": "appraisal",
        "extension": {"property_type": "residence"}})
    assert r.json()["success"] is True, r.text
    ids["a_house"] = r.json()["data"]["id"]

    # 资产：缺估值的其他资产（异常数据，不计入净资产）
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "缺估值设备", "currency": "CNY"})
    assert r.json()["success"] is True, r.text

    # 负债：普通负债 800000
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "亲友借款", "liability_type": "personal", "creditor": "亲友",
        "currency": "CNY", "initial_amount": "800000"})
    assert r.json()["success"] is True, r.text

    return ids


async def test_sample_dataset_expectations(client):
    """12 §3：固定样例数据对账——人工期望值与统计结果一致。"""
    ids = await _setup_sample(client)
    h = ids["headers"]

    r = await client.get("/api/v1/ledger/summary", headers=h)
    data = r.json()["data"]

    # 收入 = 工资 20000
    assert data["metrics"]["income"] == "20000.00"
    # 支出 = 餐饮 500 + 交通 300 + 未分类 100
    assert data["metrics"]["expense"] == "900.00"

    nw = data["net_worth"]
    # 账户余额：工资卡 100000+20000-500-300-100-1000=118100；现金 2000+1000=3000
    assert nw["accounts"] == "121100.00"
    # 资产净资产：仅房产 2100000（缺估值资产不计入）
    assert nw["assets"] == "2100000.00"
    # 负债剩余本金
    assert nw["total_liabilities"] == "800000.00"
    # 净资产 = 账户 + 资产 − 负债 = 121100 + 2100000 − 800000
    assert nw["net_worth"] == "1421100.00"

    # 资产统计：1 项待估值（缺估值设备）
    r = await client.get("/api/v1/ledger/assets/summary", headers=h)
    summary = r.json()["data"]
    assert summary["count"] == 2
    assert summary["pending_valuation_count"] == 1

    # 数据质量：未分类流水 1 笔
    r = await client.get("/api/v1/insights/data-quality", headers=h)
    dq = r.json()["data"]
    titles = [i["title"] for i in dq.get("items", [])]
    assert "未分类流水" in titles
