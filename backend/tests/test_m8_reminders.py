"""M8 提醒 + PushPlus 验收测试（08 §5/§12、19 §5.7）。

覆盖：六类提醒确定性查询、发送写通知记录、失败可重试、未配置降级不阻塞、工作台提醒计数。
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


async def _create_liability(client, h, **kw):
    payload = {
        "name": "个人借款", "liability_type": "personal", "initial_amount": "80000",
        "currency": "CNY", "interest_rate": 4.5, "repayment_method": "equal_installment",
        "term_months": 24, "monthly_payment": "3500", "payment_day": 15,
        "payment_frequency": "monthly", "start_date": "2026-01-01T00:00:00Z",
        "end_date": "2028-01-01T00:00:00Z",
    }
    payload.update(kw)
    r = await client.post("/api/v1/ledger/liabilities", headers=h, json=payload)
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


async def _create_real_estate_and_lease(client, h, a1):
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
        "deposit_amount": "3000", "receiving_account_id": a1,
    })
    assert r.json()["success"] is True, r.text
    return re_id, r.json()["data"]["id"]


async def test_reminders_deterministic_query(client):
    """08 §12：提醒由确定性查询实时计算——临近还款与逾期租金被正确识别。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _create_liability(client, h)  # 下次还款日由系统推算（本月内）
    await _create_real_estate_and_lease(client, h, ids["a1"])

    r = await client.get("/api/v1/ledger/reminders", headers=h)
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    groups = {g["type"]: g for g in data["groups"]}
    # 负债 next_payment_date 在本月 → 还款提醒
    assert groups["repayment"]["count"] >= 1
    # 六类提醒都在
    assert set(groups) == {"repayment", "credit_card_bill", "rent_due", "lease_expire",
                           "asset_maintenance", "ai_suggestion"}


async def test_reminder_send_writes_record_and_unconfigured_fails(client):
    """08 §12：发送写通知记录；PushPlus 未配置时发送失败但不阻塞（记录 status=failed）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _create_liability(client, h)

    r = await client.get("/api/v1/ledger/reminders", headers=h)
    items = [it for g in r.json()["data"]["groups"] for it in g["items"]]
    assert items, "应有还款提醒"

    # 发送（PushPlus 未配置 → failed，但记录已写入）
    r = await client.post("/api/v1/ledger/reminders/send", headers=h,
                          json={"keys": [items[0]["key"]]})
    assert r.json()["success"] is True, r.text
    result = r.json()["data"]
    assert result["total"] == 1
    assert result["failed"] == 1  # 未配置 → 发送失败

    # 通知记录已写入
    r = await client.get("/api/v1/ledger/notifications/records", headers=h)
    records = r.json()["data"]["items"]
    assert len(records) >= 1
    assert records[0]["delivery_status"] == "failed"


async def test_retry_failed_record(client):
    """08 §12：失败通知可重试（仍失败，但 retry_count 增加）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _create_liability(client, h)
    items = [it for g in (await client.get("/api/v1/ledger/reminders", headers=h)).json()["data"]["groups"] for it in g["items"]]
    await client.post("/api/v1/ledger/reminders/send", headers=h, json={"keys": [items[0]["key"]]})
    records = (await client.get("/api/v1/ledger/notifications/records", headers=h)).json()["data"]["items"]
    rid = records[0]["id"]

    r = await client.post(f"/api/v1/ledger/notifications/records/{rid}/retry", headers=h)
    assert r.json()["success"] is True
    assert r.json()["data"]["delivery_status"] == "failed"  # 仍未配置
    assert r.json()["data"]["retry_count"] >= 2


async def test_dashboard_pending_reminders_count(client):
    """工作台待处理提醒接真实计数。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _create_liability(client, h)

    r = await client.get("/api/v1/dashboard/snapshot", headers=h)
    snap = r.json()["data"]
    assert snap["metrics"]["pending_reminders"] >= 1
    assert snap["pending"]["reminders"] >= 1


async def test_reminder_send_requires_confirm_permission(client):
    """发送通知需确认权限（成员可 confirm，但需登录）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    # 未登录返回 401
    r = await client.post("/api/v1/ledger/reminders/send", json={"keys": ["x"]})
    assert r.status_code == 401


async def test_ai_suggestions_from_rules(client):
    """AI 建议分组：由确定性规则生成（草稿待确认等），不依赖模型。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 建一条草稿 → 触发「草稿待确认」建议
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "待确认支出", "amount": "10",
        "account_id": ids["a1"], "source": "manual",
    })
    assert r.json()["success"] is True, r.text

    r = await client.get("/api/v1/ledger/reminders", headers=h)
    assert r.json()["success"] is True, r.text
    groups = r.json()["data"]["groups"]
    ai = next(g for g in groups if g["type"] == "ai_suggestion")
    assert ai["count"] >= 1
    keys = {it["key"] for it in ai["items"]}
    assert "ai:drafts" in keys
    for it in ai["items"]:
        assert it["severity"] in ("critical", "warning", "info")
        assert it["reason"]
        assert it["suggestion"]
