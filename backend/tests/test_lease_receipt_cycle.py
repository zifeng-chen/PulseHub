"""实收按付款周期计算、自动核销与下一期、押金只能收一次。"""

from __future__ import annotations

from tests.test_m4_leases_reconciliation import (
    _bootstrap,
    _create_lease,
    _create_real_estate,
)


async def _prepare(client, **kw):
    ids = await _bootstrap(client)
    asset = await _create_real_estate(client, ids["headers"])
    lease = await _create_lease(client, ids["headers"], asset["id"], receiving_account_id=ids["a1"], **kw)
    return ids, lease


async def test_receipt_amount_follows_payment_frequency(client):
    """按季付：月租 5000，实收应是 15000 而不是 5000。"""
    ids, lease = await _prepare(client, payment_frequency="quarterly")
    h = ids["headers"]

    preview = await client.get(
        f"/api/v1/ledger/leases/{lease['id']}/receipt-preview", headers=h,
        params={"occurred_at": "2026-04-05"},
    )
    assert preview.json()["success"] is True, preview.text
    data = preview.json()["data"]
    assert data["amount"] == "15000.00"
    # 租期从 2026-01-01 起按季付：04-01、07-01…，到账日 04-05 之后应当是 07-01
    assert data["next_due_date"].startswith("2026-07-01")

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/receipts", headers=h, json={
        "account_id": ids["a1"], "occurred_at": "2026-04-05T00:00:00",
    })
    assert r.json()["success"] is True, r.text


async def test_receipt_auto_settles_receivable_and_creates_next(client):
    """没收租计划时收一笔，自动核销最早的待收，并按周期补下一期。"""
    ids, lease = await _prepare(client)
    h = ids["headers"]

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/receivables", headers=h, json={
        "amount": "5000", "due_date": "2026-06-01",
    })
    assert r.json()["success"] is True, r.text
    first = r.json()["data"]["id"]

    preview = await client.get(
        f"/api/v1/ledger/leases/{lease['id']}/receipt-preview", headers=h,
        params={"occurred_at": "2026-06-03"},
    )
    # 有应收时以下一期应付日为基准，不受晚几天到账影响
    assert preview.json()["data"]["next_due_date"].startswith("2026-07-01")
    assert preview.json()["data"]["receivable_id"] == first

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/receipts", headers=h, json={
        "account_id": ids["a1"], "occurred_at": "2026-06-03T00:00:00",
    })
    assert r.json()["success"] is True, r.text

    detail = (await client.get(f"/api/v1/ledger/leases/{lease['id']}", headers=h)).json()["data"]
    statuses = {r["id"]: r["status"] for r in detail["receivables"]}
    assert statuses[first] == "paid"
    upcoming = [r for r in detail["receivables"] if r["status"] == "pending"]
    assert len(upcoming) == 1
    assert upcoming[0]["due_date"].startswith("2026-07-01")
    assert detail["next_receipt_date"].startswith("2026-07-01")
    # 实收页签只放租金到账，应收计划不再重复出现
    assert all("应收" not in (e["title"] or "") for e in detail["events"])


async def test_deposit_can_only_be_received_once(client):
    ids, lease = await _prepare(client)
    h = ids["headers"]

    first = await client.post(f"/api/v1/ledger/leases/{lease['id']}/deposits/receive", headers=h, json={
        "account_id": ids["a1"], "amount": "5000",
    })
    assert first.json()["success"] is True, first.text

    second = await client.post(f"/api/v1/ledger/leases/{lease['id']}/deposits/receive", headers=h, json={
        "account_id": ids["a1"], "amount": "5000",
    })
    assert second.status_code == 409, second.text
    assert "不能重复" in second.json()["error"]["message"]

    detail = (await client.get(f"/api/v1/ledger/leases/{lease['id']}", headers=h)).json()["data"]
    assert detail["deposit_received"] is True
    assert len(detail["deposits"]) == 1
    assert detail["deposit_summary"]["held"] == "5000.00"
