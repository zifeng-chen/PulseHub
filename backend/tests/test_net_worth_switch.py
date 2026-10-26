"""净资产开关的全局一致性。

规则：账户、资产、负债、投资都应能单独决定是否纳入家庭净值。
此前只有账户与资产有开关，负债与投资是"一律计入"，导致无法排除公司贷款或代持账户。
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _net_worth(client, h) -> dict:
    return (await client.get("/api/v1/ledger/summary", headers=h,
                             params={"period": "all"})).json()["data"]["net_worth"]


async def test_liability_net_worth_switch(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    base = await _net_worth(client, h)
    assert base["included_liabilities"] == 0

    # 关闭开关：这笔负债不进净值
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json={
        "name": "公司贷款", "liability_type": "personal", "currency": "CNY",
        "initial_amount": "10000", "include_in_net_worth": False,
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["include_in_net_worth"] is False
    liability_id = r.json()["data"]["id"]
    version = r.json()["data"]["version"]

    off = await _net_worth(client, h)
    assert off["included_liabilities"] == 0
    assert off["total_liabilities"] == "0.00"
    assert off["net_worth"] == base["net_worth"]

    # 打开开关：欠款 10000 从净值中扣除
    upd = await client.put(f"/api/v1/ledger/liabilities/{liability_id}", headers=h, json={
        "version": version, "include_in_net_worth": True,
    })
    assert upd.json()["success"] is True, upd.text

    on = await _net_worth(client, h)
    assert on["included_liabilities"] == 1
    assert on["total_liabilities"] == "10000.00"


async def test_investment_net_worth_switch(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    r = await client.post("/api/v1/ledger/investments", headers=h, json={
        "name": "代持账户", "account_type": "securities", "currency": "CNY",
        "opening_cash": "5000", "include_in_net_worth": False,
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["include_in_net_worth"] is False

    off = await _net_worth(client, h)
    assert off["included_investments"] == 0

    inv_id = r.json()["data"]["id"]
    version = r.json()["data"]["version"]
    upd = await client.put(f"/api/v1/ledger/investments/{inv_id}", headers=h, json={
        "version": version, "include_in_net_worth": True,
    })
    assert upd.json()["success"] is True, upd.text

    on = await _net_worth(client, h)
    assert on["included_investments"] == 1
