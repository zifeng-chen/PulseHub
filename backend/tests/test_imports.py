"""数据导入验收测试（24 §7：人工审核式导入）。

覆盖：CSV 解析、字段映射、账户/分类匹配、金额/类型校验、重复检查、
草稿生成、批量确认、错误行隔离（不污染正式数据）。
"""
from __future__ import annotations

from tests.conftest import auth, complete_setup


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    ids: dict = {"headers": h}
    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "工资卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000", "include_in_net_worth": True})
    ids["a1"] = r.json()["data"]["id"]
    return ids


CSV_OK = "类型,标题,金额,日期,账户,分类,备注\n" \
         "收入,工资,20000,2026-10-01,工资卡,工资收入,10月工资\n" \
         "支出,餐饮,500,2026-10-02,工资卡,餐饮,午餐\n" \
         "支出,交通,300,2026-10-03,工资卡,交通,\n"


async def test_import_preview_create_confirm(client):
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 预览
    r = await client.post("/api/v1/ledger/imports/preview", headers=h,
                          json={"content": CSV_OK})
    assert r.json()["success"] is True, r.text
    prev = r.json()["data"]
    assert prev["total"] == 3
    assert prev["success"] == 3

    # 创建导入（生成草稿）
    r = await client.post("/api/v1/ledger/imports", headers=h,
                          json={"file_name": "流水.csv", "content": CSV_OK})
    assert r.json()["success"] is True, r.text
    batch = r.json()["data"]
    assert batch["status"] == "draft"
    assert batch["success_rows"] == 3
    batch_id = batch["id"]

    # 详情
    r = await client.get(f"/api/v1/ledger/imports/{batch_id}", headers=h)
    assert r.json()["success"] is True, r.text
    assert len(r.json()["data"]["records"]) == 3

    # 确认前账户余额不变（草稿不计入）
    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "100000.00"

    # 批量确认
    r = await client.post(f"/api/v1/ledger/imports/{batch_id}/confirm", headers=h)
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["confirmed"] == 3

    # 确认后账户余额 + 20000 − 500 − 300
    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "119200.00"

    # 收支统计
    r = await client.get("/api/v1/ledger/summary", headers=h)
    m = r.json()["data"]["metrics"]
    assert m["income"] == "20000.00"
    assert m["expense"] == "800.00"


async def test_import_error_and_duplicate_isolation(client):
    """24 §7：错误行与重复行隔离，不污染正式数据。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    csv_bad = "类型,标题,金额,日期,账户,分类\n" \
              "支出,正常支出,100,2026-10-01,工资卡,餐饮\n" \
              "支出,坏金额,abc,2026-10-02,工资卡,餐饮\n" \
              "转账,转账类型,100,2026-10-03,工资卡,餐饮\n" \
              "支出,正常支出,100,2026-10-01,工资卡,餐饮\n" \
              "支出,无账户,100,2026-10-04,不存在的卡,餐饮\n"

    r = await client.post("/api/v1/ledger/imports", headers=h,
                          json={"file_name": "bad.csv", "content": csv_bad})
    assert r.json()["success"] is True, r.text
    batch = r.json()["data"]
    assert batch["success_rows"] == 1   # 仅「正常支出」成功
    assert batch["error_rows"] == 3     # 坏金额 + 转账类型 + 无账户
    assert batch["duplicate_count"] == 1  # 重复支出

    # 确认后仅 1 条入账
    r = await client.post(f"/api/v1/ledger/imports/{batch['id']}/confirm", headers=h)
    assert r.json()["data"]["confirmed"] == 1
    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "99900.00"   # 100000 − 100


async def test_parse_text_to_drafts(client):
    """对话式录入：自然语言解析成草稿建议，且不落库。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    r = await client.post("/api/v1/ledger/parse-text", headers=h, json={
        "text": "昨天超市买菜 128 元\n10月15日 工资到账 12000\n加油 300",
    })
    assert r.json()["success"] is True, r.text
    data = r.json()["data"]
    assert data["total"] == 3
    items = data["items"]
    assert items[0]["event_type"] == "expense"
    assert items[0]["amount"] == "128.00"
    assert items[1]["event_type"] == "income"
    assert items[1]["amount"] == "12000.00"
    assert all("confidence" in i for i in items)

    # 解析仅为建议，不落库
    r = await client.get("/api/v1/ledger/events", headers=h)
    assert r.json()["data"]["total"] == 0


# ---------------- 与已有流水查重（同日期 + 同金额） ----------------

async def test_import_flags_rows_matching_existing_events(client):
    """导入的某笔与库里已有流水「金额 + 日期」相同时，要抽出来让用户确认。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 先手工记一笔：2026-10-05 支出 88 元
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "手工记的咖啡", "amount": "88",
        "occurred_at": "2026-10-05T09:30:00", "account_id": ids["a1"],
        "source": "manual",
    })
    existing = r.json()["data"]
    await client.post(f"/api/v1/ledger/events/{existing['id']}/confirm", headers=h, json={})

    csv_text = (
        "类型,标题,金额,日期,账户,分类,备注\n"
        "支出,星巴克,88,2026-10-05,工资卡,餐饮,\n"      # 与已有同日期同金额 → 疑似重复
        "支出,午餐,45,2026-10-05,工资卡,餐饮,\n"        # 金额不同 → 正常
    )
    preview = (await client.post("/api/v1/ledger/imports/preview", headers=h,
                                 json={"content": csv_text})).json()["data"]
    assert preview["suspected_duplicate"] == 1, preview
    hit = next(r for r in preview["rows"] if r["suspected_duplicate"])
    assert hit["suspected_duplicates"][0]["event_no"] == existing["event_no"]
    assert hit["suspected_duplicates"][0]["title"] == "手工记的咖啡"
    # 只是提示，不算错误、不算批次内重复
    assert hit["errors"] == [] and hit["duplicate"] is False


async def test_import_skip_rows_excludes_confirmed_duplicates(client):
    """用户确认「是同一笔」的行要跳过：不生成草稿事件，记录标 duplicate。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "手工记的咖啡", "amount": "88",
        "occurred_at": "2026-10-05T09:30:00", "account_id": ids["a1"], "source": "manual",
    })
    await client.post(f"/api/v1/ledger/events/{r.json()['data']['id']}/confirm", headers=h, json={})

    csv_text = (
        "类型,标题,金额,日期,账户,分类,备注\n"
        "支出,星巴克,88,2026-10-05,工资卡,餐饮,\n"
        "支出,午餐,45,2026-10-05,工资卡,餐饮,\n"
    )
    # 第 1 行用户确认是同一笔 → 跳过
    batch = (await client.post("/api/v1/ledger/imports", headers=h, json={
        "file_name": "alipay.csv", "content": csv_text, "skip_rows": [1],
    })).json()["data"]
    assert batch["duplicate_count"] == 1
    assert batch["success_rows"] == 1

    detail = (await client.get(f"/api/v1/ledger/imports/{batch['id']}", headers=h)).json()["data"]
    recs = detail["records"]
    assert recs[0]["status"] == "duplicate", recs[0]
    assert "已跳过" in (recs[0]["error"] or "")
    # 只有第 2 行生成了事件
    created = (await client.get("/api/v1/ledger/events", headers=h,
                                params={"page_size": 20})).json()["data"]["items"]
    titles = {e["title"] for e in created}
    assert "午餐" in titles and "星巴克" not in titles
