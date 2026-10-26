"""M2 资产与专项资产验收测试（15、18 §4、12 §2）。

覆盖：通用资产、类型必填、估值时间线与调整原因、资产购置/处置事件与双资金变动、
净资产并入资产、流动性归类与待估值统计、敏感字段掩码与权限、车位关联规则、
车辆保险到期识别、贵金属建议价值、水电煤户号、归档与二次确认令牌、类型切换限制。
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


async def _create_asset(client, headers, payload: dict) -> dict:
    r = await client.post("/api/v1/ledger/assets", headers=headers, json=payload)
    assert r.json()["success"] is True, r.text
    return r.json()["data"]


async def _confirm_token(client, headers, action: str, object_type: str, object_id: str) -> str:
    r = await client.post("/api/v1/settings/confirm-tokens", headers=headers,
                          json={"action": action, "object_type": object_type, "object_id": object_id})
    assert r.json()["success"] is True, r.text
    return r.json()["data"]["token"]


# ---------------- 通用资产与净资产 ----------------

async def test_generic_asset_and_net_worth(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "摄影器材", "currency": "CNY",
        "acquire_cost": "20000", "current_value": "15000", "value_source": "manual",
        "sub_type": "数字设备",
    })
    assert asset["asset_type_label"] == "其他资产"
    assert asset["liquidity"] == "non_current"
    assert asset["counts_in_net_worth"] is True

    r = await client.get("/api/v1/ledger/assets/summary", headers=h)
    summary = r.json()["data"]
    assert summary["count"] == 1
    assert summary["total_value"] == "15000.00"
    assert summary["non_current_value"] == "15000.00"
    assert summary["current_value"] == "0.00"

    # 净资产 = 账户余额 + 资产当前价值
    r = await client.get("/api/v1/ledger/summary", headers=h)
    nw = r.json()["data"]["net_worth"]
    assert nw["accounts"] == "100000.00"
    assert nw["assets"] == "15000.00"
    assert nw["net_worth"] == "115000.00"
    assert nw["included_assets"] == 1


async def test_type_required_fields(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "parking", "name": "地下车位", "extension": {"parking_type": "ownership"}})
    assert r.status_code == 422
    assert "extension.parking_no" in r.json()["error"]["field_errors"]

    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "precious_metal", "name": "金条", "extension": {"category": "gold"}})
    assert r.status_code == 422
    fields = r.json()["error"]["field_errors"]
    assert "extension.weight" in fields and "extension.unit" in fields


# ---------------- 不动产与敏感字段 ----------------

async def test_real_estate_fields_and_sensitive_masking(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "real_estate", "name": "城东自住房", "currency": "CNY",
        "acquire_cost": "1800000", "current_value": "2100000", "value_source": "appraisal",
        "extension": {
            "property_type": "residence", "title_no": "京(2020)东不动产权第0001号",
            "ownership_no": "NO-0001-XYZ", "address": "城东区示例路 1 号",
            "building_area": "128.5", "inner_area": "105.2", "land_area": "30",
            "built_year": 2018, "renovation_cost": "200000", "tax_cost": "36000",
            "ownership_ratio": "0.5", "has_mortgage": True,
        },
    })
    ext = asset["extension"]
    assert ext["property_type_label"] == "住宅"
    assert ext["building_area"] == "128.5000"
    assert ext["has_mortgage"] is True
    # 敏感字段默认掩码
    assert "*" in ext["title_no"]
    assert ext["title_no"] != "京(2020)东不动产权第0001号"

    # 审计查看权限可看完整值
    r = await client.get(f"/api/v1/ledger/assets/{asset['id']}?reveal=1", headers=h)
    assert r.json()["data"]["extension"]["title_no"] == "京(2020)东不动产权第0001号"

    # 房贷明细由负债模块管理：资产侧只保留标记与说明
    assert "负债模块" in asset["pending_dependency_note"]


async def test_real_estate_utility_accounts(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "real_estate", "name": "城南出租房",
        "extension": {"property_type": "residence", "address": "城南路 9 号"},
    })
    r = await client.post(f"/api/v1/ledger/assets/real-estates/{asset['id']}/utility-accounts",
                          headers=h, json={
                              "utility_type": "electricity", "provider": "某电力公司",
                              "account_no": "123456789012", "billing_cycle": "monthly",
                              "billing_day": 10, "default_account_id": ids["a1"]})
    assert r.json()["success"] is True, r.text
    utility = r.json()["data"]
    assert utility["utility_type_label"] == "电费"
    assert utility["account_no"] == "1234 5678 9012"

    r = await client.get(f"/api/v1/ledger/assets/real-estates/{asset['id']}/utility-accounts",
                         headers=h)
    assert len(r.json()["data"]["items"]) == 1

    # 户号必须挂在不动产上：非不动产资产不允许维护
    other = await _create_asset(client, h, {"asset_type": "other", "name": "杂物"})
    r = await client.post(f"/api/v1/ledger/assets/real-estates/{other['id']}/utility-accounts",
                          headers=h, json={"utility_type": "water"})
    assert r.status_code == 422


# ---------------- 车位与关联规则 ----------------

async def test_parking_link_rules(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    house = await _create_asset(client, h, {
        "asset_type": "real_estate", "name": "自住房",
        "extension": {"property_type": "residence"},
    })
    parking = await _create_asset(client, h, {
        "asset_type": "parking", "name": "B1-088 车位", "current_value": "180000",
        "extension": {"parking_type": "ownership", "parking_no": "B1-088",
                      "linked_real_estate_id": house["id"], "management_fee": "1200",
                      "management_fee_cycle": "yearly"},
    })
    assert parking["extension"]["linked_real_estate_name"] == "自住房"

    # 一个车位最多关联一套不动产；一套不动产可有多个车位
    parking2 = await _create_asset(client, h, {
        "asset_type": "parking", "name": "B2-102 车位",
        "extension": {"parking_type": "usage_right", "parking_no": "B2-102",
                      "linked_real_estate_id": house["id"]},
    })
    assert parking2["extension"]["linked_real_estate_id"] == house["id"]

    # 只能关联不动产类型
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "parking", "name": "错链车位",
        "extension": {"parking_type": "ownership", "parking_no": "X-1",
                      "linked_real_estate_id": parking["id"]}})
    assert r.status_code == 422
    assert "extension.linked_real_estate_id" in r.json()["error"]["field_errors"]

    # 解除关联：字段置空并保留审计
    r = await client.put(f"/api/v1/ledger/assets/{parking['id']}", headers=h, json={
        "version": parking["version"], "extension": {"linked_real_estate_id": None},
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["extension"]["linked_real_estate_id"] is None


# ---------------- 车辆与保险 ----------------

async def test_vehicle_and_insurance_expiry(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    vehicle = await _create_asset(client, h, {
        "asset_type": "vehicle", "name": "家用车", "acquire_cost": "180000",
        "current_value": "140000",
        "extension": {"brand": "示例牌", "series": "S1", "vehicle_type": "轿车",
                      "energy_type": "electric", "vin": "LSV1234567890ABCD",
                      "plate_no": "沪A12345", "mileage": "18500.5",
                      "usage_status": "in_use", "has_loan": True},
    })
    ext = vehicle["extension"]
    assert ext["energy_type_label"] == "纯电"
    assert "*" in ext["vin"]
    assert "*" in ext["plate_no"]

    soon = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
    r = await client.post(f"/api/v1/ledger/assets/vehicles/{vehicle['id']}/insurances",
                          headers=h, json={
                              "insurance_type": "compulsory", "insurer": "某保险公司",
                              "policy_no": "P-001", "effective_date": "2026-01-01",
                              "expiry_date": soon, "premium": "950"})
    assert r.json()["success"] is True, r.text
    insurance = r.json()["data"]
    assert insurance["insurance_type_label"] == "交强险"
    assert insurance["expiring_soon"] is True
    assert 0 <= insurance["days_to_expiry"] <= 30

    r = await client.get(f"/api/v1/ledger/assets/vehicles/{vehicle['id']}/insurances",
                         headers=h)
    assert len(r.json()["data"]["items"]) == 1


# ---------------- 贵金属 ----------------

async def test_precious_metal_auto_valuation(client):
    """贵金属：品类由分类表达，计价方式/克重/纯度决定，当前价值按金价自动估算。"""
    from app.services import metal_price as price_service

    price_service.set_cached_payload({
        "available": True, "stale": False, "source": "测试行情", "gold": {"price": "890.00", "unit": "元/克"},
        "recycle": [], "recycle_gold": "876", "fetched_at": "2026-10-21T00:00:00+00:00", "date": "2026-10-21",
    })
    try:
        ids = await _bootstrap(client)
        h = ids["headers"]
        metal = await _create_asset(client, h, {
            "asset_type": "precious_metal", "name": "投资金条 100g",
            "extension": {"pricing_mode": "by_weight", "purity": "999.9", "weight": "100",
                          "unit": "g", "unit_price": "600", "processing_fee": "200",
                          "brand": "周大福", "brand_code": "chowtaifook",
                          "purchase_channel": "线下门店", "certificate_no": "CERT-0001"},
        })
        ext = metal["extension"]
        assert ext["pricing_mode"] == "by_weight"
        assert ext["pricing_mode_label"] == "克重计价"
        # 100g × 0.9999 × 890 = 88991.10
        assert metal["current_value"] == "88991.10"
        # 购置成本 = 100 × 600 + 200 = 60200
        assert metal["acquire_cost"] == "60200.00"
        assert metal["value_source"] == "market"
        assert "*" in ext["certificate_no"]
    finally:
        price_service.set_cached_payload(None)


async def test_precious_metal_flat_price(client):
    """一口价：购入成本取一口价；当前价值仍按克重 × 金价估算。"""
    from app.services import metal_price as price_service

    price_service.set_cached_payload({
        "available": True, "stale": False, "source": "测试行情", "gold": {"price": "890.00", "unit": "元/克"},
        "recycle": [], "fetched_at": "2026-10-21T00:00:00+00:00",
    })
    try:
        ids = await _bootstrap(client)
        h = ids["headers"]
        metal = await _create_asset(client, h, {
            "asset_type": "precious_metal", "name": "生肖金饰",
            "extension": {"pricing_mode": "flat", "flat_price": "3600", "purity": "999",
                          "weight": "3", "unit": "g"},
        })
        assert metal["extension"]["flat_price"] == "3600.00"
        assert metal["acquire_cost"] == "3600.00"
        # 3 × 0.999 × 890 = 2667.33
        assert metal["current_value"] == "2667.33"
    finally:
        price_service.set_cached_payload(None)


async def test_metal_brands_and_prices_api(client):
    """品牌字典（含中国香港/中国澳门品牌）与行情接口可用，行情不可用时不阻断。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    brands = (await client.get("/api/v1/ledger/metal-brands", headers=h)).json()["data"]
    names = [b["name"] for b in brands["brands"]]
    assert "周大福" in names and "六福珠宝" in names and "老凤祥" in names
    assert any("银行" in n for n in names)
    # 购买渠道随品牌联动
    by_code = {b["code"]: b for b in brands["brands"]}
    assert "线下门店" in by_code["chowtaifook"]["channels"]
    assert "银行柜面" in by_code["icbc_bar"]["channels"]

    prices = (await client.get("/api/v1/ledger/metal-prices", headers=h)).json()["data"]
    assert "available" in prices and "gold" in prices


# ---------------- 估值时间线与调整原因 ----------------

async def test_valuation_timeline_and_reason_required(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "收藏品", "current_value": "10000",
        "value_source": "manual",
    })
    # 初始价值写入时间线
    r = await client.get(f"/api/v1/ledger/assets/{asset['id']}/valuations", headers=h)
    assert len(r.json()["data"]["items"]) == 1

    # 调整已确认价值必须填写原因
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/valuations", headers=h,
                          json={"value": "12000", "source": "appraisal"})
    assert r.status_code == 422
    assert "reason" in r.json()["error"]["field_errors"]

    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/valuations", headers=h,
                          json={"value": "12000", "source": "appraisal",
                                "reason": "第三方评估机构重新评估", "source_note": "某评估所 2026-10"})
    assert r.json()["success"] is True, r.text

    r = await client.get(f"/api/v1/ledger/assets/{asset['id']}", headers=h)
    data = r.json()["data"]
    assert data["current_value"] == "12000.00"
    assert data["value_source"] == "appraisal"
    assert len(data["valuations"]) == 2
    assert data["valuations"][0]["reason"] == "第三方评估机构重新评估"


# ---------------- 资产购置 / 处置事件 ----------------

async def test_asset_acquire_changes_account_and_net_worth(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "笔记本电脑", "currency": "CNY",
        "acquire_cost": "12000", "current_value": "9000",
        "acquisition": {"mode": "confirm", "account_id": ids["a1"], "amount": "12000"},
    })
    assert asset["acquisition_event"]["status"] == "confirmed"

    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "88000.00"       # 100000 − 12000

    r = await client.get(f"/api/v1/ledger/assets/{asset['id']}", headers=h)
    events = r.json()["data"]["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "asset_acquire"
    assert len(events[0]["movements"]) == 2                  # 账户转出 + 资产转入
    targets = {m["target_type"] for m in events[0]["movements"]}
    assert targets == {"account", "asset"}

    r = await client.get("/api/v1/ledger/summary", headers=h)
    nw = r.json()["data"]["net_worth"]
    assert nw["accounts"] == "88000.00"
    assert nw["assets"] == "9000.00"
    assert nw["net_worth"] == "97000.00"


async def test_asset_acquire_draft_then_confirm(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "家具", "acquire_cost": "5000", "current_value": "5000",
        "acquisition": {"mode": "draft", "account_id": ids["a1"], "amount": "5000"},
    })
    assert asset["acquisition_event"]["status"] == "draft"
    event_id = asset["acquisition_event"]["id"]

    # 草稿不改变账户余额
    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "100000.00"

    r = await client.get(f"/api/v1/ledger/events/{event_id}", headers=h)
    event = r.json()["data"]
    r = await client.post(f"/api/v1/ledger/events/{event_id}/confirm", headers=h,
                          json={"version": event["version"]})
    assert r.json()["success"] is True, r.text
    assert len(r.json()["data"]["movements"]) == 2

    r = await client.get(f"/api/v1/ledger/accounts/{ids['a1']}", headers=h)
    assert r.json()["data"]["balance"] == "95000.00"


async def test_asset_dispose_requires_token_and_reason_and_reversal(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "旧手机", "current_value": "2000",
    })
    # 缺少令牌
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/dispose", headers=h,
                          json={"dispose_type": "sold", "amount": "1500",
                                "account_id": ids["a1"], "reason": "换新机"})
    assert r.status_code == 422
    assert "confirm_token" in r.json()["error"]["field_errors"]

    # 缺少去向账户（有收入）
    token = await _confirm_token(client, h, "asset.dispose", "asset", asset["id"])
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/dispose", headers=h,
                          json={"dispose_type": "sold", "amount": "1500",
                                "reason": "换新机", "confirm_token": token})
    assert r.status_code == 422
    assert "account_id" in r.json()["error"]["field_errors"]

    # 正常处置
    token = await _confirm_token(client, h, "asset.dispose", "asset", asset["id"])
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/dispose", headers=h,
                          json={"dispose_type": "sold", "amount": "1500",
                                "account_id": ids["a1"], "reason": "换新机",
                                "confirm_token": token})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["status"] == "disposed"
    event_id = r.json()["data"]["event_id"]

    # 已处置资产不再参与净资产
    r = await client.get("/api/v1/ledger/summary", headers=h)
    nw = r.json()["data"]["net_worth"]
    assert nw["assets"] == "0.00"
    assert nw["accounts"] == "101500.00"      # 100000 + 1500

    # 重复处置被拦截
    token = await _confirm_token(client, h, "asset.dispose", "asset", asset["id"])
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/dispose", headers=h,
                          json={"dispose_type": "sold", "amount": "1",
                                "account_id": ids["a1"], "reason": "重复",
                                "confirm_token": token})
    assert r.status_code == 409

    # 冲销处置事件：资产恢复处置前状态
    r = await client.get(f"/api/v1/ledger/events/{event_id}", headers=h)
    event = r.json()["data"]
    r = await client.post(f"/api/v1/ledger/events/{event_id}/reverse", headers=h,
                          json={"version": event["version"], "reason": "误操作"})
    assert r.json()["success"] is True, r.text
    r = await client.get(f"/api/v1/ledger/assets/{asset['id']}", headers=h)
    assert r.json()["data"]["status"] == "active"


async def test_archive_requires_token_and_reason(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {"asset_type": "other", "name": "旧家具"})
    token = await _confirm_token(client, h, "asset.archive", "asset", asset["id"])
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/archive", headers=h,
                          json={"reason": "", "confirm_token": token})
    assert r.status_code == 422
    assert "reason" in r.json()["error"]["field_errors"]

    token = await _confirm_token(client, h, "asset.archive", "asset", asset["id"])
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/archive", headers=h,
                          json={"reason": "已停止使用", "confirm_token": token})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["status"] == "archived"


# ---------------- 类型切换与统计口径 ----------------

async def test_type_switch_blocked_when_business_data(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "待定资产", "current_value": "1000",
    })
    # 有估值记录 → 不允许直接切换类型
    r = await client.put(f"/api/v1/ledger/assets/{asset['id']}", headers=h,
                         json={"version": asset["version"], "asset_type": "vehicle"})
    assert r.status_code == 409
    assert "asset_type" in r.json()["error"]["field_errors"]

    # 无业务数据的资产可以切换
    plain = await _create_asset(client, h, {"asset_type": "other", "name": "空资产"})
    r = await client.put(f"/api/v1/ledger/assets/{plain['id']}", headers=h,
                         json={"version": plain["version"], "asset_type": "real_estate",
                               "extension": {"property_type": "residence"}})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["asset_type"] == "real_estate"


async def test_summary_liquidity_and_pending_valuation(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    await _create_asset(client, h, {
        "asset_type": "precious_metal", "name": "银条", "current_value": "8000",
        "extension": {"category": "silver", "weight": "500", "unit": "g"},
    })
    await _create_asset(client, h, {
        "asset_type": "real_estate", "name": "未估值房产",
        "extension": {"property_type": "residence"},
    })
    r = await client.get("/api/v1/ledger/assets/summary", headers=h)
    summary = r.json()["data"]
    assert summary["count"] == 2
    assert summary["current_value"] == "8000.00"       # 贵金属 = 流动资产
    assert summary["non_current_value"] == "0.00"
    assert summary["pending_valuation_count"] == 1        # 未估值房产

    # 列表筛选：类型 + 流动性
    r = await client.get("/api/v1/ledger/assets?asset_type=precious_metal", headers=h)
    assert len(r.json()["data"]["items"]) == 1
    r = await client.get("/api/v1/ledger/assets?liquidity=non_current", headers=h)
    assert len(r.json()["data"]["items"]) == 1

    # 分类型列表
    r = await client.get("/api/v1/ledger/assets/real-estates", headers=h)
    assert len(r.json()["data"]["items"]) == 1
    assert r.json()["data"]["items"][0]["asset_type"] == "real_estate"


async def test_asset_update_current_value_requires_reason(client):
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "设备", "current_value": "5000", "value_source": "manual",
    })
    r = await client.put(f"/api/v1/ledger/assets/{asset['id']}", headers=h,
                         json={"version": asset["version"], "current_value": "6000"})
    assert r.status_code == 422
    assert "reason" in r.json()["error"]["field_errors"]

    r = await client.put(f"/api/v1/ledger/assets/{asset['id']}", headers=h,
                         json={"version": asset["version"], "current_value": "6000",
                               "reason": "市场价格上涨"})
    assert r.json()["success"] is True, r.text
    # 调整写入估值时间线
    r = await client.get(f"/api/v1/ledger/assets/{asset['id']}/valuations", headers=h)
    assert len(r.json()["data"]["items"]) == 2


async def test_vehicle_expense_linked_to_asset(client):
    """15 §5.4 / 23 §4：费用进入统一流水事件并保留资产关联（车辆关联 ID）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    vehicle = await _create_asset(client, h, {
        "asset_type": "vehicle", "name": "家用轿车", "currency": "CNY",
        "acquire_cost": "120000", "current_value": "100000", "value_source": "manual",
        "extension": {"brand": "示例品牌"},
    })

    # 创建支出草稿并关联到车辆
    r = await client.post("/api/v1/ledger/events", headers=h, json={
        "event_type": "expense", "title": "加油", "amount": "300",
        "account_id": ids["a1"], "asset_id": vehicle["id"], "source": "manual",
    })
    assert r.json()["success"] is True, r.text
    event_id = r.json()["data"]["id"]

    r = await client.post(f"/api/v1/ledger/events/{event_id}/confirm", headers=h, json={})
    assert r.json()["success"] is True, r.text

    # 事件详情保留资产关联
    r = await client.get(f"/api/v1/ledger/events/{event_id}", headers=h)
    assert r.json()["data"]["asset_id"] == vehicle["id"]

    # 资产详情「费用流水」可追溯到该事件
    r = await client.get(f"/api/v1/ledger/assets/{vehicle['id']}", headers=h)
    asset_detail = r.json()["data"]
    linked = [e for e in asset_detail["events"] if e["id"] == event_id]
    assert len(linked) == 1
    assert linked[0]["event_type"] == "expense"

    # 支出已计入统计
    r = await client.get("/api/v1/ledger/summary", headers=h)
    assert float(r.json()["data"]["metrics"]["expense"]) >= 300


async def test_asset_category_assignment(client):
    """08 §3：资产接入「资产」分类域，可关联分类、回填分类标签并按分类筛选。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    # 读取资产分类域默认分类（setup 种子：不动产/车位/车辆/贵金属/其他资产）
    r = await client.get("/api/v1/settings/categories?domain=asset", headers=h)
    cats = r.json()["data"]["items"]
    assert len(cats) >= 5
    target = next((c for c in cats if c["name"] == "其他资产"), cats[0])

    # 创建资产并关联分类
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "收藏手办", "current_value": "3000",
        "category_id": target["id"],
    })
    assert asset["category_id"] == target["id"]
    assert asset["category_label"] == target["name"]

    # 列表按分类筛选
    r = await client.get(f"/api/v1/ledger/assets?category_id={target['id']}", headers=h)
    items = r.json()["data"]["items"]
    assert any(i["id"] == asset["id"] for i in items)

    # 详情返回分类标签
    r = await client.get(f"/api/v1/ledger/assets/{asset['id']}", headers=h)
    assert r.json()["data"]["category_label"] == target["name"]

    # 编辑更换分类
    r = await client.put(f"/api/v1/ledger/assets/{asset['id']}", headers=h,
                         json={"version": asset["version"], "category_id": None})
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["category_id"] is None
    assert r.json()["data"]["category_label"] == ""


async def test_precious_metal_realized_gain(client):
    """15 §6.2：贵金属处置计算已实现损益 = 处置收入 − (购置成本 + 加工费 + 税费)。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    metal = await _create_asset(client, h, {
        "asset_type": "precious_metal", "name": "投资金条", "currency": "CNY",
        "acquire_cost": "48000", "current_value": "52000", "value_source": "manual",
        "extension": {"category": "gold", "weight": "100", "unit": "g",
                      "purity": "0.999", "processing_fee": "200", "tax_cost": "100"},
    })

    token = await _confirm_token(client, h, "asset.dispose", "asset", metal["id"])
    r = await client.post(f"/api/v1/ledger/assets/{metal['id']}/dispose", headers=h, json={
        "dispose_type": "sold", "amount": "52000", "account_id": ids["a1"],
        "reason": "金价上涨止盈", "confirm_token": token,
    })
    assert r.json()["success"] is True, r.text
    # 已实现损益 = 52000 − (48000 + 200 + 100) = 3700
    assert r.json()["data"]["realized_gain"] == "3700.00"

    # 事件详情携带损益
    event_id = r.json()["data"]["event_id"]
    r = await client.get(f"/api/v1/ledger/events/{event_id}", headers=h)
    assert r.json()["data"]["realized_gain"] == "3700.00"


async def test_asset_type_derived_from_category(client):
    """资产类型由所选分类推导：否则从总览页新增的房产会落到 other，
    导致负债（房贷）与租约的「关联资产」都选不到它（回归）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]

    cats = (await client.get("/api/v1/settings/categories", headers=h,
                             params={"domain": "asset"})).json()["data"]["items"]
    by_name = {c["name"]: c for c in cats}
    assert "不动产" in by_name and "住宅" in by_name and "燃油车" in by_name

    # 只传子分类「住宅」，不传 asset_type
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "name": "测试房产", "category_id": by_name["住宅"]["id"], "currency": "CNY",
        "acquire_cost": "800000", "current_value": "800000",
        "extension": {"property_type": "residence"},
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["asset_type"] == "real_estate", r.text

    # 不动产专用列表能查到（负债/租赁页就是从这里取候选）
    r = await client.get("/api/v1/ledger/assets/real-estates", headers=h)
    assert any(a["name"] == "测试房产" for a in r.json()["data"]["items"]), r.text

    # 车辆 → vehicle
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "name": "测试车", "category_id": by_name["燃油车"]["id"], "currency": "CNY",
        "acquire_cost": "150000", "current_value": "150000",
        "extension": {"brand": "大众"},
    })
    assert r.json()["data"]["asset_type"] == "vehicle", r.text

    # 车位：现在是不动产的子分类，推导出的类型应是 real_estate（不再是独立 parking）
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "name": "测试车位", "category_id": by_name["车位"]["id"], "currency": "CNY",
        "acquire_cost": "80000", "current_value": "80000",
        "extension": {"property_type": "garage"},
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["asset_type"] == "real_estate", r.text


async def test_disposed_asset_cannot_be_reactivated_directly(client):
    """已处置资产不能直接改回「在用」：处置资金已入账，直接复活会让净资产重复计。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    asset = await _create_asset(client, h, {
        "asset_type": "other", "name": "旧平板", "current_value": "1000",
    })
    token = await _confirm_token(client, h, "asset.dispose", "asset", asset["id"])
    r = await client.post(f"/api/v1/ledger/assets/{asset['id']}/dispose", headers=h, json={
        "dispose_type": "sold", "amount": "800", "account_id": ids["a1"],
        "reason": "闲置出售", "confirm_token": token,
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["status"] == "disposed"

    r = await client.put(f"/api/v1/ledger/assets/{asset['id']}", headers=h, json={"status": "active"})
    assert r.status_code == 409, r.text
    assert "冲销" in r.json()["error"]["message"]
