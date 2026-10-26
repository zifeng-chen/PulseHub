"""M4 房产租赁 + 对账验收测试（06 §5/§9、15 §3.3、18 §4 顺序 6/7、02 §2）。

覆盖：租约与房产出租联动、应收不改变账户余额、实收计入收入并核销应收、
押金不计收支、逾期派生、终止租约二次确认、对账差额调整事件与标记已核对、
对账不直接覆盖系统余额。
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


async def _create_real_estate(client, headers, name: str = "城东自住房") -> dict:
    r = await client.post("/api/v1/ledger/assets", headers=headers, json={
        "asset_type": "real_estate", "name": name, "currency": "CNY",
        "acquire_cost": "2000000", "current_value": "2500000", "value_source": "manual",
        "extension": {"property_type": "residence", "address": "某市某区某路 1 号"},
    })
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


async def _create_lease(client, headers, real_estate_id: str, **kw) -> dict:
    payload = {
        "real_estate_id": real_estate_id,
        "tenant_name": "张三",
        "monthly_rent": "5000",
        "payment_frequency": "monthly",
        "currency": "CNY",
        "deposit_amount": "5000",
        "receiving_account_id": None,
        "start_date": "2026-01-01",
        "end_date": "2027-01-01",
    }
    payload.update(kw)
    r = await client.post("/api/v1/ledger/leases", headers=headers, json=payload)
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


async def _metrics(client, headers) -> dict:
    r = await client.get("/api/v1/ledger/summary", headers=headers)
    return r.json()["data"]


# ---------------- 租约与房产联动 ----------------

async def test_lease_create_and_property_link(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    re_asset = await _create_real_estate(client, h)
    lease = await _create_lease(client, h, re_asset["id"], receiving_account_id=ids["a1"])

    assert lease["lease_no"].startswith("LS")
    assert lease["real_estate_name"] == "城东自住房"
    assert lease["status"] == "active"
    assert lease["payment_frequency_label"] == "按月"

    # 房产出租摘要联动
    r = await client.get("/api/v1/ledger/assets", headers=h)
    item = next(a for a in r.json()["data"]["items"] if a["id"] == re_asset["id"])
    ext = item["extension"]
    assert ext["has_lease"] is True

    # 房产租赁列表（view=properties）
    r = await client.get("/api/v1/ledger/leases", headers=h, params={"view": "properties"})
    props = r.json()["data"]["items"]
    assert any(p["id"] == re_asset["id"] and p["has_lease"] is True for p in props)


# ---------------- 应收 / 实收 ----------------

async def test_receivable_does_not_change_balance(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    re_asset = await _create_real_estate(client, h)
    lease = await _create_lease(client, h, re_asset["id"])

    before = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]
    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/receivables", headers=h, json={
        "amount": "5000", "due_date": _days_ago(0), "period_start": "2026-02-01",
        "period_end": "2026-02-28",
    })
    assert r.json()["success"] is True, r.text
    receivable = r.json()["data"]
    assert receivable["status"] == "pending"

    # 应收不改变账户余额
    after = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]
    assert after["balance"] == before["balance"]


async def test_receipt_counts_as_income_and_settles_receivable(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    re_asset = await _create_real_estate(client, h)
    lease = await _create_lease(client, h, re_asset["id"], receiving_account_id=ids["a1"])

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/receivables", headers=h, json={
        "amount": "5000", "due_date": _days_ago(0),
    })
    receivable_id = r.json()["data"]["id"]

    income_before = float((await _metrics(client, h))["metrics"]["income"])
    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/receipts", headers=h, json={
        "amount": "5000", "account_id": ids["a1"], "receivable_id": receivable_id,
    })
    assert r.json()["success"] is True, r.text

    income_after = float((await _metrics(client, h))["metrics"]["income"])
    assert abs(income_after - income_before - 5000) < 0.01

    # 应收已核销
    r = await client.get(f"/api/v1/ledger/leases/{lease['id']}/receivables", headers=h)
    rec = next(x for x in r.json()["data"]["items"] if x["id"] == receivable_id)
    assert rec["status"] == "paid"


# ---------------- 押金 ----------------

async def test_deposit_not_counted(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    re_asset = await _create_real_estate(client, h)
    lease = await _create_lease(client, h, re_asset["id"], receiving_account_id=ids["a1"])

    m0 = await _metrics(client, h)
    bal0 = float((await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"])

    # 收取押金
    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/deposits/receive", headers=h, json={
        "amount": "5000", "account_id": ids["a1"],
    })
    assert r.json()["success"] is True, r.text
    deposit = r.json()["data"]
    assert deposit["status"] == "held"

    m1 = await _metrics(client, h)
    bal1 = float((await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"])
    assert abs(bal1 - bal0 - 5000) < 0.01                      # 账户余额增加
    assert float(m1["metrics"]["income"]) == float(m0["metrics"]["income"])  # 不计收入

    # 退还押金
    r = await client.post(
        f"/api/v1/ledger/leases/{lease['id']}/deposits/{deposit['id']}/return",
        headers=h, json={"account_id": ids["a1"]})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["status"] == "returned"

    m2 = await _metrics(client, h)
    bal2 = float((await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"])
    assert abs(bal2 - bal1 + 5000) < 0.01
    assert float(m2["metrics"]["expense"]) == float(m1["metrics"]["expense"])  # 不计支出


# ---------------- 逾期派生 / 终止 ----------------

async def test_overdue_derived_and_terminate(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    re_asset = await _create_real_estate(client, h)
    lease = await _create_lease(client, h, re_asset["id"])

    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/receivables", headers=h, json={
        "amount": "5000", "due_date": _days_ago(10),
    })
    receivable = r.json()["data"]
    assert receivable["overdue"] is True
    assert receivable["status"] == "pending"

    # 终止租约需令牌 + 原因
    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/terminate", headers=h, json={
        "reason": "租客退租",
    })
    assert r.status_code == 422  # 缺少确认令牌

    token = (await client.post("/api/v1/settings/confirm-tokens", headers=h, json={
        "action": "lease.terminate", "object_type": "lease", "object_id": lease["id"],
    })).json()["data"]["token"]
    r = await client.post(f"/api/v1/ledger/leases/{lease['id']}/terminate", headers=h, json={
        "reason": "租客退租", "confirm_token": token,
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["status"] == "terminated"

    # 房产转空置
    r = await client.get("/api/v1/ledger/assets", headers=h)
    item = next(a for a in r.json()["data"]["items"] if a["id"] == re_asset["id"])
    assert item["extension"]["has_lease"] is False


# ---------------- 对账 ----------------

async def test_reconciliation_adjustment_event(client):
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 期初 100000，无变动；系统余额 100000
    preview = (await client.get("/api/v1/ledger/reconciliation", headers=h,
                                params={"account_id": ids["a1"]})).json()["data"]
    assert preview["system_balance"] == "100000.00"

    # 实际余额 100050 → 差额 +50 → 生成收入调整事件
    r = await client.post("/api/v1/ledger/reconciliation", headers=h, json={
        "account_id": ids["a1"], "actual_balance": "100050", "reason": "银行到账补记",
    })
    assert r.json()["success"] is True, r.text
    result = r.json()["data"]
    assert result["adjusted"] is True
    assert result["diff"] == "50.00"
    assert result["adjustment_event"]["event_type"] == "income"
    assert result["adjustment_event"]["source"] == "reconciliation"

    # 系统余额已对齐
    after = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]
    assert after["balance"] == "100050.00"
    assert after["last_reconciled_at"] is not None


async def test_reconciliation_negative_diff_and_zero_diff(client):
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 差额为负 → 支出调整
    r = await client.post("/api/v1/ledger/reconciliation", headers=h, json={
        "account_id": ids["a1"], "actual_balance": "99800", "reason": "多记支出冲回",
    })
    assert r.json()["success"] is True
    assert r.json()["data"]["adjustment_event"]["event_type"] == "expense"

    # 差额为 0 → 不生成调整事件，仅标记已核对
    bal = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"]
    r = await client.post("/api/v1/ledger/reconciliation", headers=h, json={
        "account_id": ids["a1"], "actual_balance": bal,
    })
    assert r.json()["success"] is True
    assert r.json()["data"]["adjusted"] is False
    assert r.json()["data"]["adjustment_event"] is None


async def test_reconciliation_requires_reason_when_diff(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/reconciliation", headers=h, json={
        "account_id": ids["a1"], "actual_balance": "100100",
    })
    assert r.status_code == 422
    assert "reason" in r.json()["error"]["field_errors"]


async def test_lease_category(client):
    """08 §3：租赁接入「租赁」分类域，可关联分类、回填标签并按分类筛选。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    re_asset = await _create_real_estate(client, h, "出租房")

    lcats = (await client.get("/api/v1/settings/categories?domain=lease", headers=h)).json()["data"]["items"]
    assert len(lcats) >= 3
    target = lcats[0]

    lease = await _create_lease(client, h, re_asset["id"], category_id=target["id"])
    assert lease["category_id"] == target["id"]
    assert lease["category_label"] == target["name"]

    r = await client.get(f"/api/v1/ledger/leases?view=leases&category_id={target['id']}", headers=h)
    items = r.json()["data"]["items"]
    assert any(i["id"] == lease["id"] for i in items)
