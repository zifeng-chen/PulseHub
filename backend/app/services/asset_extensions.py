"""资产类型扩展服务（15 §3~§7：不动产、车位、车辆、车险、贵金属、水电煤户号）。

规则：
- 扩展字段写在独立扩展表，不塞进主表 JSON（18 §6）；
- 车位最多关联一套不动产，解除关联保留审计历史（15 §4.2）；
- 水电煤户号必须关联具体不动产，不能只挂在家庭或账户上（15 §3.4）；
- 贵金属参考价值 = 重量 × 纯度 × 参考单价，仅作建议值，必须标明来源与日期（15 §6.2）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.logging import mask_value
from app.core.money import fmt_amount
from app.db.models.assets import (
    ParkingSpace,
    PreciousMetal,
    RealEstate,
    UtilityAccount,
    Vehicle,
    VehicleInsurance,
)
from app.db.models.business import Account, Asset
from app.services import audit as audit_service

logger = logging.getLogger(__name__)

EXT_MODELS = {
    "real_estate": RealEstate,
    "parking": ParkingSpace,
    "vehicle": Vehicle,
    "precious_metal": PreciousMetal,
}

REAL_ESTATE_FIELDS = (
    "property_type", "title_no", "ownership_no", "address", "building", "unit", "floor",
    "room_no", "building_area", "inner_area", "land_area", "orientation", "layout",
    "built_year", "delivery_date", "renovation_cost", "tax_cost", "agency_fee",
    "management_fee_price", "unit_price",
    "ownership_ratio", "has_mortgage", "note",
)
PARKING_FIELDS = (
    "parking_type", "certificate_no", "parking_no", "area_name", "area",
    "management_fee", "management_fee_cycle", "linked_real_estate_id", "note",
)
VEHICLE_FIELDS = (
    "brand", "series", "model", "vin", "plate_no", "engine_no", "vehicle_type",
    "energy_type", "register_date", "mileage", "color", "usage_status",
    "parking_space_id", "has_loan", "note",
)
METAL_FIELDS = (
    "brand", "brand_code", "certificate_no", "purchase_channel",
    "pricing_mode", "flat_price",
    "purity", "weight", "unit", "unit_price", "processing_fee", "tax_cost",
    "storage_location", "note",
)

EXT_FIELDS = {
    "real_estate": REAL_ESTATE_FIELDS,
    "parking": PARKING_FIELDS,
    "vehicle": VEHICLE_FIELDS,
    "precious_metal": METAL_FIELDS,
}

_MONEY_FIELDS = {
    "renovation_cost", "tax_cost", "agency_fee",  # 不动产
    "area", "management_fee",                     # 车位
    "mileage",                                    # 车辆
    "purity", "weight", "unit_price", "processing_fee", "flat_price",  # 贵金属
    "management_fee_price", "unit_price",                                       # 不动产
}
_AREA_FIELDS = {"building_area", "inner_area", "land_area"}
_DATE_FIELDS = {"delivery_date", "register_date", "valuation_date"}
_INT_FIELDS = {"built_year"}
_BOOL_FIELDS = {"has_mortgage", "has_loan"}

_ENUM_BY_FIELD = {
    "property_type": enum_zh.PROPERTY_TYPE,
    "parking_type": enum_zh.PARKING_TYPE,
    "energy_type": enum_zh.VEHICLE_ENERGY_TYPE,
    "usage_status": enum_zh.VEHICLE_USAGE_STATUS,
    "category": enum_zh.METAL_CATEGORY,
    "form": enum_zh.METAL_FORM,
    "unit": enum_zh.METAL_UNIT,
    "valuation_source": enum_zh.VALUATION_SOURCE,
    "billing_cycle": enum_zh.BILLING_CYCLE,
    "insurance_type": enum_zh.INSURANCE_TYPE,
    "renewal_status": enum_zh.INSURANCE_RENEWAL_STATUS,
    "utility_type": enum_zh.UTILITY_TYPE,
}

# 同名不同义的枚举：住宅的 unit 是「单元」，贵金属的 unit 才是计量单位。
# 这里显式声明，查询时先看类型专属映射，再回落到通用映射。
_TYPE_SCOPED_ENUMS: dict[tuple[str, str], dict] = {
    ("real_estate", "unit"): {},
    # 贵金属计价方式：克重计价 / 一口价
    ("precious_metal", "pricing_mode"): {"by_weight": "克重计价", "flat": "一口价"},
}


def _enum_for(asset_type: str | None, field: str) -> dict | None:
    if asset_type is not None and (asset_type, field) in _TYPE_SCOPED_ENUMS:
        mapping = _TYPE_SCOPED_ENUMS[(asset_type, field)]
        return mapping or None
    return _ENUM_BY_FIELD.get(field)


_ENUM_LABEL_KEY = {
    "property_type": "property_type",
    "parking_type": "parking_type",
    "energy_type": "vehicle_energy_type",
    "usage_status": "vehicle_usage_status",
    "category": "metal_category",
    "form": "metal_form",
    "unit": "metal_unit",
    "valuation_source": "valuation_source",
    "billing_cycle": "billing_cycle",
    "insurance_type": "insurance_type",
    "renewal_status": "insurance_renewal_status",
    "utility_type": "utility_type",
}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _parse_dt(value, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return to_local_naive(value)
    try:
        return to_local_naive(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        raise AppError(ErrorCode.VALIDATION_ERROR, "日期格式不正确",
                       field_errors={field: "日期格式应为 ISO 8601"})


def _coerce_row_data(
    model_fields: tuple[str, ...], payload: dict, *, asset_type: str | None = None,
) -> dict:
    """把扩展 payload 转成模型字段（含数值/日期/枚举/布尔校验）。"""
    field_errors: dict[str, str] = {}
    data: dict = {}
    # 白名单之外的键会被丢弃：必须留痕，否则「填了但没保存」只能靠用户发现
    dropped = sorted(k for k in payload if k not in model_fields)
    if dropped:
        logger.warning(
            "扩展字段不在白名单，已丢弃：%s（payload 里的多余键通常是前端字段与后端不同步）",
            ", ".join(dropped),
        )
    for field, raw in payload.items():
        if field not in model_fields:
            continue
        if field in _BOOL_FIELDS:
            data[field] = bool(raw)
            continue
        if raw in (None, ""):
            data[field] = None
            continue
        enum_values = _enum_for(asset_type, field)
        if enum_values is not None:
            if raw not in enum_values:
                field_errors[field] = "取值不合法"
            else:
                data[field] = raw
            continue
        if field in _MONEY_FIELDS or field in _AREA_FIELDS:
            try:
                value = Decimal(str(raw))
                if not value.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors[field] = "数值格式不正确"
                continue
            if value < 0:
                field_errors[field] = "数值不能为负数"
                continue
            data[field] = value
            continue
        if field in _INT_FIELDS:
            try:
                data[field] = int(raw)
            except (TypeError, ValueError):
                field_errors[field] = "必须是整数"
            continue
        if field in _DATE_FIELDS:
            data[field] = _parse_dt(raw, field)
            continue
        data[field] = raw

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "扩展字段校验失败",
                       field_errors={f"extension.{k}": v for k, v in field_errors.items()})
    return data


async def _link_real_estate(db: AsyncSession, household_id: str, real_estate_asset_id: str) -> Asset:
    asset = (await db.execute(select(Asset).where(Asset.id == real_estate_asset_id))).scalars().first()
    if asset is None or asset.deleted_at is not None or asset.household_id != household_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "关联的不动产不存在",
                       field_errors={"extension.linked_real_estate_id": "关联的不动产不存在"})
    if asset.asset_type != "real_estate":
        raise AppError(ErrorCode.VALIDATION_ERROR, "只能关联不动产类型的资产",
                       field_errors={"extension.linked_real_estate_id": "该资产不是不动产"})
    return asset


async def _link_parking(db: AsyncSession, household_id: str, parking_asset_id: str) -> Asset:
    asset = (await db.execute(select(Asset).where(Asset.id == parking_asset_id))).scalars().first()
    if asset is None or asset.deleted_at is not None or asset.household_id != household_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "关联的车位不存在",
                       field_errors={"extension.parking_space_id": "关联的车位不存在"})
    if asset.asset_type != "parking":
        raise AppError(ErrorCode.VALIDATION_ERROR, "只能关联车位类型的资产",
                       field_errors={"extension.parking_space_id": "该资产不是车位"})
    return asset


async def create_extension(db: AsyncSession, asset: Asset, payload: dict) -> None:
    """按资产类型创建扩展行（其他资产不建扩展行，由通用字段 + 自定义属性覆盖）。"""
    model = EXT_MODELS.get(asset.asset_type)
    if model is None:
        return
    data = _coerce_row_data(
        EXT_FIELDS[asset.asset_type], payload or {}, asset_type=asset.asset_type)

    if asset.asset_type == "real_estate":
        data.setdefault("property_type", "residence")
    if asset.asset_type == "parking":
        data.setdefault("parking_type", "ownership")
    if asset.asset_type == "precious_metal":
        data.setdefault("category", "gold")
        data.setdefault("unit", "g")

    if data.get("linked_real_estate_id"):
        await _link_real_estate(db, asset.household_id, data["linked_real_estate_id"])
    if data.get("parking_space_id"):
        await _link_parking(db, asset.household_id, data["parking_space_id"])

    row = model(id=new_id(), household_id=asset.household_id, asset_id=asset.id, **data)
    db.add(row)
    await db.flush()


async def update_extension(db: AsyncSession, asset: Asset, payload: dict) -> None:
    model = EXT_MODELS.get(asset.asset_type)
    if model is None:
        raise AppError(ErrorCode.STATE_INVALID, "其他资产不使用类型扩展表",
                       field_errors={"extension": "该资产类型没有扩展字段"})
    row = (await db.execute(select(model).where(model.asset_id == asset.id))).scalars().first()
    if row is None:
        await create_extension(db, asset, payload)
        return

    data = _coerce_row_data(
        EXT_FIELDS[asset.asset_type], payload or {}, asset_type=asset.asset_type)
    if data.get("linked_real_estate_id"):
        await _link_real_estate(db, asset.household_id, data["linked_real_estate_id"])
    if data.get("parking_space_id"):
        await _link_parking(db, asset.household_id, data["parking_space_id"])

    before = {f: getattr(row, f) for f in data.keys()}
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_by = asset.updated_by

    if before != {f: getattr(row, f) for f in data.keys()}:
        await audit_service.record_audit(
            db,
            action="asset.extension_update",
            action_label="编辑资产专项资料",
            object_type="asset",
            object_id=asset.id,
            before=before,
            after={f: getattr(row, f) for f in data.keys()},
            reason="扩展字段变更",
        )
    await db.flush()


def _base_labels(data: dict) -> dict:
    for field, namespace in _ENUM_LABEL_KEY.items():
        if field in data:
            data[f"{field}_label"] = enum_zh.zh(namespace, data.get(field))
    return data


def serialize_real_estate(row: RealEstate) -> dict:
    return _base_labels({
        "property_type": row.property_type,
        "title_no": row.title_no,
        "ownership_no": row.ownership_no,
        "address": row.address,
        "building": row.building,
        "unit": row.unit,
        "floor": row.floor,
        "room_no": row.room_no,
        "building_area": f"{_dec(row.building_area):.4f}" if row.building_area is not None else None,
        "inner_area": f"{_dec(row.inner_area):.4f}" if row.inner_area is not None else None,
        "land_area": f"{_dec(row.land_area):.4f}" if row.land_area is not None else None,
        "orientation": row.orientation,
        "layout": row.layout,
        "built_year": row.built_year,
        "delivery_date": local_iso(row.delivery_date) if row.delivery_date else None,
        "renovation_cost": fmt_amount(row.renovation_cost) if row.renovation_cost is not None else None,
        "tax_cost": fmt_amount(row.tax_cost) if row.tax_cost is not None else None,
        "agency_fee": fmt_amount(row.agency_fee) if row.agency_fee is not None else None,
        "management_fee_price": (
            f"{_dec(row.management_fee_price):.4f}" if row.management_fee_price is not None else None
        ),
        "unit_price": f"{_dec(row.unit_price):.4f}" if row.unit_price is not None else None,
        "ownership_ratio": f"{_dec(row.ownership_ratio):.4f}" if row.ownership_ratio is not None else None,
        "has_mortgage": row.has_mortgage,
        "has_lease": row.has_lease,
        "current_lease_id": row.current_lease_id,
        "vacancy_start_date": local_iso(row.vacancy_start_date) if row.vacancy_start_date else None,
        "note": row.note,
    })


def serialize_parking(row: ParkingSpace) -> dict:
    return _base_labels({
        "parking_type": row.parking_type,
        "certificate_no": row.certificate_no,
        "parking_no": row.parking_no,
        "area_name": row.area_name,
        "area": f"{_dec(row.area):.4f}" if row.area is not None else None,
        "management_fee": fmt_amount(row.management_fee) if row.management_fee is not None else None,
        "management_fee_cycle": row.management_fee_cycle,
        "management_fee_cycle_label": enum_zh.zh("billing_cycle", row.management_fee_cycle) if row.management_fee_cycle else "",
        "linked_real_estate_id": row.linked_real_estate_id,
        "note": row.note,
    })


def serialize_vehicle(row: Vehicle) -> dict:
    return _base_labels({
        "brand": row.brand,
        "series": row.series,
        "model": row.model,
        "vin": row.vin,
        "plate_no": row.plate_no,
        "engine_no": row.engine_no,
        "vehicle_type": row.vehicle_type,
        "energy_type": row.energy_type,
        "register_date": local_iso(row.register_date) if row.register_date else None,
        "mileage": f"{_dec(row.mileage):.1f}" if row.mileage is not None else None,
        "color": row.color,
        "usage_status": row.usage_status,
        "parking_space_id": row.parking_space_id,
        "has_loan": row.has_loan,
        "note": row.note,
    })


def serialize_metal(row: PreciousMetal) -> dict:
    data = _base_labels({
        # 历史字段：品类 / 形式已由「分类 + 计价方式」取代，仅回显旧数据，不再写入
        "category": row.category,
        "form": row.form,
        "brand": row.brand,
        "brand_code": row.brand_code,
        "certificate_no": row.certificate_no,
        "purchase_channel": row.purchase_channel,
        "pricing_mode": row.pricing_mode or "by_weight",
        "pricing_mode_label": {"by_weight": "克重计价", "flat": "一口价"}.get(
            row.pricing_mode or "by_weight", "克重计价"),
        "flat_price": fmt_amount(row.flat_price) if row.flat_price is not None else None,
        "purity": f"{_dec(row.purity):.4f}" if row.purity is not None else None,
        "weight": f"{_dec(row.weight):.4f}" if row.weight is not None else None,
        "unit": row.unit,
        "unit_price": fmt_amount(row.unit_price) if row.unit_price is not None else None,
        "processing_fee": fmt_amount(row.processing_fee) if row.processing_fee is not None else None,
        "tax_cost": fmt_amount(row.tax_cost) if row.tax_cost is not None else None,
        "storage_location": row.storage_location,
        "note": row.note,
    })
    return data


_SERIALIZERS = {
    "real_estate": (RealEstate, serialize_real_estate),
    "parking": (ParkingSpace, serialize_parking),
    "vehicle": (Vehicle, serialize_vehicle),
    "precious_metal": (PreciousMetal, serialize_metal),
}


async def extension_for(db: AsyncSession, asset: Asset) -> dict | None:
    entry = _SERIALIZERS.get(asset.asset_type)
    if entry is None:
        return None
    model, serializer = entry
    row = (await db.execute(select(model).where(model.asset_id == asset.id))).scalars().first()
    if row is None:
        return None
    data = serializer(row)
    if asset.asset_type == "parking" and row.linked_real_estate_id:
        linked = (
            await db.execute(select(Asset).where(Asset.id == row.linked_real_estate_id))
        ).scalars().first()
        if linked is not None:
            data["linked_real_estate_name"] = linked.name
    if asset.asset_type == "vehicle" and row.parking_space_id:
        linked = (
            await db.execute(select(Asset).where(Asset.id == row.parking_space_id))
        ).scalars().first()
        if linked is not None:
            data["parking_space_name"] = linked.name
    return data


# ---------------- 水电煤户号（15 §3.4） ----------------

UTILITY_FIELDS = (
    "utility_type", "provider", "account_no", "account_name", "billing_address",
    "billing_cycle", "billing_day", "default_account_id", "enabled",
    "last_bill_date", "last_bill_amount", "note",
)


def format_account_no(value: str | None) -> str | None:
    """户号展示格式：去掉空格后每 4 位分组（如 1234 5678 9012），便于口头报号与核对。

    户号不属于敏感信息，明文展示；存储仍是连续字符串，格式化只发生在输出层。
    """
    if not value:
        return None
    compact = re.sub(r"\s+", "", str(value))
    if not compact:
        return None
    if len(compact) <= 4:
        return compact
    return " ".join(compact[i:i + 4] for i in range(0, len(compact), 4))


def serialize_utility(row: UtilityAccount, reveal: bool = False) -> dict:
    return {
        "id": row.id,
        "real_estate_id": row.real_estate_id,
        "utility_type": row.utility_type,
        "utility_type_label": enum_zh.zh("utility_type", row.utility_type),
        "provider": row.provider,
        # 户号不是敏感数据：明文返回，并按每 4 位分组便于阅读
        "account_no": format_account_no(row.account_no),
        "account_name": row.account_name,
        "billing_address": row.billing_address,
        "billing_cycle": row.billing_cycle,
        "billing_cycle_label": enum_zh.zh("billing_cycle", row.billing_cycle) if row.billing_cycle else "",
        "billing_day": row.billing_day,
        "default_account_id": row.default_account_id,
        "enabled": row.enabled,
        "last_bill_date": local_iso(row.last_bill_date) if row.last_bill_date else None,
        "last_bill_amount": (
            fmt_amount(row.last_bill_amount) if row.last_bill_amount is not None else None
        ),
        "note": row.note,
        "created_at": utc_iso(row.created_at),
    }


async def _load_real_estate(db: AsyncSession, user: CurrentUser, asset_id: str) -> Asset:
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalars().first()
    if asset is None or asset.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "不动产不存在", status_code=404)
    if asset.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    if asset.asset_type != "real_estate":
        raise AppError(ErrorCode.VALIDATION_ERROR, "该资产不是不动产",
                       field_errors={"asset_id": "只有不动产可以维护水电煤户号"})
    return asset


def _validate_utility_payload(payload: dict, *, partial: bool) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}

    if "utility_type" in payload or not partial:
        utility_type = payload.get("utility_type")
        if utility_type not in enum_zh.UTILITY_TYPE:
            field_errors["utility_type"] = "请选择公用事业类型"
        else:
            data["utility_type"] = utility_type

    if "billing_cycle" in payload and payload["billing_cycle"] not in (None, ""):
        if payload["billing_cycle"] not in enum_zh.BILLING_CYCLE:
            field_errors["billing_cycle"] = "缴费周期不合法"
        else:
            data["billing_cycle"] = payload["billing_cycle"]

    if "billing_day" in payload and payload["billing_day"] not in (None, ""):
        try:
            day = int(payload["billing_day"])
        except (TypeError, ValueError):
            field_errors["billing_day"] = "缴费日必须是 1-31 的整数"
        else:
            if not 1 <= day <= 31:
                field_errors["billing_day"] = "缴费日必须是 1-31 的整数"
            else:
                data["billing_day"] = day

    if "last_bill_amount" in payload and payload["last_bill_amount"] not in (None, ""):
        try:
            amount = Decimal(str(payload["last_bill_amount"]))
            if not amount.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors["last_bill_amount"] = "金额格式不正确"
        else:
            if amount < 0:
                field_errors["last_bill_amount"] = "金额不能为负数"
            else:
                data["last_bill_amount"] = amount

    for date_field in ("last_bill_date",):
        if date_field in payload:
            data[date_field] = _parse_dt(payload.get(date_field), date_field)

    if "enabled" in payload:
        data["enabled"] = bool(payload["enabled"])

    for plain in ("provider", "account_name", "billing_address",
                  "default_account_id", "note"):
        if plain in payload:
            data[plain] = payload[plain]

    # 户号：存连续字符串（展示时再分组），限制在列宽内
    if "account_no" in payload:
        raw = payload.get("account_no")
        compact = re.sub(r"\s+", "", str(raw)).strip() if raw not in (None, "") else ""
        if len(compact) > 64:
            field_errors["account_no"] = "户号过长（最多 64 位）"
        else:
            data["account_no"] = compact or None

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "水电煤户号校验失败", field_errors=field_errors)
    return data


async def list_utility_accounts(
    db: AsyncSession, user: CurrentUser, asset_id: str
) -> list[UtilityAccount]:
    await _load_real_estate(db, user, asset_id)
    return list(
        (await db.execute(
            select(UtilityAccount)
            .where(UtilityAccount.real_estate_id == asset_id, UtilityAccount.deleted_at.is_(None))
            .order_by(UtilityAccount.created_at.asc())
        )).scalars().all()
    )


async def create_utility_account(
    db: AsyncSession, user: CurrentUser, asset_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> UtilityAccount:
    asset = await _load_real_estate(db, user, asset_id)
    data = _validate_utility_payload(payload, partial=False)
    await _check_default_account(db, user, data.get("default_account_id"))

    row = UtilityAccount(
        id=new_id(),
        household_id=asset.household_id,
        real_estate_id=asset.id,
        utility_type=data["utility_type"],
        provider=data.get("provider"),
        account_no=data.get("account_no"),
        account_name=data.get("account_name"),
        billing_address=data.get("billing_address"),
        billing_cycle=data.get("billing_cycle"),
        billing_day=data.get("billing_day"),
        default_account_id=data.get("default_account_id"),
        enabled=data.get("enabled", True),
        last_bill_date=data.get("last_bill_date"),
        last_bill_amount=data.get("last_bill_amount"),
        note=data.get("note"),
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(row)
    await db.flush()
    await audit_service.record_audit(
        db,
        action="utility.create",
        action_label="新增水电煤户号",
        object_type="utility_account",
        object_id=row.id,
        related_object_id=asset.id,
        after={"utility_type": row.utility_type, "provider": row.provider,
               "real_estate_id": asset.id},
        user=user,
        ctx=ctx,
    )
    return row


async def _get_utility(db: AsyncSession, user: CurrentUser, utility_id: str) -> UtilityAccount:
    row = (await db.execute(select(UtilityAccount).where(UtilityAccount.id == utility_id))).scalars().first()
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "水电煤户号不存在", status_code=404)
    if row.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return row


async def _check_default_account(db: AsyncSession, user: CurrentUser, account_id: str | None) -> None:
    if not account_id:
        return
    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
    if account is None or account.deleted_at is not None or account.household_id != user.household_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "默认缴费账户不存在",
                       field_errors={"default_account_id": "账户不存在"})


async def update_utility_account(
    db: AsyncSession, user: CurrentUser, utility_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> UtilityAccount:
    row = await _get_utility(db, user, utility_id)
    data = _validate_utility_payload(payload, partial=True)
    await _check_default_account(db, user, data.get("default_account_id"))

    before = {f: getattr(row, f) for f in data}
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="utility.update",
        action_label="编辑水电煤户号",
        object_type="utility_account",
        object_id=row.id,
        related_object_id=row.real_estate_id,
        before=before,
        after={f: getattr(row, f) for f in data},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return row


async def delete_utility_account(
    db: AsyncSession, user: CurrentUser, utility_id: str, reason: str,
    ctx: RequestContext | None = None,
) -> UtilityAccount:
    """删除户号为软删除（保留历史），需填写原因。"""
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除户号必须填写原因",
                       field_errors={"reason": "请填写删除原因"})
    row = await _get_utility(db, user, utility_id)
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="utility.delete",
        action_label="删除水电煤户号",
        object_type="utility_account",
        object_id=row.id,
        related_object_id=row.real_estate_id,
        before={"utility_type": row.utility_type, "provider": row.provider},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return row


# ---------------- 车辆保险（15 §5.3） ----------------

INSURANCE_FIELDS = (
    "insurance_type", "insurer", "policy_no", "insured_at", "effective_date",
    "expiry_date", "premium", "coverage", "deductible", "insured_person",
    "beneficiary", "renewal_status", "attachment_id", "remind_date", "note",
)


def serialize_insurance(row: VehicleInsurance, today: datetime | None = None) -> dict:
    now = today or local_now()
    days_left = None
    if row.expiry_date is not None:
        expiry = row.expiry_date if row.expiry_date.tzinfo else row.expiry_date.replace(tzinfo=timezone.utc)
        days_left = (expiry.date() - now.date()).days
    return {
        "id": row.id,
        "vehicle_id": row.vehicle_id,
        "insurance_type": row.insurance_type,
        "insurance_type_label": enum_zh.zh("insurance_type", row.insurance_type),
        "insurer": row.insurer,
        "policy_no": row.policy_no,
        "insured_at": local_iso(row.insured_at) if row.insured_at else None,
        "effective_date": local_iso(row.effective_date) if row.effective_date else None,
        "expiry_date": local_iso(row.expiry_date) if row.expiry_date else None,
        "premium": fmt_amount(row.premium) if row.premium is not None else None,
        "coverage": fmt_amount(row.coverage) if row.coverage is not None else None,
        "deductible": fmt_amount(row.deductible) if row.deductible is not None else None,
        "insured_person": row.insured_person,
        "beneficiary": row.beneficiary,
        "renewal_status": row.renewal_status,
        "renewal_status_label": enum_zh.zh("insurance_renewal_status", row.renewal_status),
        "attachment_id": row.attachment_id,
        "remind_date": local_iso(row.remind_date) if row.remind_date else None,
        "days_to_expiry": days_left,
        "expiring_soon": days_left is not None and 0 <= days_left <= 30,
        "expired": days_left is not None and days_left < 0,
        "note": row.note,
        "created_at": utc_iso(row.created_at),
    }


async def _load_vehicle(db: AsyncSession, user: CurrentUser, asset_id: str) -> Asset:
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalars().first()
    if asset is None or asset.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "车辆不存在", status_code=404)
    if asset.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    if asset.asset_type != "vehicle":
        raise AppError(ErrorCode.VALIDATION_ERROR, "该资产不是车辆",
                       field_errors={"asset_id": "只有车辆可以维护保险"})
    return asset


def _validate_insurance_payload(payload: dict, *, partial: bool) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}

    if "insurance_type" in payload or not partial:
        insurance_type = payload.get("insurance_type")
        if insurance_type not in enum_zh.INSURANCE_TYPE:
            field_errors["insurance_type"] = "请选择保险类型"
        else:
            data["insurance_type"] = insurance_type

    if "renewal_status" in payload and payload["renewal_status"] not in (None, ""):
        if payload["renewal_status"] not in enum_zh.INSURANCE_RENEWAL_STATUS:
            field_errors["renewal_status"] = "续保状态不合法"
        else:
            data["renewal_status"] = payload["renewal_status"]

    for money_field in ("premium", "coverage", "deductible"):
        if money_field in payload and payload[money_field] not in (None, ""):
            try:
                value = Decimal(str(payload[money_field]))
                if not value.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors[money_field] = "金额格式不正确"
            else:
                if value < 0:
                    field_errors[money_field] = "金额不能为负数"
                else:
                    data[money_field] = value

    for date_field in ("insured_at", "effective_date", "expiry_date", "remind_date"):
        if date_field in payload:
            data[date_field] = _parse_dt(payload.get(date_field), date_field)

    for plain in ("insurer", "policy_no", "insured_person", "beneficiary",
                  "attachment_id", "note"):
        if plain in payload:
            data[plain] = payload[plain]

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "保险信息校验失败", field_errors=field_errors)
    return data


async def list_insurances(db: AsyncSession, user: CurrentUser, asset_id: str) -> list[VehicleInsurance]:
    await _load_vehicle(db, user, asset_id)
    return list(
        (await db.execute(
            select(VehicleInsurance)
            .where(VehicleInsurance.vehicle_id == asset_id, VehicleInsurance.deleted_at.is_(None))
            .order_by(VehicleInsurance.expiry_date.desc(), VehicleInsurance.created_at.desc())
        )).scalars().all()
    )


async def create_insurance(
    db: AsyncSession, user: CurrentUser, asset_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> VehicleInsurance:
    asset = await _load_vehicle(db, user, asset_id)
    data = _validate_insurance_payload(payload, partial=False)

    row = VehicleInsurance(
        id=new_id(),
        household_id=asset.household_id,
        vehicle_id=asset.id,
        insurance_type=data["insurance_type"],
        insurer=data.get("insurer"),
        policy_no=data.get("policy_no"),
        insured_at=data.get("insured_at"),
        effective_date=data.get("effective_date"),
        expiry_date=data.get("expiry_date"),
        premium=data.get("premium"),
        coverage=data.get("coverage"),
        deductible=data.get("deductible"),
        insured_person=data.get("insured_person"),
        beneficiary=data.get("beneficiary"),
        renewal_status=data.get("renewal_status", "pending"),
        attachment_id=data.get("attachment_id"),
        remind_date=data.get("remind_date"),
        note=data.get("note"),
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(row)
    await db.flush()
    await audit_service.record_audit(
        db,
        action="insurance.create",
        action_label="新增车辆保险",
        object_type="vehicle_insurance",
        object_id=row.id,
        related_object_id=asset.id,
        after={"insurance_type": row.insurance_type, "insurer": row.insurer,
               "expiry_date": row.expiry_date},
        user=user,
        ctx=ctx,
    )
    return row


async def _get_insurance(db: AsyncSession, user: CurrentUser, insurance_id: str) -> VehicleInsurance:
    row = (await db.execute(
        select(VehicleInsurance).where(VehicleInsurance.id == insurance_id)
    )).scalars().first()
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "保险记录不存在", status_code=404)
    if row.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return row


async def update_insurance(
    db: AsyncSession, user: CurrentUser, insurance_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> VehicleInsurance:
    row = await _get_insurance(db, user, insurance_id)
    data = _validate_insurance_payload(payload, partial=True)
    before = {f: getattr(row, f) for f in data}
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="insurance.update",
        action_label="编辑车辆保险",
        object_type="vehicle_insurance",
        object_id=row.id,
        related_object_id=row.vehicle_id,
        before=before,
        after={f: getattr(row, f) for f in data},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return row


async def delete_insurance(
    db: AsyncSession, user: CurrentUser, insurance_id: str, reason: str,
    ctx: RequestContext | None = None,
) -> VehicleInsurance:
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除保险记录必须填写原因",
                       field_errors={"reason": "请填写删除原因"})
    row = await _get_insurance(db, user, insurance_id)
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="insurance.delete",
        action_label="删除车辆保险",
        object_type="vehicle_insurance",
        object_id=row.id,
        related_object_id=row.vehicle_id,
        before={"insurance_type": row.insurance_type, "insurer": row.insurer},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return row


async def extensions_for(db: AsyncSession, assets: list[Asset]) -> dict[str, dict]:
    """批量取扩展（列表页使用，避免逐条查询）。"""
    grouped: dict[str, dict] = {}
    linked_ids: set[str] = set()
    for asset_type, (model, serializer) in _SERIALIZERS.items():
        ids = [a.id for a in assets if a.asset_type == asset_type]
        if not ids:
            continue
        rows = list(
            (await db.execute(select(model).where(model.asset_id.in_(ids)))).scalars().all()
        )
        for row in rows:
            data = serializer(row)
            grouped[row.asset_id] = data
            if asset_type == "parking" and row.linked_real_estate_id:
                linked_ids.add(row.linked_real_estate_id)
            if asset_type == "vehicle" and row.parking_space_id:
                linked_ids.add(row.parking_space_id)

    if linked_ids:
        linked = {
            a.id: a.name
            for a in (await db.execute(select(Asset).where(Asset.id.in_(linked_ids)))).scalars().all()
        }
        for data in grouped.values():
            if data.get("linked_real_estate_id") in linked:
                data["linked_real_estate_name"] = linked[data["linked_real_estate_id"]]
            if data.get("parking_space_id") in linked:
                data["parking_space_name"] = linked[data["parking_space_id"]]
    return grouped


def custom_attributes_from(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
