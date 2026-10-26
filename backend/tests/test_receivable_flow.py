"""借出款全链路（06 §2、15 §7）：借出不是支出，回款不是收入。

用户场景：借给朋友 5 万，从工资卡转出；朋友分期还回来。
- 借出：账户减少、应收增加，净资产不变，不计支出
- 回款：账户增加、应收本金减少，不计收入
"""

from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    a1 = (await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True,
    })).json()["data"]["id"]
    cats = (await client.get("/api/v1/ledger/categories", headers=h)).json()["data"]["items"]
    lend = next(c for c in cats if c["domain"] == "asset" and c["name"] == "借出款" and not c["parent_id"])
    return {"headers": h, "a1": a1, "lend_cat": lend["id"]}


async def test_lend_out_and_repayment_flow(client):
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 借出 50000：从工资卡出钱
    asset = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给张三", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "张三", "borrower_id_no": "110101199001011234",
            "lend_type": "bank_card", "principal": "50000",
            "lent_at": "2026-10-01", "repayment_method": "installments",
            "due_date": "2027-04-01", "fee_bearer": "borrower",
            "source_account_id": ids["a1"], "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "50000"},
    })).json()
    assert asset["success"] is True, asset
    asset_id = asset["data"]["id"]
    # 当前价值 = 未收回本金
    assert asset["data"]["current_value"] == "50000.00"

    # 账户减少 5 万
    acc = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]
    assert acc["balance"] == "50000.00", acc

    # 借出事件是「资金转出」，不是支出
    events = (await client.get("/api/v1/ledger/events", headers=h,
                               params={"page_size": 20})).json()["data"]["items"]
    lend_ev = next(e for e in events if e["event_type"] == "asset_acquire")
    assert lend_ev["flow_direction"] == "outflow"
    assert lend_ev["flow_direction_label"] == "资金转出"

    # 回款 2 万
    r = (await client.post(f"/api/v1/ledger/assets/receivables/{asset_id}/repayments",
                           headers=h, json={
        "amount": "20000", "account_id": ids["a1"], "occurred_at": "2026-10-20T10:00:00",
    })).json()
    assert r["success"] is True, r

    # 账户回到 7 万
    acc = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]
    assert acc["balance"] == "70000.00", acc

    # 资产当前价值降到 3 万（未收回本金）
    detail = (await client.get(f"/api/v1/ledger/assets/{asset_id}", headers=h)).json()["data"]
    assert detail["current_value"] == "30000.00", detail["current_value"]

    # 回款事件是「资金转入」，不是收入
    events = (await client.get("/api/v1/ledger/events", headers=h,
                               params={"page_size": 20})).json()["data"]["items"]
    repay_ev = next(e for e in events if e["event_type"] == "receivable_repay")
    assert repay_ev["flow_direction"] == "inflow"
    assert repay_ev["flow_direction_label"] == "资金转入"


async def test_lend_out_not_counted_as_expense(client):
    """借出不能进支出统计：否则「本月支出」会凭空多出 5 万。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    before = (await client.get("/api/v1/insights/expenses", headers=h)).json()["data"]

    await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给李四", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "李四", "borrower_id_no": "110101199001015678",
            "lend_type": "cash", "principal": "30000",
            "lent_at": "2026-10-01", "repayment_method": "lump_sum",
            "fee_bearer": "borrower", "source_account_id": ids["a1"],
            "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "30000"},
    })

    after = (await client.get("/api/v1/insights/expenses", headers=h)).json()["data"]
    # 支出总额不应因借出而增加
    def total(d):
        for key in ("total", "total_amount", "summary"):
            v = d.get(key)
            if isinstance(v, str):
                return v
            if isinstance(v, dict) and "total" in v:
                return v["total"]
        return "0"
    assert total(before) == total(after), f"借出不应计入支出：{total(before)} → {total(after)}"


async def test_id_no_is_required(client):
    """借款人身份证号必填：没有凭证属性的借出款不建议录入。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给王五", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "王五", "lend_type": "cash", "principal": "1000",
            "lent_at": "2026-10-01", "repayment_method": "lump_sum",
            "fee_bearer": "borrower", "source_account_id": ids["a1"],
            "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "1000"},
    })).json()
    assert r["success"] is False
    assert "borrower_id_no" in r["error"]["field_errors"]


async def test_existing_lend_does_not_touch_account(client):
    """补录既有借出：账户余额不变、不生成资金流水，但应收资产照常计入净资产。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    before = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"]
    before_events = (await client.get("/api/v1/ledger/events", headers=h)).json()["data"]["total"]

    asset = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "去年借给赵六", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "赵六", "borrower_id_no": "110101199001019999",
            "lend_type": "bank_card", "lent_mode": "existing", "principal": "20000",
            "lent_at": "2025-06-01", "repayment_method": "installments",
            "due_date": "2026-06-01", "fee_bearer": "borrower",
            "include_in_net_worth": True,
        },
        # 前端对补录传 mode=none
        "acquisition": {"mode": "none"},
    })).json()
    assert asset["success"] is True, asset
    assert asset["data"]["current_value"] == "20000.00"

    after = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"]
    assert after == before, f"补录不应改变账户余额：{before} → {after}"
    after_events = (await client.get("/api/v1/ledger/events", headers=h)).json()["data"]["total"]
    assert after_events == before_events, "补录不应生成资金流水"

    detail = (await client.get(f"/api/v1/ledger/assets/{asset['data']['id']}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["lent_mode"] == "existing"
    assert rec["lent_mode_label"] == "补录既有借出"


async def test_interest_split_on_repayment(client):
    """约定利息 + 回款拆分：本金冲减应收（不计收入），利息计入收入。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    before_income = (await client.get("/api/v1/insights/income", headers=h)).json()["data"]

    asset = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给孙七", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "孙七", "borrower_id_no": "110101199001017777",
            "lend_type": "bank_card", "lent_mode": "account", "principal": "10000",
            "lent_at": "2026-01-01", "repayment_method": "lump_sum",
            "due_date": "2026-12-31", "fee_bearer": "borrower",
            "source_account_id": ids["a1"], "include_in_net_worth": True,
            "interest_enabled": True, "interest_rate": "5",
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "10000"},
    })).json()
    assert asset["success"] is True, asset
    aid = asset["data"]["id"]

    # 约定计息但没填利率应被拦下（这里已填，验证序列化）
    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["interest_enabled"] is True
    assert rec["interest_rate"] == "5.00"

    # 回款 10500，其中利息 500
    r = (await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments",
                           headers=h, json={
        "amount": "10500", "interest_part": "500",
        "account_id": ids["a1"], "occurred_at": "2026-10-21T10:00:00",
    })).json()
    assert r["success"] is True, r

    # 应收归零（只冲本金 10000），利息 500 单独入账
    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["outstanding"] == "0.00", rec["outstanding"]
    assert rec["interest_received"] == "500.00"
    assert rec["settled"] is True

    events = (await client.get("/api/v1/ledger/events", headers=h,
                               params={"page_size": 30})).json()["data"]["items"]
    principal_ev = next(e for e in events if e["event_type"] == "receivable_repay")
    assert principal_ev["amount"] == "10000.00", principal_ev["amount"]
    assert principal_ev["flow_direction"] == "inflow"
    interest_ev = next(e for e in events if e["event_type"] == "income" and "利息" in e["title"])
    assert interest_ev["amount"] == "500.00"
    assert interest_ev["category_name"] == "利息收入"

    after_income = (await client.get("/api/v1/insights/income", headers=h)).json()["data"]
    assert after_income != before_income, "利息应计入收入统计"


async def test_interest_requires_rate(client):
    """勾了「约定利息」却没填利率 → 拦住。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给周八", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "周八", "borrower_id_no": "110101199001018888",
            "lend_type": "cash", "principal": "5000", "lent_at": "2026-01-01",
            "repayment_method": "lump_sum", "fee_bearer": "borrower",
            "source_account_id": ids["a1"], "include_in_net_worth": True,
            "interest_enabled": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "5000"},
    })).json()
    assert r["success"] is False
    assert "interest_rate" in r["error"]["field_errors"]


async def test_edit_receivable_is_persisted(client):
    """编辑借出款要真的生效（以前 update_asset 忽略 receivable，改动被静默丢弃）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给吴九", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "吴九", "borrower_id_no": "110101199001016666",
            "lend_type": "cash", "lent_mode": "existing", "principal": "3000",
            "lent_at": "2026-03-01", "repayment_method": "lump_sum",
            "fee_bearer": "borrower", "include_in_net_worth": True,
        },
        "acquisition": {"mode": "none"},
    })).json()["data"]
    aid = asset["id"]

    # 改还款方式、加利息约定；身份证留空 → 应保留原值
    r = (await client.put(f"/api/v1/ledger/assets/{aid}", headers=h, json={
        "receivable": {
            "borrower_name": "吴九", "borrower_id_no": "",
            "lend_type": "cash", "lent_mode": "existing", "principal": "3000",
            "lent_at": "2026-03-01", "repayment_method": "monthly_interest",
            "fee_bearer": "borrower", "include_in_net_worth": True,
            "interest_enabled": True, "interest_rate": "4.5",
        },
    })).json()
    assert r["success"] is True, r

    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["repayment_method"] == "monthly_interest", "还款方式改动应生效"
    assert rec["interest_enabled"] is True
    assert rec["interest_rate"] == "4.50"
    # 身份证留空不应被清掉
    assert rec["borrower_id_no"].replace("*", ""), "身份证不应被清空"
    assert rec["borrower_id_no"] != ""


async def test_cannot_change_principal_after_repayment(client):
    """已有回款后改本金会让「已还 + 未还」对不上，必须拒绝。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给郑十", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "郑十", "borrower_id_no": "110101199001015555",
            "lend_type": "cash", "principact": "0", "principal": "8000",
            "lent_at": "2026-03-01", "repayment_method": "installments",
            "fee_bearer": "borrower", "source_account_id": ids["a1"],
            "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "8000"},
    })).json()["data"]["id"]

    await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments", headers=h,
                      json={"amount": "2000", "account_id": ids["a1"],
                            "occurred_at": "2026-10-01T10:00:00"})

    r = (await client.put(f"/api/v1/ledger/assets/{aid}", headers=h, json={
        "receivable": {
            "borrower_name": "郑十", "lend_type": "cash", "lent_mode": "account",
            "principal": "10000", "lent_at": "2026-03-01",
            "repayment_method": "installments", "fee_bearer": "borrower",
            "source_account_id": ids["a1"], "include_in_net_worth": True,
        },
    })).json()
    assert r["success"] is False
    assert "本金" in r["error"]["message"]


# ---------------- 金额不固定的分期：总额对账 + 进度预警 ----------------

def _make_rec(principal="12000", lent="2026-01-01", due="2026-12-31"):
    """构造一个只用于计算口径的借出款对象（不写库）。"""
    from datetime import datetime
    from decimal import Decimal
    from app.db.models.assets import AssetReceivable
    rec = AssetReceivable()
    rec.principal = Decimal(principal)
    rec.lent_at = datetime.fromisoformat(lent + "T00:00:00")
    rec.due_date = datetime.fromisoformat(due + "T00:00:00")
    return rec


def test_reconcile_on_track():
    """按期还、进度领先时间 → on_track。"""
    from datetime import datetime
    from decimal import Decimal
    from app.services.receivables import build_reconcile

    rec = _make_rec()
    now = datetime(2026, 7, 1, 12, 0, 0)          # 已过约 181/364 天 → 时间进度 ~50%
    r = build_reconcile(rec, Decimal("7000"), [], now=now)
    assert r["status"] == "on_track", r
    assert r["paid_percent"] > r["time_percent"]
    assert r["days_left"] > 0


def test_reconcile_not_judged_by_uniform_progress():
    """约定是「期限内还完」，不是「均匀还」：前期还得少不判落后，只报客观指标。"""
    from datetime import datetime
    from decimal import Decimal
    from app.services.receivables import build_reconcile

    rec = _make_rec()
    now = datetime(2026, 7, 1, 12, 0, 0)
    r = build_reconcile(rec, Decimal("1000"), [], now=now)
    # 已还 8%、时间过了 50%：仍然不判 behind，给出「剩余 / 剩几天 / 每天需还」
    assert r["status"] == "on_track", r
    assert "每天需还" in r["status_label"]
    assert r["daily_needed"] is not None
    assert r["remaining"] == "11000.00"


def test_reconcile_overdue():
    """过了约定还清日还有余额 → overdue，报逾期天数与欠款。"""
    from datetime import datetime
    from decimal import Decimal
    from app.services.receivables import build_reconcile

    rec = _make_rec()
    now = datetime(2027, 2, 10, 9, 0, 0)
    r = build_reconcile(rec, Decimal("9000"), [], now=now)
    assert r["status"] == "overdue", r
    assert r["overdue_days"] > 0
    assert r["days_left"] == 0
    assert "逾期" in r["status_label"] and "3000" in r["status_label"]


def test_reconcile_settled_and_no_window():
    """结清后不再提示进度；没登记期限时只报总额。"""
    from datetime import datetime
    from decimal import Decimal
    from app.services.receivables import build_reconcile

    rec = _make_rec()
    r = build_reconcile(rec, Decimal("12000"), [], now=datetime(2026, 7, 1))
    assert r["status"] == "settled" and r["remaining"] == "0.00"

    rec2 = _make_rec()
    rec2.due_date = None
    r2 = build_reconcile(rec2, Decimal("3000"), [], now=datetime(2026, 7, 1))
    assert r2["status"] == "no_window"
    assert r2["time_percent"] is None
    assert r2["remaining"] == "9000.00"


async def test_repayment_period_label(client):
    """回款可标期次，且不同金额的多期回款都能正确累计。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给钱十一", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "钱十一", "borrower_id_no": "110101199001014444",
            "lend_type": "cash", "lent_mode": "account", "principal": "12000",
            "lent_at": "2026-01-01", "repayment_method": "installments",
            "due_date": "2026-12-31", "fee_bearer": "borrower",
            "source_account_id": ids["a1"], "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "12000"},
    })).json()["data"]["id"]

    # 三期金额各不相同
    for period, amount in ((1, "3000"), (2, "1500"), (3, "4000")):
        r = (await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments",
                               headers=h, json={
            "amount": amount, "period_no": period,
            "account_id": ids["a1"], "occurred_at": f"2026-0{period + 2}-10T10:00:00",
        })).json()
        assert r["success"] is True, r

    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["outstanding"] == "3500.00"          # 12000 - 8500
    assert rec["reconcile"]["repayment_count"] == 3
    assert rec["reconcile"]["paid"] == "8500.00"
    periods = sorted(r["period_no"] for r in rec["repayments"])
    assert periods == [1, 2, 3]
    assert rec["reconcile"]["status"] in ("on_track", "behind", "overdue")


def test_zero_repayment_near_due_is_flagged():
    """一分没还、期限又快到了：算不出节奏也要标「会逾期」。"""
    from datetime import datetime
    from decimal import Decimal
    from app.services.receivables import build_reconcile

    rec = _make_rec()                       # 12000，2026-01-01 → 2026-12-31
    now = datetime(2026, 10, 22, 9, 0, 0)   # 距到期约 70 天 → 超过 30 天阈值，不标
    r = build_reconcile(rec, Decimal("0"), [], now=now)
    assert r["will_be_late"] is False

    now_close = datetime(2026, 12, 23, 9, 0, 0)   # 剩 8 天且零回款
    r2 = build_reconcile(rec, Decimal("0"), [], now=now_close)
    assert r2["status"] == "at_risk"
    assert "尚未收到回款" in r2["status_label"]


def test_id_no_masked_front6_back6():
    """身份证按前 6 后 6 显示：既能确认是谁，又不泄露完整号码。"""
    from app.services.receivables import mask_id_no

    masked = mask_id_no("310227199501071419")
    assert masked == "310227******071419"
    assert masked[:6] == "310227" and masked[-6:] == "071419"
    assert mask_id_no(None) == ""
    assert mask_id_no("12345678901") == "*" * 11


async def test_existing_lend_not_in_net_worth_by_default(client):
    """补录既有借出默认不计入净资产：否则净资产会凭空多出一笔。

    从账户出借则必须计入（账户少了钱，应收要多回来对冲）。
    """
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 补录：显式不计入（前端切换登记方式时自动这样置）
    a1 = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "补录不计入-甲", "category_id": ids["lend_cat"],
        "include_in_net_worth": False,
        "receivable": {
            "borrower_name": "甲", "borrower_id_no": "110101199001011111",
            "lend_type": "cash", "lent_mode": "existing", "principal": "10000",
            "lent_at": "2025-01-01", "repayment_method": "lump_sum",
            "fee_bearer": "borrower", "include_in_net_worth": False,
        },
        "acquisition": {"mode": "none"},
    })).json()["data"]
    assert a1["include_in_net_worth"] is False

    # 从账户出借：计入
    a2 = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "出借计入-乙", "category_id": ids["lend_cat"],
        "include_in_net_worth": True,
        "receivable": {
            "borrower_name": "乙", "borrower_id_no": "110101199001012222",
            "lend_type": "cash", "lent_mode": "account", "principal": "5000",
            "lent_at": "2026-10-01", "repayment_method": "lump_sum",
            "fee_bearer": "borrower", "source_account_id": ids["a1"],
            "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "5000"},
    })).json()["data"]
    assert a2["include_in_net_worth"] is True


async def test_history_repayment_does_not_touch_account(client):
    """补录历史回款：只冲减应收，不生成资金流水、不动账户、不重复计入收入。

    场景：借出 10000，对方之前已还 3000（钱早花掉了），现在补录。
    """
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给历史甲", "category_id": ids["lend_cat"],
        "include_in_net_worth": False,
        "receivable": {
            "borrower_name": "历史甲", "borrower_id_no": "110101199001013333",
            "lend_type": "cash", "lent_mode": "existing", "principal": "10000",
            "lent_at": "2024-04-28", "repayment_method": "installments",
            "due_date": "2028-05-31", "fee_bearer": "borrower",
            "include_in_net_worth": False,
        },
        "acquisition": {"mode": "none"},
    })).json()["data"]["id"]

    bal_before = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"]
    ev_before = (await client.get("/api/v1/ledger/events", headers=h)).json()["data"]["total"]
    income_before = (await client.get("/api/v1/insights/income", headers=h)).json()["data"]

    r = (await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments",
                           headers=h, json={
        "amount": "3000", "affects_account": False,
        "occurred_at": "2025-06-10T10:00:00", "note": "历史回款补录",
    })).json()
    assert r["success"] is True, r

    # 应收减少，但账户与流水都不变
    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["outstanding"] == "7000.00"
    assert detail["current_value"] == "7000.00"

    bal_after = (await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)).json()["data"]["balance"]
    assert bal_after == bal_before, f"历史回款不应改变账户：{bal_before} → {bal_after}"
    ev_after = (await client.get("/api/v1/ledger/events", headers=h)).json()["data"]["total"]
    assert ev_after == ev_before, "历史回款不应生成资金流水"
    income_after = (await client.get("/api/v1/insights/income", headers=h)).json()["data"]
    # data_updated_at 每次请求都会变，只比数据本身
    strip = lambda d: {k: v for k, v in d.items() if k != "data_updated_at"}  # noqa: E731
    assert strip(income_after) == strip(income_before), "历史回款不应重复计入收入"

    # 记录上带「历史」标记
    assert rec["repayments"][0]["affects_account"] is False
    assert rec["repaid_total"] == "3000.00"


async def test_normal_repayment_still_needs_account(client):
    """正常回款（默认 affects_account=true）仍然必须选到账账户。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给正常乙", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "正常乙", "borrower_id_no": "110101199001014444",
            "lend_type": "cash", "lent_mode": "account", "principal": "5000",
            "lent_at": "2026-10-01", "repayment_method": "lump_sum",
            "fee_bearer": "borrower", "source_account_id": ids["a1"],
            "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "5000"},
    })).json()["data"]["id"]

    r = (await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments",
                           headers=h, json={"amount": "1000"})).json()
    assert r["success"] is False
    assert "account_id" in r["error"]["field_errors"]


# ---------------- 分期计划（金额每期不同） ----------------

async def test_periods_from_segments_and_allocation(client):
    """按分段生成 37 期（首期 6000 / 3200×22 / 5000×13 / 末 3100），回款按顺序分摊。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "分期计划-甲", "category_id": ids["lend_cat"],
        "include_in_net_worth": False,
        "receivable": {
            "borrower_name": "分期甲", "borrower_id_no": "110101199001015551",
            "lend_type": "bank_card", "lent_mode": "existing", "principal": "144500",
            "lent_at": "2024-04-28", "repayment_method": "installments",
            "due_date": "2028-05-31", "fee_bearer": "borrower",
            "include_in_net_worth": False,
        },
        "acquisition": {"mode": "none"},
    })).json()["data"]["id"]

    r = (await client.put(f"/api/v1/ledger/assets/receivables/{aid}/periods", headers=h,
                          json={
        "start_month": "2025-05",
        "segments": [
            {"count": 1, "amount": "6000"},
            {"count": 22, "amount": "3200"},
            {"count": 13, "amount": "5000"},
            {"count": 1, "amount": "3100"},
        ],
    })).json()
    assert r["success"] is True, r
    assert r["data"]["count"] == 37
    assert r["data"]["total"] == "144500.00"

    # 补录历史回款 57,200 → 应正好覆盖前 17 期
    await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments", headers=h, json={
        "amount": "57200", "affects_account": False, "occurred_at": "2026-10-01T00:00:00",
    })

    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    periods = rec["periods"]
    assert len(periods) == 37
    assert periods[0]["due_date"].startswith("2025-05")
    assert periods[0]["amount"] == "6000.00"
    assert periods[1]["amount"] == "3200.00"
    assert periods[23]["amount"] == "5000.00"
    assert periods[36]["amount"] == "3100.00"

    paid = [p for p in periods if p["status"] == "paid"]
    assert len(paid) == 17, f"应正好还完前 17 期，实际 {len(paid)}"
    assert periods[16]["period_no"] == 17 and periods[16]["status"] == "paid"
    assert periods[17]["period_no"] == 18 and periods[17]["status"] == "pending"
    assert rec["reconcile"]["paid_periods"] == 17
    assert rec["reconcile"]["unpaid_periods"] == 20


async def test_period_total_must_equal_principal(client):
    """分期合计与本金不一致要拦住，否则逐期对账永远差。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "分期计划-乙", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "分期乙", "borrower_id_no": "110101199001016662",
            "lend_type": "cash", "lent_mode": "existing", "principal": "10000",
            "lent_at": "2025-01-01", "repayment_method": "installments",
            "fee_bearer": "borrower", "include_in_net_worth": True,
        },
        "acquisition": {"mode": "none"},
    })).json()["data"]["id"]

    r = (await client.put(f"/api/v1/ledger/assets/receivables/{aid}/periods", headers=h,
                          json={"start_month": "2025-02",
                                "segments": [{"count": 3, "amount": "3000"}]})).json()
    assert r["success"] is False
    assert "不一致" in r["error"]["message"]


async def test_partial_repayment_marks_period_partial(client):
    """一笔回款不够还一期 → 该期标「部分」。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "分期计划-丙", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "分期丙", "borrower_id_no": "110101199001017773",
            "lend_type": "cash", "lent_mode": "account", "principal": "9600",
            "lent_at": "2026-10-01", "repayment_method": "installments",
            "fee_bearer": "borrower", "source_account_id": ids["a1"],
            "include_in_net_worth": True,
        },
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "9600"},
    })).json()["data"]["id"]

    await client.put(f"/api/v1/ledger/assets/receivables/{aid}/periods", headers=h,
                     json={"start_month": "2026-10",
                           "segments": [{"count": 3, "amount": "3200"}]})
    await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments", headers=h,
                      json={"amount": "1000", "account_id": ids["a1"],
                            "occurred_at": "2026-10-15T10:00:00"})

    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    periods = detail["related"]["receivable"]["periods"]
    assert periods[0]["status"] == "partial"
    assert periods[0]["paid_amount"] == "1000.00"
    assert periods[0]["unpaid"] == "2200.00"
    assert periods[1]["status"] == "pending"


async def test_repayment_period_range_is_automatic(client):
    """回款的期次由系统自动算：57,200 覆盖第 1-17 期，后续 3,200 落到第 18 期。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    aid = (await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "期次自动-甲", "category_id": ids["lend_cat"],
        "receivable": {
            "borrower_name": "期次甲", "borrower_id_no": "110101199001018881",
            "lend_type": "bank_card", "lent_mode": "existing", "principal": "144500",
            "lent_at": "2024-04-28", "repayment_method": "installments",
            "due_date": "2028-05-31", "fee_bearer": "borrower",
            "include_in_net_worth": False,
        },
        "acquisition": {"mode": "none"},
    })).json()["data"]["id"]

    await client.put(f"/api/v1/ledger/assets/receivables/{aid}/periods", headers=h, json={
        "start_month": "2025-05",
        "segments": [{"count": 1, "amount": "6000"}, {"count": 22, "amount": "3200"},
                     {"count": 13, "amount": "5000"}, {"count": 1, "amount": "3100"}],
    })
    # 历史回款 57,200：6000 + 3200×16 = 57,200，正好覆盖前 17 期
    await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments", headers=h, json={
        "amount": "57200", "affects_account": False, "occurred_at": "2026-10-01T00:00:00",
    })

    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["repayments"][0]["period_range"] == "1-17"

    # 再还 3,200 → 自动落到第 18 期
    await client.post(f"/api/v1/ledger/assets/receivables/{aid}/repayments", headers=h, json={
        "amount": "3200", "affects_account": False, "occurred_at": "2026-11-02T00:00:00",
    })
    detail = (await client.get(f"/api/v1/ledger/assets/{aid}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    ranges = {r["amount"]: r["period_range"] for r in rec["repayments"]}
    assert ranges["57200.00"] == "1-17", ranges
    assert ranges["3200.00"] == "18", ranges
    assert rec["reconcile"]["paid_periods"] == 18


def test_principal_of_interest_only_repayment():
    """纯利息回款（本金 0）不能被当成全额本金冲减应收。"""
    from decimal import Decimal

    from app.services.receivables import principal_of

    # 明确拆分：只收利息 → 本金为 0
    assert principal_of(Decimal("1000"), Decimal("0"), Decimal("50")) == Decimal("0")
    # 明确拆分：本金 800 + 利息 200
    assert principal_of(Decimal("1000"), Decimal("800"), Decimal("200")) == Decimal("800")
    # 历史记录（本金/利息都没拆）→ 整笔视为本金
    assert principal_of(Decimal("1000"), Decimal("0"), Decimal("0")) == Decimal("1000")
