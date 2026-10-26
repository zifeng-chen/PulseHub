"""M1 核心账务验收测试（18 §4、12 §2）。

覆盖：账户余额口径、收入支出、内部转账、转账组、基础统计、多币种分组、
未交付模块类型拦截、关联校验、审计与版本冲突。
"""

from __future__ import annotations

from decimal import Decimal

from tests.conftest import auth, complete_setup


async def _bootstrap(client, with_accounts: bool = True) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    ids: dict = {"headers": h, "access": tokens["access"]}
    if with_accounts:
        r = await client.post("/api/v1/ledger/accounts", headers=h, json={
            "name": "工资卡", "account_type": "bank", "institution": "某银行",
            "currency": "CNY", "opening_balance": "10000", "include_in_net_worth": True,
        })
        assert r.json()["success"] is True, r.text
        ids["a1"] = r.json()["data"]["id"]
        r = await client.post("/api/v1/ledger/accounts", headers=h, json={
            "name": "现金", "account_type": "cash", "currency": "CNY",
            "opening_balance": "500", "include_in_net_worth": True,
        })
        ids["a2"] = r.json()["data"]["id"]
    return ids


async def _create_category(client, headers, domain: str, name: str) -> str:
    r = await client.post("/api/v1/settings/categories", headers=headers,
                          json={"domain": domain, "name": name})
    assert r.json()["success"] is True, r.text
    return r.json()["data"]["id"]


async def _confirmed_expense(client, headers, account_id: str, amount: str,
                             title: str = "支出", category_id: str | None = None) -> dict:
    payload = {"event_type": "expense", "title": title, "amount": amount,
               "account_id": account_id}
    if category_id:
        payload["category_id"] = category_id
    r = await client.post("/api/v1/ledger/events", headers=headers, json=payload)
    assert r.json()["success"] is True, r.text
    event = r.json()["data"]
    r = await client.post(f"/api/v1/ledger/events/{event['id']}/confirm",
                          headers=headers, json={"version": event["version"]})
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


# ---------------- 账户与余额 ----------------

async def test_account_creation_and_opening_balance(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    data = r.json()["data"]
    assert data["opening_balance"] == "10000.00"
    assert data["balance"] == "10000.00"
    assert data["available_balance"] == "10000.00"
    assert data["account_type_label"] == "银行卡"
    assert data["flow"] == []


async def test_account_validation(client):
    ids = await _bootstrap(client, with_accounts=False)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={"name": ""})
    assert r.status_code == 422
    assert "name" in r.json()["error"]["field_errors"]

    r = await client.post("/api/v1/ledger/accounts", headers=h,
                          json={"name": "X", "account_type": "credit_card"})
    assert r.status_code == 422
    assert "account_type" in r.json()["error"]["field_errors"]


async def test_balance_equals_opening_plus_confirmed_movements(client):
    ids = await _bootstrap(client)
    h, a1 = ids["headers"], ids["a1"]

    # 草稿不影响余额
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "草稿支出", "amount": "999", "account_id": a1})
    draft = r.json()["data"]
    r = await client.get(f"/api/v1/ledger/accounts/{a1}", headers=h)
    assert r.json()["data"]["balance"] == "10000.00"

    # 确认后才生效
    await client.post(f"/api/v1/ledger/events/{draft['id']}/confirm", headers=h,
                      json={"version": draft["version"]})
    r = await client.get(f"/api/v1/ledger/accounts/{a1}", headers=h)
    assert r.json()["data"]["balance"] == "9001.00"

    # 收入增加余额
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "工资", "amount": "2000", "account_id": a1})
    inc = r.json()["data"]
    await client.post(f"/api/v1/ledger/events/{inc['id']}/confirm", headers=h,
                      json={"version": inc["version"]})
    r = await client.get(f"/api/v1/ledger/accounts/{a1}", headers=h)
    assert r.json()["data"]["balance"] == "11001.00"

    # 冲销后余额回退
    r = await client.post(f"/api/v1/ledger/events/{inc['id']}/reverse", headers=h,
                          json={"reason": "测试冲销"})
    assert r.json()["data"]["status"] == "reversed"
    r = await client.get(f"/api/v1/ledger/accounts/{a1}", headers=h)
    assert r.json()["data"]["balance"] == "9001.00"


# ---------------- 事件类型与关联校验 ----------------

async def test_deferred_event_types_are_blocked(client):
    """未交付模块的事件类型仍不允许创建（18 §6）；资产/负债/投资/租赁类型已随 M2/M3/M4 开放但必须关联对象。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 缺少关联对象时被字段校验拦截（不是 409 模块未交付）
    for event_type, payload, field in (
        ("asset_acquire", {"account_id": ids["a1"]}, "asset_id"),
        ("liability_repay", {}, "liability_id"),
        ("investment_buy", {}, "investment_account_id"),
        ("rent_received", {}, "lease_id"),
        ("deposit_receive", {}, "lease_id"),
    ):
        r = await client.post("/api/v1/ledger/events", headers=h, json={
            "event_type": event_type, "title": "缺少关联对象", "amount": "1", **payload})
        assert r.status_code == 422, event_type
        assert field in r.json()["error"]["field_errors"]


async def test_category_domain_must_match_event_type(client):
    ids = await _bootstrap(client)
    h, a1 = ids["headers"], ids["a1"]
    income_cat = await _create_category(client, h, "income", "测试收入分类")

    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "支出配收入分类", "amount": "10",
        "account_id": a1, "category_id": income_cat})
    assert r.status_code == 422
    assert "category_id" in r.json()["error"]["field_errors"]


async def test_event_account_must_belong_to_household(client):
    ids = await _bootstrap(client)
    h, a1 = ids["headers"], ids["a1"]
    expense_cat = await _create_category(client, h, "expense", "测试支出分类")
    await _confirmed_expense(client, h, a1, "10", "正常支出", expense_cat)

    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "不存在账户", "amount": "1",
        "account_id": "no-such-account"})
    assert r.status_code == 422
    assert "account_id" in r.json()["error"]["field_errors"]


# ---------------- 内部转账 ----------------

async def test_transfer_moves_balance_without_affecting_income_expense(client):
    ids = await _bootstrap(client)
    h, a1, a2 = ids["headers"], ids["a1"], ids["a2"]

    r = await client.get("/api/v1/ledger/summary", headers=h)
    before = r.json()["data"]

    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a1, "to_account_id": a2, "amount": "1500",
        "occurred_at": "2026-10-15T10:00:00+08:00", "note": "取现"})
    assert r.json()["success"] is True, r.text
    transfer = r.json()["data"]
    assert transfer["status"] == "confirmed"
    assert transfer["status_label"] == "已确认"
    assert transfer["group_no"].startswith("TR")

    r = await client.get(f"/api/v1/ledger/accounts/{a1}", headers=h)
    assert r.json()["data"]["balance"] == "8500.00"
    r = await client.get(f"/api/v1/ledger/accounts/{a2}", headers=h)
    assert r.json()["data"]["balance"] == "2000.00"

    r = await client.get("/api/v1/ledger/summary", headers=h)
    after = r.json()["data"]
    # 内部转账不改变收入与支出
    assert after["metrics"]["income"] == before["metrics"]["income"]
    assert after["metrics"]["expense"] == before["metrics"]["expense"]
    # 净资产不变（同币种内部平移）
    assert after["net_worth"]["net_worth"] == before["net_worth"]["net_worth"]


async def test_transfer_same_account_forbidden(client):
    ids = await _bootstrap(client)
    h, a1 = ids["headers"], ids["a1"]
    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a1, "to_account_id": a1, "amount": "100"})
    assert r.status_code == 422
    assert "to_account_id" in r.json()["error"]["field_errors"]


async def test_transfer_fee_is_expense_but_not_principal(client):
    ids = await _bootstrap(client)
    h, a1, a2 = ids["headers"], ids["a1"], ids["a2"]

    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a1, "to_account_id": a2, "amount": "1000", "fee": "5"})
    transfer = r.json()["data"]
    assert Decimal(transfer["fee"]) == Decimal("5.00")
    assert transfer["fee_event_id"]

    # 手续费计入支出，转账本金不计入
    r = await client.get("/api/v1/ledger/summary", headers=h)
    assert Decimal(r.json()["data"]["metrics"]["expense"]) == Decimal("5.00")

    # 转出账户扣 1000 + 5
    r = await client.get(f"/api/v1/ledger/accounts/{a1}", headers=h)
    assert r.json()["data"]["balance"] == "8995.00"


async def test_cross_currency_transfer_requires_rate(client):
    ids = await _bootstrap(client)
    h, a1 = ids["headers"], ids["a1"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "美元账户", "account_type": "bank", "currency": "USD",
        "opening_balance": "100", "include_in_net_worth": True})
    usd = r.json()["data"]["id"]

    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a1, "to_account_id": usd, "amount": "700"})
    assert r.status_code == 422
    err = r.json()["error"]["field_errors"]
    assert "exchange_rate" in err and "to_amount" in err

    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a1, "to_account_id": usd, "amount": "700",
        "to_amount": "100", "exchange_rate": "7.0"})
    assert r.json()["success"] is True
    assert r.json()["data"]["conversion_status"] == "converted"


async def test_transfer_reverse_restores_balances(client):
    ids = await _bootstrap(client)
    h, a1, a2 = ids["headers"], ids["a1"], ids["a2"]
    r = await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a1, "to_account_id": a2, "amount": "500"})
    group = r.json()["data"]

    r = await client.post(f"/api/v1/ledger/transfers/{group['id']}/reverse", headers=h,
                          json={"reason": "转错账户"})
    assert r.json()["data"]["status"] == "reversed"

    r = await client.get(f"/api/v1/ledger/accounts/{a1}", headers=h)
    assert r.json()["data"]["balance"] == "10000.00"
    r = await client.get(f"/api/v1/ledger/accounts/{a2}", headers=h)
    assert r.json()["data"]["balance"] == "500.00"


# ---------------- 基础统计与多币种 ----------------

async def test_summary_income_expense_net_worth(client):
    ids = await _bootstrap(client)
    h, a1 = ids["headers"], ids["a1"]
    cat = await _create_category(client, h, "expense", "餐饮测试")
    await _confirmed_expense(client, h, a1, "128.50", "午饭", cat)

    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "工资", "amount": "3000", "account_id": a1})
    inc = r.json()["data"]
    await client.post(f"/api/v1/ledger/events/{inc['id']}/confirm", headers=h,
                      json={"version": inc["version"]})

    r = await client.get("/api/v1/ledger/summary", headers=h)
    data = r.json()["data"]
    assert Decimal(data["metrics"]["income"]) == Decimal("3000.00")
    assert Decimal(data["metrics"]["expense"]) == Decimal("128.50")
    assert Decimal(data["metrics"]["balance"]) == Decimal("2871.50")
    # 净资产 = 10000 + 500 + 3000 - 128.50
    assert Decimal(data["net_worth"]["net_worth"]) == Decimal("13371.50")
    assert data["caliber"]["excludes_draft"] is True
    assert "期初余额" in data["caliber"]["account_balance"]


async def test_multi_currency_is_grouped_not_summed(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "美元账户", "currency": "USD", "opening_balance": "100"})
    usd = r.json()["data"]["id"]
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "income", "title": "美元收入", "amount": "50", "account_id": usd})
    inc = r.json()["data"]
    await client.post(f"/api/v1/ledger/events/{inc['id']}/confirm", headers=h,
                      json={"version": inc["version"]})

    r = await client.get("/api/v1/ledger/summary", headers=h)
    data = r.json()["data"]
    assert data["accounts"]["by_currency"].__len__() == 2
    currencies = {item["currency"] for item in data["accounts"]["by_currency"]}
    assert currencies == {"CNY", "USD"}
    # 人民币账户合计仍是 10500，没有被美元金额污染
    cny = next(i for i in data["accounts"]["by_currency"] if i["currency"] == "CNY")
    assert Decimal(cny["total"]) == Decimal("10500.00")
    # 美元收入不计入人民币口径的收入合计
    assert Decimal(data["metrics"]["income"]) == Decimal("0.00")


# ---------------- 流水筛选 ----------------

async def test_event_filters_and_pagination(client):
    ids = await _bootstrap(client)
    h, a1, a2 = ids["headers"], ids["a1"], ids["a2"]
    cat = await _create_category(client, h, "expense", "交通测试")
    await _confirmed_expense(client, h, a1, "20", "地铁", cat)
    await _confirmed_expense(client, h, a2, "80", "打车", cat)

    r = await client.get("/api/v1/ledger/events", headers=h,
                         params={"direction": "expense", "account_id": a2})
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["title"] == "打车"

    r = await client.get("/api/v1/ledger/events", headers=h,
                         params={"keyword": "地铁"})
    assert len(r.json()["data"]["items"]) == 1

    r = await client.get("/api/v1/ledger/events", headers=h,
                         params={"amount_min": 50})
    assert all(Decimal(i["amount"]) >= 50 for i in r.json()["data"]["items"])

    r = await client.get("/api/v1/ledger/events", headers=h,
                         params={"category_id": cat, "status": "confirmed"})
    assert r.json()["data"]["total"] == 2

    r = await client.get("/api/v1/ledger/events", headers=h, params={"page_size": 1})
    body = r.json()["data"]
    assert len(body["items"]) == 1 and body["has_next"] is True


# ---------------- 账户保护与审计 ----------------

async def test_account_with_flow_cannot_change_currency_or_be_deleted(client):
    ids = await _bootstrap(client)
    h, a1 = ids["headers"], ids["a1"]
    await _confirmed_expense(client, h, a1, "10", "测试支出")

    r = await client.put(f"/api/v1/ledger/accounts/{a1}", headers=h, json={"currency": "USD"})
    assert r.status_code == 409
    assert "currency" in r.json()["error"]["field_errors"]

    r = await client.post("/api/v1/settings/confirm-tokens", headers=h,
                          json={"action": "account.archive", "object_type": "account",
                                "object_id": a1})
    token = r.json()["data"]["token"]
    r = await client.delete(f"/api/v1/ledger/accounts/{a1}",
                            headers=h, params={"confirm_token": token, "reason": "不再使用"})
    assert r.json()["data"]["status"] == "disabled"


async def test_account_archive_requires_confirm_token(client):
    ids = await _bootstrap(client)
    h, a2 = ids["headers"], ids["a2"]
    r = await client.delete(f"/api/v1/ledger/accounts/{a2}",
                            headers=h, params={"reason": "不再使用"})
    assert r.status_code == 422
    assert "confirm_token" in r.json()["error"]["field_errors"]


async def test_m1_writes_full_audit_trail(client):
    ids = await _bootstrap(client)
    h, a1, a2 = ids["headers"], ids["a1"], ids["a2"]
    await client.post("/api/v1/ledger/transfers", headers=h, json={
        "from_account_id": a1, "to_account_id": a2, "amount": "200"})

    r = await client.get("/api/v1/audit", headers=h, params={"object_type": "transfer"})
    items = r.json()["data"]["items"]
    assert items, "转账必须写入审计"
    r = await client.get(f"/api/v1/audit/{items[0]['id']}", headers=h)
    detail = r.json()["data"]
    assert any("转账" in str(c["field_label"]) or "状态" in str(c["field_label"])
               for c in detail["changes"]) or detail["after_summary"]


async def test_readonly_member_cannot_create_account_or_transfer(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    await client.post("/api/v1/settings/members", headers=h, json={
        "username": "ro2", "display_name": "只读2", "password": "Viewer@2026#1",
        "role": "readonly"})
    r = await client.post("/api/v1/auth/login", json={"username": "ro2", "password": "Viewer@2026#1"})
    vh = auth(r.json()["data"]["access_token"])

    assert (await client.get("/api/v1/ledger/accounts", headers=vh)).status_code == 200
    r = await client.post("/api/v1/ledger/accounts", headers=vh, json={"name": "不应成功"})
    assert r.status_code == 403
    r = await client.post("/api/v1/ledger/transfers", headers=vh, json={
        "from_account_id": ids["a1"], "to_account_id": ids["a2"], "amount": "1"})
    assert r.status_code == 403


async def test_draft_cancel_and_edit(client):
    """11 §2 / 23 §6：草稿可编辑、可取消（draft→cancelled 终态）；已确认/已取消不可再编辑或取消。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 创建草稿
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "原始标题", "amount": "100",
        "account_id": ids["a1"], "source": "manual",
    })
    assert r.json()["success"] is True, r.text
    draft = r.json()["data"]
    assert draft["status"] == "draft"

    # 编辑草稿（改标题与金额）
    r = await client.put(f"/api/v1/ledger/events/{draft['id']}", headers=h, json={
        "version": draft["version"], "title": "修改后的标题", "amount": "200",
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["title"] == "修改后的标题"
    assert r.json()["data"]["amount"] == "200.00"

    # 编辑后版本 +1，确认用新版本
    edited = r.json()["data"]
    r = await client.post(f"/api/v1/ledger/events/{edited['id']}/confirm", headers=h,
                          json={"version": edited["version"]})
    assert r.json()["success"] is True, r.text

    # 已确认事件不能再编辑
    confirmed = r.json()["data"]
    r = await client.put(f"/api/v1/ledger/events/{confirmed['id']}", headers=h, json={
        "version": confirmed["version"], "title": "不应生效"})
    assert r.status_code == 409

    # 另一个草稿 → 取消
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "待取消", "amount": "50",
        "account_id": ids["a1"], "source": "manual",
    })
    draft2 = r.json()["data"]
    r = await client.post(f"/api/v1/ledger/events/{draft2['id']}/cancel", headers=h,
                          json={"version": draft2["version"]})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["status"] == "cancelled"

    # 已取消是终态：不能再确认
    cancelled = r.json()["data"]
    r = await client.post(f"/api/v1/ledger/events/{cancelled['id']}/confirm", headers=h,
                          json={"version": cancelled["version"]})
    assert r.status_code == 409


async def test_account_category(client):
    """账户接入「账户」分类域（08 §3）：可设分类、列表回填标签并可筛选，跨域分类被拒。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    cats = (await client.get("/api/v1/settings/categories", headers=h,
                             params={"domain": "account"})).json()["data"]["items"]
    assert cats, "应预置「账户」分类"
    cat = next(c for c in cats if c["parent_id"] is None)

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "分类测试卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100", "category_id": cat["id"],
    })
    assert r.json()["success"] is True, r.text
    created = r.json()["data"]
    assert created["category_id"] == cat["id"]
    assert created["category_label"] == cat["name"]

    r = await client.get("/api/v1/ledger/accounts", headers=h,
                         params={"category_id": cat["id"]})
    items = r.json()["data"]["items"]
    assert any(i["id"] == created["id"] for i in items)
    assert all(i["category_label"] == cat["name"] for i in items)

    # 跨域分类（支出域）应被拒绝
    exp = (await client.get("/api/v1/settings/categories", headers=h,
                            params={"domain": "expense"})).json()["data"]["items"][0]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "错误分类卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "0", "category_id": exp["id"],
    })
    assert r.status_code == 422


async def test_account_card_and_holder(client):
    """银行卡记录卡号后四位（仅存末四位）；虚拟账户记录开户人。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 银行卡：传完整卡号 → 只保留后四位
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "招行储蓄卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "0", "card_last4": "6225881234567890",
    })
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["card_last4"] == "7890"
    assert data["card_masked"] == "尾号 7890"

    # 虚拟账户：开户人
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "我的花呗", "account_type": "e_wallet", "currency": "CNY",
        "opening_balance": "0", "holder_name": "张三",
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["holder_name"] == "张三"

    # 卡号后四位不是 4 位数字 → 校验失败
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "坏卡号", "account_type": "bank", "currency": "CNY",
        "opening_balance": "0", "card_last4": "12",
    })
    assert r.status_code == 422

    # 账户子分类包含信用卡 / 花呗 / 借呗 / 白条
    cats = (await client.get("/api/v1/settings/categories", headers=h,
                             params={"domain": "account"})).json()["data"]["items"]
    names = {c["name"] for c in cats}
    for expected in ("银行卡", "储蓄卡", "信用卡", "消费信贷", "花呗", "借呗", "白条"):
        assert expected in names, f"缺少账户分类：{expected}"


async def test_account_category_applies_to(client):
    """账户分类带「适用账户类型」：新建账户时按类型联动筛选（08 §3）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    cats = (await client.get("/api/v1/settings/categories", headers=h,
                             params={"domain": "account"})).json()["data"]["items"]
    by_name = {c["name"]: c for c in cats}

    assert by_name["银行卡"]["applies_to"] == "bank"
    assert by_name["储蓄存款"]["applies_to"] == "bank"
    assert by_name["信用卡"]["applies_to"] == "bank"        # 子分类继承父分类
    assert by_name["消费信贷"]["applies_to"] == "e_wallet"
    assert by_name["花呗"]["applies_to"] == "e_wallet"
    assert by_name["电子钱包"]["applies_to"] == "e_wallet"
    assert by_name["现金"]["applies_to"] == "cash"
    assert by_name["收款账户"]["applies_to"] is None        # 通用
    assert by_name["其他账户"]["applies_to"] is None

    # 新增子分类自动继承父分类的适用范围
    r = await client.post("/api/v1/settings/categories", headers=h, json={
        "domain": "account", "name": "虚拟银行卡", "parent_id": by_name["银行卡"]["id"],
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["applies_to"] == "bank"

    # 显式指定多值适用范围
    r = await client.post("/api/v1/settings/categories", headers=h, json={
        "domain": "account", "name": "混合收款", "applies_to": "bank,e_wallet",
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["applies_to"] == "bank,e_wallet"

    # 编辑可改适用范围
    cid = r.json()["data"]["id"]
    r = await client.patch(f"/api/v1/settings/categories/{cid}", headers=h,
                           json={"applies_to": "cash"})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["applies_to"] == "cash"


async def test_account_credit_card_fields(client):
    """信用卡类账户记录额度、已用、可用额度、有效期、账单日与还款日（06 §7）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    cats = (await client.get("/api/v1/settings/categories", headers=h,
                             params={"domain": "account"})).json()["data"]["items"]
    by_name = {c["name"]: c for c in cats}
    # 分类字段档案：信用卡与消费信贷区分开
    assert by_name["信用卡"]["attribute_profile"] == "credit_card"
    assert by_name["花呗"]["attribute_profile"] == "credit_line"
    assert by_name["储蓄卡"]["attribute_profile"] == "debit_card"
    assert by_name["支付宝"]["attribute_profile"] == "wallet"
    assert by_name["现金备用"]["attribute_profile"] == "cash"

    credit = by_name["信用卡"]
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "招行信用卡", "account_type": "bank", "category_id": credit["id"],
        "card_last4": "1234", "institution": "招商银行",
        "credit_limit": "50000", "credit_used": "12345.67",
        "card_expiry": "2029-05", "bill_day": 5, "due_day": 23,
    })
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["credit_limit"] == "50000.00"
    assert data["credit_used"] == "12345.67"
    assert data["available_credit"] == "37654.33"
    assert data["card_expiry"] == "2029-05"
    assert data["bill_day"] == 5 and data["due_day"] == 23

    # 已用额度是派生值（由余额决定），编辑时传入不会改变它；
    # 要修正历史请调整期初余额或走冲销/更正，避免与资金变动打架。
    r = await client.put(f"/api/v1/ledger/accounts/{data['id']}", headers=h, json={
        "version": data["version"], "credit_used": "10000",
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["credit_used"] == "12345.67"
    assert r.json()["data"]["available_credit"] == "37654.33"

    # 校验：负数额度 / 非法有效期 / 账单日越界
    for patch, field in (
        ({"credit_limit": "-1"}, "credit_limit"),
        ({"card_expiry": "2029/05"}, "card_expiry"),
        ({"bill_day": 31}, "bill_day"),
    ):
        body = {"name": "坏卡", "account_type": "bank", "currency": "CNY",
                "opening_balance": "0", **patch}
        r = await client.post("/api/v1/ledger/accounts", headers=h, json=body)
        assert r.status_code == 422, r.text
        assert field in r.json()["error"]["field_errors"]


async def test_account_savings_and_loan_fields(client):
    """储蓄与借款分类的专属字段：年利率、存期、到期日（按分类细化，08 §3）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    cats = (await client.get("/api/v1/settings/categories", headers=h,
                             params={"domain": "account"})).json()["data"]["items"]
    by_name = {c["name"]: c for c in cats}
    assert by_name["定期存款"]["attribute_profile"] == "savings_fixed"
    assert by_name["活期储蓄"]["attribute_profile"] == "savings_current"
    assert by_name["借呗"]["attribute_profile"] == "loan_line"
    assert by_name["京东金条"]["attribute_profile"] == "loan_line"
    assert by_name["花呗"]["attribute_profile"] == "credit_line"

    # 定期存款：年利率 + 存期 + 到期日
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "一年期定存", "account_type": "bank",
        "category_id": by_name["定期存款"]["id"],
        "opening_balance": "100000", "interest_rate": "1.75",
        "term_months": 12, "maturity_date": "2027-10-17",
    })
    assert r.json()["success"] is True, r.text
    d = r.json()["data"]
    assert d["interest_rate"] == "1.7500"      # 利率非金额，保留 4 位
    assert d["term_months"] == 12
    assert d["maturity_date"].startswith("2027-10-17")

    # 借呗：借款额度 + 年化利率，可用额度自动计算
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "借呗", "account_type": "e_wallet",
        "category_id": by_name["借呗"]["id"],
        "credit_limit": "30000", "credit_used": "5000",
        "interest_rate": "7.3", "due_day": 20,
    })
    assert r.json()["success"] is True, r.text
    d = r.json()["data"]
    assert d["available_credit"] == "25000.00"
    assert d["interest_rate"] == "7.3000"

    # 校验：利率越界 / 存期越界 / 到期日格式
    for patch, field in (
        ({"interest_rate": "150"}, "interest_rate"),
        ({"term_months": 0}, "term_months"),
        ({"maturity_date": "2027/10/17"}, "maturity_date"),
    ):
        body = {"name": "坏账户", "account_type": "bank", "currency": "CNY",
                "opening_balance": "0", **patch}
        r = await client.post("/api/v1/ledger/accounts", headers=h, json=body)
        assert r.status_code == 422, r.text
        assert field in r.json()["error"]["field_errors"]


async def test_input_limits_rejected_before_db(client):
    """超长文本与超大金额应在字段级被拦截，而不是落到数据库错误（生产 MySQL 会 500）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 账户名称超过列宽 128
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "长" * 200, "account_type": "cash", "currency": "CNY", "opening_balance": "0",
    })
    assert r.status_code == 422, r.text
    assert "name" in r.json()["error"]["field_errors"]

    # 开户机构超过列宽 128
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "某卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "0", "institution": "银" * 200,
    })
    assert r.status_code == 422, r.text
    assert "institution" in r.json()["error"]["field_errors"]

    # 金额超出 Numeric(20,4) 安全范围
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "巨款", "account_type": "cash", "currency": "CNY", "opening_balance": "1e20",
    })
    assert r.status_code == 422, r.text
    assert "opening_balance" in r.json()["error"]["field_errors"]

    # 事件金额超范围
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "巨额支出", "amount": "1e20", "account_id": ids["a1"],
    })
    assert r.status_code == 422, r.text
    assert "amount" in r.json()["error"]["field_errors"]

    # 事件标题超过列宽 255
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "长" * 300, "amount": "1", "account_id": ids["a1"],
    })
    assert r.status_code == 422, r.text
    assert "title" in r.json()["error"]["field_errors"]
