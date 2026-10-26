"""资产扩展字段「存得进、读得出」的全量回归。

背景：住宅表单一直收集「物业费单价 / 参考单价」，但这两个字段既不在模型里、
也不在扩展字段白名单里，保存时被静默丢弃 —— 用户点编辑发现输入框是空的。

这里不逐个断言业务语义，而是对每种资产类型的**全部**扩展字段做一次
创建 → 读取 → 编辑 → 再读取 的往返，任何字段没接进模型 / 没接进序列化都会失败。
新增扩展字段时请顺带把值放进 FIELD_VALUES。
"""

from __future__ import annotations

from app.services import asset_extensions as ext

from tests.conftest import auth, complete_setup

# 每种类型挑一个非空值；枚举必须取自对应枚举字典的首项
FIELD_VALUES: dict[str, dict[str, object]] = {
    "real_estate": {
        "property_type": "residence", "title_no": "京(2020)东不动产权第 0001 号",
        "ownership_no": "1101012020000001", "address": "北京市朝阳区某某路 8 号院 3 号楼 1201",
        "building": "3 号楼", "unit": "2 单元", "floor": "12", "room_no": "1201",
        "building_area": "89.5", "inner_area": "72.3", "land_area": "12.4",
        "orientation": "南北", "layout": "两室一厅", "built_year": 2010,
        "delivery_date": "2012-06-30", "renovation_cost": "180000", "tax_cost": "36000",
        "agency_fee": "27000", "management_fee_price": "3.5", "unit_price": "65000",
        "ownership_ratio": "0.5", "has_mortgage": True, "note": "自住",
    },
    "parking": {
        "parking_type": "ownership", "certificate_no": "沪(2021)浦字第 0002 号",
        "parking_no": "B1-088", "area_name": "地下二层 A 区", "area": "12.5",
        "management_fee": "120", "management_fee_cycle": "monthly", "note": "临电梯口",
    },
    "vehicle": {
        "brand": "丰田", "series": "凯美瑞", "model": "2.0G", "vin": "LVGBE40K8GG123456",
        "plate_no": "沪A12345", "engine_no": "2AR1234567", "vehicle_type": "轿车",
        "energy_type": "fuel", "register_date": "2021-03-15", "mileage": "45000",
        "color": "白色", "usage_status": "in_use", "has_loan": True, "note": "家用",
    },
    "precious_metal": {
        "brand": "中国黄金", "brand_code": "chinagold", "certificate_no": "AU2021001",
        "purchase_channel": "线下门店", "pricing_mode": "by_weight", "flat_price": "0",
        "purity": "999.9", "weight": "100", "unit": "g",
        "unit_price": "480", "processing_fee": "200", "tax_cost": "0",
        "storage_location": "家中保险柜", "note": "长期持有",
    },
}

_NUMERIC_KEYS = ext._MONEY_FIELDS | ext._AREA_FIELDS | {"ownership_ratio"}
# 关联类字段指向另一条资产，单独验证「键存在」，不参与值比对
_LINK_FIELDS = {"linked_real_estate_id", "parking_space_id"}


def _payload(asset_type: str) -> dict:
    values = FIELD_VALUES[asset_type]
    missing = [f for f in ext.EXT_FIELDS[asset_type]
               if f not in values and f not in _LINK_FIELDS]
    assert not missing, f"{asset_type} 缺少测试值：{missing}"
    return {"extension": {k: v for k, v in values.items() if k in ext.EXT_FIELDS[asset_type]}}


async def _bootstrap(client) -> dict:
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    return {"headers": h}


def _assert_same(field: str, expected, actual) -> None:
    # 敏感字段（证件号 / VIN / 车牌）默认返回掩码，掩码只验证「有值」
    if isinstance(actual, str) and "*" in actual:
        return
    if expected is None:
        assert actual in (None, ""), f"{field}: 期望空，实际 {actual!r}"
        return
    if isinstance(expected, bool):
        assert bool(actual) is expected, f"{field}: 期望 {expected}，实际 {actual!r}"
        return
    if field in _NUMERIC_KEYS or field in ext._AREA_FIELDS:
        assert float(actual) == float(expected), f"{field}: 期望 {expected}，实际 {actual!r}"
        return
    if field in ext._INT_FIELDS:
        assert int(actual) == int(expected), f"{field}: 期望 {expected}，实际 {actual!r}"
        return
    if field in ext._DATE_FIELDS:
        assert str(actual)[:10] == str(expected)[:10], f"{field}: 期望 {expected}，实际 {actual!r}"
        return
    assert str(actual) == str(expected), f"{field}: 期望 {expected}，实际 {actual!r}"


async def _roundtrip(client, asset_type: str) -> None:
    ids = await _bootstrap(client)
    h = ids["headers"]
    values = FIELD_VALUES[asset_type]

    body = {
        "asset_type": asset_type,
        "name": f"扩展字段回归-{asset_type}",
        "currency": "CNY",
        "acquire_cost": "100000",
        "current_value": "120000",
        "extension": values,
    }
    r = await client.post("/api/v1/ledger/assets", headers=h, json=body)
    assert r.json()["success"] is True, r.text
    created = r.json()["data"]

    detail = (await client.get(
        f"/api/v1/ledger/assets/{created['id']}", headers=h)).json()["data"]
    for field in ext.EXT_FIELDS[asset_type]:
        if field not in (detail.get("extension") or {}):
            raise AssertionError(f"{asset_type}.{field} 没有写回序列化结果（创建）")
        if field in _LINK_FIELDS:
            continue
        _assert_same(field, values[field], detail["extension"][field])

    # 编辑：改两个代表性字段，确认能落库并被读回
    change = {k: values[k] for k in ext.EXT_FIELDS[asset_type] if k in values}
    if asset_type == "real_estate":
        change["management_fee_price"] = "4.2"
        change["unit_price"] = "72000"
    elif asset_type == "precious_metal":
        change["unit_price"] = "600"
    update = {
        "name": created["name"],
        "acquire_cost": "100000",
        "current_value": "120000",
        "version": created["version"],
        "extension": change,
    }
    r = await client.put(f"/api/v1/ledger/assets/{created['id']}", headers=h, json=update)
    assert r.json()["success"] is True, r.text

    detail = (await client.get(
        f"/api/v1/ledger/assets/{created['id']}", headers=h)).json()["data"]
    for field, expected in change.items():
        if field in _LINK_FIELDS:
            continue
        _assert_same(field, expected, (detail.get("extension") or {}).get(field))


async def test_real_estate_extension_roundtrip(client):
    await _roundtrip(client, "real_estate")


async def test_parking_extension_roundtrip(client):
    await _roundtrip(client, "parking")


async def test_vehicle_extension_roundtrip(client):
    await _roundtrip(client, "vehicle")


async def test_precious_metal_extension_roundtrip(client):
    await _roundtrip(client, "precious_metal")


async def test_management_fee_price_and_unit_price_persist(client):
    """住宅的物业费单价与参考单价必须能存下来（回归历史 bug）。"""
    ids = await _bootstrap(client)
    h = ids["headers"]
    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "real_estate", "name": "首创悦都45号", "currency": "CNY",
        "acquire_cost": "3000000", "current_value": "3200000",
        "extension": {
            "property_type": "residence", "building_area": "89.5",
            "management_fee_price": "3.5", "unit_price": "65000",
        },
    })
    assert r.json()["success"] is True, r.text
    asset_id = r.json()["data"]["id"]

    detail = (await client.get(f"/api/v1/ledger/assets/{asset_id}", headers=h)).json()["data"]
    ext_data = detail["extension"]
    assert float(ext_data["management_fee_price"]) == 3.5
    assert float(ext_data["unit_price"]) == 65000
