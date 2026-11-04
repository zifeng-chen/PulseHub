"""台账工作台接口（A+B 版台账总览）冒烟用例。

核心约束：本接口**不返回任何合计金额**，只返回计数/状态/缺口，
避免与脉枢台的净资产、账户余额、资产价值等指标重叠。
"""

from __future__ import annotations

import pytest

from tests.conftest import auth, complete_setup


@pytest.mark.asyncio
async def test_workbench_returns_health_and_queue(client):
    tokens = await complete_setup(client)
    r = await client.get("/api/v1/dashboard/workbench", headers=auth(tokens["access"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    data = body["data"]

    # 五维健康度
    assert len(data["health"]["dims"]) == 5
    assert {d["key"] for d in data["health"]["dims"]} == {
        "accounts", "assets", "liabilities", "investments", "leases",
    }
    assert data["health"]["state"] in {"ok", "warn", "bad"}

    # 待办队列三段
    for bucket in ("today", "week", "later"):
        assert isinstance(data["queue"][bucket], list)
    assert data["queue"]["counts"]["today"] == len(data["queue"]["today"])

    # 账本目录七域
    assert len(data["domains"]) == 7

    # 记账进度
    assert data["recording"]["month_events"] >= 0


@pytest.mark.asyncio
async def test_workbench_has_no_total_amount(client):
    """整页不得出现合计金额字段（与脉枢台分工）。"""
    tokens = await complete_setup(client)
    r = await client.get("/api/v1/dashboard/workbench", headers=auth(tokens["access"]))
    data = r.json()["data"]

    forbidden = {"net_worth", "total_assets", "total_liabilities", "total_value",
                 "market_value", "balance", "base_total", "income", "expense"}
    assert not (forbidden & set(data.keys()))
    for dim in data["health"]["dims"]:
        assert not (forbidden & set(dim.keys()))


@pytest.mark.asyncio
async def test_liquidity_and_debt_ratio_shape(client):
    """脉枢台流动资金 / 生存跑道 / 资产负债率字段齐备（空家庭时为 0 / 无法测算）。"""
    tokens = await complete_setup(client)
    r = await client.get("/api/v1/dashboard/snapshot", headers=auth(tokens["access"]))
    snap = r.json()["data"]

    liq = snap["liquidity"]
    assert liq["value"] == "0.00"
    assert liq["runway_months"] is None
    assert liq["runway_level"] == "unknown"

    nw = snap["net_worth"]
    assert nw["debt_ratio"] == "0.0"
    assert nw["debt_ratio_level"] == "ok"


@pytest.mark.asyncio
async def test_gaps_and_queue_carry_focus_ids(client):
    """待办 / 待补齐必须带出「要处理的那几条」的 id。

    只给页面路径是不够的：跳过去以后一屏几十条，用户还得自己找是哪一条。
    前端用 `?focus=<id,id>` 高亮并滚到目标，这里锁住后端确实把这些 id 给出来。
    """
    from datetime import date

    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    # 缺估值的资产（无 current_value）
    asset = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "vehicle", "name": "旧车", "acquire_cost": "50000",
        "extension": {"brand": "示例牌"},
    })).json()["data"]

    # 缺还款要素的负债：无月供、无还款日
    liability = (await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "亲友借款", "liability_type": "personal", "creditor": "亲友",
        "currency": "CNY", "initial_amount": "20000",
    })).json()["data"]

    # 有还款日、有月供的负债：应出现在「还款提醒」队列里
    due_liability = (await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "装修分期", "liability_type": "personal", "creditor": "某银行",
        "currency": "CNY", "initial_amount": "100000", "monthly_payment": "3000",
        "payment_day": date.today().day, "repayment_method": "equal_installment",
        "payment_frequency": "monthly", "term_months": 36,
    })).json()["data"]

    data = (await client.get("/api/v1/dashboard/workbench", headers=h)).json()["data"]

    gaps = {g["key"]: g for g in data["gaps"]}
    assert asset["id"] in gaps["assets:unvalued"]["focus"].split(",")
    assert liability["id"] in gaps["liabilities:fields"]["focus"].split(",")

    queue = [q for bucket in ("today", "week", "later") for q in data["queue"][bucket]]
    assert queue, "示例数据应至少产生一条待办"
    for item in queue:
        assert isinstance(item.get("focus"), str)

    repayment = [q for q in queue if q["type"] == "repayment"]
    assert any(due_liability["id"] in q["focus"].split(",") for q in repayment), repayment
