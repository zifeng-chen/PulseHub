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
