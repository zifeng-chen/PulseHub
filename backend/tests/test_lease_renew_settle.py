"""续租与退租结算测试。

覆盖：续租生成新租约并让旧租约转已到期；退租结算按「押金 − 水电煤 − 破损赔偿」算应退，
押金入账时生成退款流水，租约终止且详情能查到结算记录。
"""

from __future__ import annotations

from tests.test_m4_leases_reconciliation import (
    _bootstrap,
    _create_lease,
    _create_real_estate,
)


async def _prepare(client):
    ids = await _bootstrap(client)
    asset = await _create_real_estate(client, ids["headers"])
    lease = await _create_lease(client, ids["headers"], asset["id"])
    return ids, asset, lease


async def test_renew_creates_new_lease_and_expires_old(client):
    ids, _asset, lease = await _prepare(client)
    h = ids["headers"]

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/renew", headers=h, json={
        "start_date": "2027-01-02",
        "end_date": "2028-01-01",
        "monthly_rent": "5600",
        "deposit_amount": "5000",
    })
    assert r.json()["success"] is True, r.text
    new = r.json()["data"]
    assert new["previous_lease_id"] == lease["id"]
    assert new["status"] == "active"
    assert new["monthly_rent"] == "5600.00"
    assert new["start_date"].startswith("2027-01-02")

    old = await client.get(f"/api/v1/ledger/leases/{lease['id']}", headers=h)
    assert old.json()["data"]["status"] == "expired"


async def test_settle_deducts_utility_and_damage(client):
    ids, _asset, lease = await _prepare(client)
    h = ids["headers"]

    # 押金先入账，结算时才有退款流水
    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/deposits/receive", headers=h, json={
        "account_id": ids["a1"], "amount": "5000",
    })
    assert r.json()["success"] is True, r.text

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/settle", headers=h, json={
        "refund_account_id": ids["a1"],
        "items": [
            {"item_type": "utility", "label": "电费", "amount": "200"},
            {"item_type": "damage", "label": "沙发破损", "amount": "300", "note": "已确认"},
        ],
    })
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["utility_total"] == "200.00"
    assert data["damage_total"] == "300.00"
    assert data["deduction_total"] == "500.00"
    assert data["refund_amount"] == "4500.00"
    assert len(data["items"]) == 2

    detail = await client.get(f"/api/v1/ledger/leases/{lease['id']}", headers=h)
    body = detail.json()["data"]
    assert body["status"] == "terminated"
    assert len(body["settlements"]) == 1
    assert body["settlements"][0]["refund_amount"] == "4500.00"


async def test_settle_without_deposit_records_only(client):
    """只勾选过「已收押金」、没登记到账时，结算不动账户，也不会报错。"""
    ids, _asset, lease = await _prepare(client)
    h = ids["headers"]
    r = await client.put(f"/api/v1/ledger/leases/{lease['id']}", headers=h, json={
        "deposit_received": True,
        "version": lease["version"],
    })
    assert r.json()["success"] is True, r.text

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/settle", headers=h, json={
        "items": [{"item_type": "damage", "label": "墙面污损", "amount": "800"}],
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["refund_amount"] == "4200.00"
