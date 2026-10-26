"""资产领域服务（06 §2、15、18 §4 M2 资产与专项资产）。

口径：
- 资产当前价值一律以 `assets.current_value` 为准，估值历史存于 `asset_valuations`；
- 净资产统计只纳入「计入净资产 且 未出售/未报废/未归档」的资产当前价值（01 §4）；
- 处置必须填写处置日期、金额、去向账户与原因，并生成已确认的 `asset_dispose` 事件；
- 归档只改变状态，保留记录与关联事件，不删除任何历史；
- 资产类型决定必填字段；已有业务数据（估值/事件）的资产不允许直接切换类型，必须走更正流程（15 §9）；
- 证件号、VIN、车牌、户号等敏感字段默认掩码，只有具备审计查看权限的用户可以查看完整值。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.money import fmt_amount
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.logging import mask_value
from app.db.models.assets import AssetValuation
from app.db.models.business import Asset, BusinessEvent, EventMovement
from app.services import audit as audit_service

# 流动性归类（06 §2 顶部统计）：由资产类型推导，不新增字段
LIQUID_ASSET_TYPES = {"precious_metal"}

# 不计入净资产统计的资产状态
INACTIVE_ASSET_STATUSES = ("disposed", "scrapped", "archived")

# 敏感字段（默认掩码，按权限查看完整值）
SENSITIVE_FIELDS = {
    "title_no", "ownership_no", "certificate_no", "vin", "plate_no", "engine_no",
}

GENERIC_EDITABLE = (
    "name", "sub_type", "asset_type", "category_id", "owner_member_id", "currency",
    "acquire_date", "acquire_cost", "current_value", "value_source", "last_valuation_date",
    "status", "include_in_net_worth", "linked_account_id", "linked_liability_id",
    "depreciation_method", "maintenance_date", "note",
)

# 类型必填字段（06 §2「资产类型决定必填字段」）
# 不动产的 property_type 不再让用户选：由「住宅 / 车位」分类推导，缺省按住宅补全
REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "real_estate": (),
    "parking": ("parking_type", "parking_no"),
    "vehicle": ("brand",),
    "precious_metal": ("weight", "unit"),  # 品类已由资产分类表达，不再重复要求
    "other": (),
}

CALIBER = {
    "current_value": "资产当前价值以资产主表 current_value 为准，估值历史保留在估值时间线",
    "net_worth": "只纳入「计入净资产 且 未出售/未报废/未归档」的资产当前价值；多币种按币种分组不相加",
    "liquidity": "流动资产口径：贵金属；其余资产类型按非流动资产归类",
    "pending_valuation": "待估值 = 当前价值为空或从未登记估值的资产",
    "dispose": "处置必须填写处置日期、金额、去向账户与原因，并生成资产处置事件",
    "valuation_edit": "修改已确认的当前价值必须填写调整原因，并写入估值时间线",
}

_PENDING_DEBT_NOTE = "房贷/车贷的余额、利率与还款由负债模块统一管理，资产侧只保留标记与关联，不重复录入第二套贷款数据（15 §3.2、15 §5.2）"


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def money(value) -> str:
    """金额对外统一输出（默认两位小数）。"""
    return fmt_amount(_dec(value))


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


def liquidity_of(asset_type: str) -> str:
    return "current" if asset_type in LIQUID_ASSET_TYPES else "non_current"


def _mask_sensitive(data: dict, reveal: bool) -> dict:
    if reveal:
        return data
    for field in SENSITIVE_FIELDS:
        if field in data and data[field]:
            data[field] = mask_value(data[field])
    return data


def serialize_asset(
    asset: Asset,
    *,
    extension: dict | None = None,
    reveal: bool = False,
    extra: dict | None = None,
    category_label: str | None = None,
) -> dict:
    data = {
        "id": asset.id,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "asset_type_label": enum_zh.zh("asset_type", asset.asset_type),
        "sub_type": asset.sub_type,
        "category_id": asset.category_id,
        "category_label": category_label or "",
        "owner_member_id": asset.owner_member_id,
        "currency": asset.currency,
        "acquire_date": local_iso(asset.acquire_date) if asset.acquire_date else None,
        "acquire_cost": money(asset.acquire_cost),
        "current_value": money(asset.current_value),
        "value_source": asset.value_source,
        "value_source_label": enum_zh.zh("valuation_source", asset.value_source) if asset.value_source else "",
        "last_valuation_date": local_iso(asset.last_valuation_date) if asset.last_valuation_date else None,
        "status": asset.status,
        "status_label": enum_zh.zh("asset_status", asset.status),
        "include_in_net_worth": asset.include_in_net_worth,
        "linked_account_id": asset.linked_account_id,
        "linked_liability_id": asset.linked_liability_id,
        "depreciation_method": asset.depreciation_method,
        "depreciation_method_label": (
            enum_zh.zh("depreciation_method", asset.depreciation_method)
            if asset.depreciation_method else ""
        ),
        "maintenance_date": local_iso(asset.maintenance_date) if asset.maintenance_date else None,
        "liquidity": liquidity_of(asset.asset_type),
        "liquidity_label": enum_zh.zh("asset_liquidity", liquidity_of(asset.asset_type)),
        "counts_in_net_worth": bool(
            asset.include_in_net_worth and asset.status not in INACTIVE_ASSET_STATUSES
        ),
        "custom_attributes": json.loads(asset.custom_attributes) if asset.custom_attributes else None,
        "note": asset.note,
        "version": asset.version,
        "created_at": utc_iso(asset.created_at),
        "updated_at": utc_iso(asset.updated_at),
        "pending_dependency_note": _PENDING_DEBT_NOTE,
    }
    if extension:
        data["extension"] = _mask_sensitive(extension, reveal)
    if extra:
        data.update(extra)
    return data


async def category_label_map(db: AsyncSession, household_id: str | None) -> dict[str, str]:
    """分类 id -> 中文名映射（08 §3 六个分类域），供资产序列化回填分类标签。"""
    from app.services import catalog as catalog_service

    return await catalog_service.category_label_map(db, household_id)


async def get_asset(db: AsyncSession, user: CurrentUser, asset_id: str) -> Asset:
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalars().first()
    if asset is None or asset.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "资产不存在", status_code=404)
    if asset.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return asset


async def list_assets(
    db: AsyncSession,
    user: CurrentUser,
    *,
    keyword: str | None = None,
    asset_type: str | None = None,
    category_id: str | None = None,
    member_id: str | None = None,
    status: str | None = None,
    liquidity: str | None = None,
    valuation_from: datetime | None = None,
    valuation_to: datetime | None = None,
    pending_valuation: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Asset], int]:
    conditions = [Asset.household_id == user.household_id, Asset.deleted_at.is_(None)]
    if keyword:
        conditions.append(Asset.name.ilike(f"%{keyword}%"))
    if asset_type:
        conditions.append(Asset.asset_type == asset_type)
    if category_id:
        conditions.append(Asset.category_id == category_id)
    if member_id:
        conditions.append(Asset.owner_member_id == member_id)
    if status:
        conditions.append(Asset.status == status)
    if liquidity == "current":
        conditions.append(Asset.asset_type.in_(sorted(LIQUID_ASSET_TYPES)))
    elif liquidity == "non_current":
        conditions.append(Asset.asset_type.not_in(sorted(LIQUID_ASSET_TYPES)))
    if valuation_from is not None:
        conditions.append(Asset.last_valuation_date >= valuation_from)
    if valuation_to is not None:
        conditions.append(Asset.last_valuation_date < valuation_to)
    if pending_valuation:
        # 与顶部统计同口径：当前价值为空/0，或从未登记估值
        conditions.append(or_(
            Asset.current_value.is_(None),
            Asset.current_value == 0,
            Asset.last_valuation_date.is_(None),
        ))

    total = int(
        (await db.execute(select(func.count(Asset.id)).where(*conditions))).scalar_one()
    )
    rows = list(
        (await db.execute(
            select(Asset).where(*conditions)
            .order_by(Asset.created_at.asc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    return rows, total


async def asset_summary(db: AsyncSession, household_id: str, base_currency: str = "CNY") -> dict:
    """资产顶部统计（06 §2）：资产总值、流动资产、非流动资产、待估值资产。"""
    assets = list(
        (await db.execute(
            select(Asset).where(Asset.household_id == household_id, Asset.deleted_at.is_(None))
        )).scalars().all()
    )

    buckets: dict[str, dict] = {}
    pending_count = 0
    for asset in assets:
        is_pending = asset.current_value is None or _dec(asset.current_value) == 0 or asset.last_valuation_date is None
        if is_pending:
            pending_count += 1
        bucket = buckets.setdefault(asset.currency, {
            "currency": asset.currency, "total": Decimal("0"), "current": Decimal("0"),
            "non_current": Decimal("0"), "count": 0, "pending_valuation": 0,
            "in_net_worth": Decimal("0"),
        })
        bucket["count"] += 1
        if asset.include_in_net_worth and asset.status not in INACTIVE_ASSET_STATUSES:
            value = _dec(asset.current_value)
            bucket["in_net_worth"] += value
            if liquidity_of(asset.asset_type) == "current":
                bucket["current"] += value
            else:
                bucket["non_current"] += value
            bucket["total"] += value
        if is_pending:
            bucket["pending_valuation"] += 1

    items = [
        {
            "currency": b["currency"],
            "total": money(b["total"]),
            "current": money(b["current"]),
            "non_current": money(b["non_current"]),
            "in_net_worth": money(b["in_net_worth"]),
            "count": b["count"],
            "pending_valuation": b["pending_valuation"],
        }
        for b in buckets.values()
    ]
    primary = next((b for b in items if b["currency"] == base_currency), None)
    if primary is None:
        primary = {"currency": base_currency, "total": "0.0000", "current": "0.0000",
                   "non_current": "0.0000", "in_net_worth": "0.0000", "count": 0,
                   "pending_valuation": 0}
    return {
        "base_currency": base_currency,
        "count": len(assets),
        "total_value": primary["total"],
        "current_value": primary["current"],
        "non_current_value": primary["non_current"],
        "pending_valuation_count": primary["pending_valuation"],
        "by_currency": items,
        "multi_currency": len(items) > 1,
        "caliber": CALIBER,
    }


def _validate_generic(payload: dict, *, partial: bool) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}

    if "name" in payload or not partial:
        name = (payload.get("name") or "").strip()
        if not name:
            field_errors["name"] = "请填写资产名称"
        else:
            data["name"] = name

    if "asset_type" in payload or not partial:
        asset_type = payload.get("asset_type")
        if asset_type not in enum_zh.ASSET_TYPE:
            field_errors["asset_type"] = "资产类型不合法"
        else:
            data["asset_type"] = asset_type

    if "currency" in payload:
        currency = (payload.get("currency") or "CNY").upper()
        if not (2 <= len(currency) <= 8):
            field_errors["currency"] = "币种代码不合法"
        else:
            data["currency"] = currency

    for money_field in ("acquire_cost", "current_value"):
        if money_field in payload and payload[money_field] not in (None, ""):
            try:
                value = Decimal(str(payload[money_field]))
                if not value.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors[money_field] = "金额格式不正确"
            else:
                if value < 0:
                    field_errors[money_field] = "金额必须大于等于 0"
                else:
                    data[money_field] = value

    for date_field in ("acquire_date", "maintenance_date"):
        if date_field in payload:
            data[date_field] = _parse_dt(payload.get(date_field), date_field)

    for enum_field, enum_meta in (
        ("status", enum_zh.ASSET_STATUS),
        ("depreciation_method", enum_zh.DEPRECIATION_METHOD),
        ("value_source", enum_zh.VALUATION_SOURCE),
    ):
        if enum_field in payload and payload[enum_field] not in (None, ""):
            if payload[enum_field] not in enum_meta:
                field_errors[enum_field] = "取值不合法"
            else:
                data[enum_field] = payload[enum_field]

    for plain in ("sub_type", "category_id", "owner_member_id", "linked_account_id",
                  "linked_liability_id", "note"):
        if plain in payload:
            data[plain] = payload[plain] or None

    if "include_in_net_worth" in payload:
        data["include_in_net_worth"] = bool(payload["include_in_net_worth"])

    if "custom_attributes" in payload:
        value = payload["custom_attributes"]
        if value in (None, ""):
            data["custom_attributes"] = None
        elif isinstance(value, dict):
            data["custom_attributes"] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                field_errors["custom_attributes"] = "自定义属性必须是合法 JSON"
            else:
                data["custom_attributes"] = value
        else:
            field_errors["custom_attributes"] = "自定义属性必须是 JSON 对象"

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "资产信息校验失败", field_errors=field_errors)
    return data


async def _new_event_no() -> str:
    return f"EV{local_now().strftime('%Y%m%d')}{new_id()[:6].upper()}"


# 「资产」分类一级节点 → 资产类型（06 §2）。分类由用户在表单里显式选择，
# 因此以分类为准推导类型，避免前端未带 asset_type 时落到 other。
_CATEGORY_ROOT_TO_TYPE = {
    "不动产": "real_estate",
    "车位": "parking",
    "车辆": "vehicle",
    "贵金属": "precious_metal",
    "借出款": "other",
}


async def _derive_asset_type(db: AsyncSession, category_id: str | None) -> str | None:
    """按分类树上溯到一级分类并映射资产类型；无法判断时返回 None。"""
    if not category_id:
        return None
    from app.db.models.config import Category

    node = (await db.execute(select(Category).where(Category.id == category_id))).scalars().first()
    if node is None:
        return None
    for _ in range(4):  # 最多上溯 4 层，防御脏数据成环
        if node.parent_id is None:
            break
        parent = (
            await db.execute(select(Category).where(Category.id == node.parent_id))
        ).scalars().first()
        if parent is None:
            break
        node = parent
    return _CATEGORY_ROOT_TO_TYPE.get(node.name)


async def _metal_auto_values(ext: dict) -> tuple[Decimal | None, Decimal | None]:
    """按当日金价推算贵金属的 (当前价值, 购置成本)。

    当前价值 = 克重 × 纯度系数 × 上金所 AU9999 金价（元/克）；
    购置成本按录入方式：
      - 克重计价：克重 × 克价 + 加工费
      - 一口价：一口价金额
    金价接口不可用时返回 (None, 购置成本)，由用户后续手工估值兜底。
    """
    from app.services import metal_price as price_service

    weight = ext.get("weight")
    purity = ext.get("purity")
    weight_dec = _dec(weight) if weight not in (None, "") else None
    ratio = _purity_ratio(_dec(purity)) if purity not in (None, "") else Decimal("0.999")

    mode = ext.get("pricing_mode") or "by_weight"
    flat = ext.get("flat_price")
    unit_price = ext.get("unit_price")
    fee = ext.get("processing_fee")

    acquire: Decimal | None = None
    if mode == "flat" and flat not in (None, ""):
        acquire = _dec(flat)
    elif weight_dec is not None and unit_price not in (None, ""):
        acquire = weight_dec * _dec(unit_price)
        if fee not in (None, ""):
            acquire += _dec(fee)

    current: Decimal | None = None
    if weight_dec is not None and weight_dec > 0:
        gold = await price_service.gold_price_per_gram()
        if gold is not None:
            current = (weight_dec * ratio * gold).quantize(Decimal("0.01"))
    if current is None and acquire is not None:
        # 金价不可用：至少用购置价作为当前价值，避免资产不计入净资产
        current = acquire
    return current, acquire


def _purity_ratio(value: Decimal | None) -> Decimal:
    """纯度归一化成 0~1 系数。

    兼容常见写法：0.9999（系数）、99.99（百分数）、999 / 999.9（千分数）、9999（万分数）。
    """
    if value is None or value <= 0:
        return Decimal("0.999")
    if value <= 1:
        ratio = value
    elif value <= 100:
        ratio = value / Decimal("100")
    elif value <= 1000:
        ratio = value / Decimal("1000")
    else:
        ratio = value / Decimal("10000")
    ratio = ratio.quantize(Decimal("0.0001"))
    return min(ratio, Decimal("1")) if ratio > 0 else Decimal("0.999")


async def create_asset(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None
) -> tuple[Asset, BusinessEvent | None]:
    """新资产。可选：同时登记资产购置事件（草稿或直接确认）。

    - `acquisition.mode = none`：只登记资产（补录既有资产），不生成资金事件；
    - `mode = draft`：生成资产购置草稿事件，确认后才改变账户余额；
    - `mode = confirm`：生成并确认资产购置事件，账户减少、资产增加同时生效。
    """
    # 分类优先：先按分类推导出类型再做通用校验。
    # 否则前端从「资产」总览页新增时（asset_type 缺省或 other）会落到 other，
    # 导致房贷、租约的「关联资产」都选不到这条资产（06 §2、08 §3）。
    if payload.get("receivable"):
        payload = {**payload, "asset_type": "other"}
    if payload.get("category_id") and payload.get("asset_type") in (None, "", "other"):
        derived = await _derive_asset_type(db, payload["category_id"])
        if derived:
            payload = {**payload, "asset_type": derived}

    data = _validate_generic(payload, partial=False)
    asset_type = data["asset_type"]

    # 类型扩展必填校验（06 §2：资产类型决定必填字段）
    extension_payload = payload.get("extension") or {}
    # 不动产不再要求用户选「房产类型」：按分类推导，缺省视为住宅
    if asset_type == "real_estate" and not extension_payload.get("property_type"):
        extension_payload = {**extension_payload, "property_type": "residence"}
    missing = [
        field for field in REQUIRED_BY_TYPE.get(asset_type, ())
        if extension_payload.get(field) in (None, "")
    ]
    if missing:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "资产类型必填字段未填写完整",
            field_errors={f"extension.{field}": "该类型必填字段" for field in missing},
        )

    # 贵金属：当前价值与购置成本按当日金价自动推算（不再让用户手填当前价格）
    auto_acquire_cost: Decimal | None = None
    if asset_type == "precious_metal":
        auto_value, auto_acquire_cost = await _metal_auto_values(extension_payload)
        if auto_value is not None:
            data["current_value"] = auto_value
            data.setdefault("value_source", "market")
        if auto_acquire_cost is not None and (
            data.get("acquire_cost") in (None, "") or _dec(data.get("acquire_cost")) <= 0
        ):
            data["acquire_cost"] = auto_acquire_cost

    # 业务时间（购置日、估值日、购置事件发生时间）一律用本地墙钟，
    # 否则零点后登记的资产会被算到前一天，购置事件也会少 8 小时。
    now = local_now()
    asset = Asset(
        id=new_id(),
        household_id=user.household_id or "",
        name=data["name"],
        asset_type=asset_type,
        sub_type=data.get("sub_type"),
        category_id=data.get("category_id"),
        owner_member_id=data.get("owner_member_id"),
        currency=data.get("currency", "CNY"),
        acquire_date=data.get("acquire_date") or now,
        acquire_cost=data.get("acquire_cost", Decimal("0")),
        current_value=data.get("current_value", Decimal("0")),
        value_source=data.get("value_source"),
        last_valuation_date=(
            now if data.get("current_value") is not None and _dec(data["current_value"]) > 0 else None
        ),
        status=data.get("status", "active"),
        include_in_net_worth=data.get("include_in_net_worth", True),
        linked_account_id=data.get("linked_account_id"),
        linked_liability_id=data.get("linked_liability_id"),
        depreciation_method=data.get("depreciation_method"),
        maintenance_date=data.get("maintenance_date"),
        custom_attributes=data.get("custom_attributes"),
        note=data.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(asset)
    await db.flush()

    # 类型扩展表（同事务写入，失败整体回滚）
    from app.services import asset_extensions as ext_service

    await ext_service.create_extension(db, asset, extension_payload)

    # 借出款：写入应收明细，并把「当前价值 / 购置成本」校正为本金（未收回本金）
    if payload.get("receivable"):
        from app.services import receivables as receivable_service

        await receivable_service.create_for_asset(db, user, asset, payload["receivable"])

    # 初始估值：登记当前价值时写入估值时间线，保证时间线完整
    if data.get("current_value") is not None and _dec(data.get("current_value")) > 0:
        db.add(AssetValuation(
            id=new_id(), household_id=asset.household_id, asset_id=asset.id,
            value=data["current_value"], currency=asset.currency, valued_at=now,
            source=data.get("value_source") or "manual", reason="资产登记初始价值",
            created_by=user.user_id, updated_by=user.user_id,
        ))

    await audit_service.record_audit(
        db,
        action="asset.create",
        action_label="新增资产",
        object_type="asset",
        object_id=asset.id,
        after={
            "name": asset.name, "asset_type": asset.asset_type, "currency": asset.currency,
            "acquire_cost": asset.acquire_cost, "current_value": asset.current_value,
            "status": asset.status, "include_in_net_worth": asset.include_in_net_worth,
        },
        user=user,
        ctx=ctx,
    )

    event: BusinessEvent | None = None
    acquisition = payload.get("acquisition") or {}
    mode = acquisition.get("mode") or "none"
    if mode not in ("none", "draft", "confirm"):
        raise AppError(ErrorCode.VALIDATION_ERROR, "购置事件模式不合法",
                       field_errors={"acquisition.mode": "可选值为 none / draft / confirm"})
    if mode != "none":
        if not acquisition.get("account_id"):
            raise AppError(ErrorCode.VALIDATION_ERROR, "登记购置事件必须选择资金来源账户",
                           field_errors={"acquisition.account_id": "请选择资金来源账户"})
        # 资金来源账户必须属于当前家庭且处于可用状态，否则资金变动会挂到别人/停用的账户上
        from app.db.models.business import Account

        source_account = (await db.execute(
            select(Account).where(Account.id == acquisition["account_id"])
        )).scalars().first()
        if source_account is None or source_account.household_id != user.household_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "资金来源账户不存在",
                           field_errors={"acquisition.account_id": "资金来源账户不存在"})
        if source_account.status != "active":
            raise AppError(ErrorCode.VALIDATION_ERROR, "资金来源账户已停用",
                           field_errors={"acquisition.account_id": "账户已停用"})
        amount = acquisition.get("amount")
        if amount in (None, ""):
            amount = data.get("acquire_cost") or 0
        try:
            amount_value = Decimal(str(amount))
            if not amount_value.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "购置金额格式不正确",
                           field_errors={"acquisition.amount": "金额必须是数字"})
        if amount_value < 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "购置金额不能为负数",
                           field_errors={"acquisition.amount": "金额必须大于等于 0"})
        occurred_at = _parse_dt(acquisition.get("occurred_at"), "acquisition.occurred_at") or now

        event = BusinessEvent(
            id=new_id(), household_id=asset.household_id, event_no=await _new_event_no(),
            event_type="asset_acquire",
            status="draft" if mode == "draft" else "confirmed",
            occurred_at=occurred_at, confirmed_at=occurred_at if mode == "confirm" else None,
            amount=amount_value, currency=asset.currency,
            original_amount=amount_value, original_currency=asset.currency,
            base_currency=asset.currency, conversion_status="same_currency",
            account_id=acquisition["account_id"], asset_id=asset.id,
            member_id=asset.owner_member_id,
            title=f"资产购置 · {asset.name}", source="manual",
            note=acquisition.get("note"), version=1,
            created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(event)
        await db.flush()
        if mode == "confirm":
            from app.services import events as event_service

            for spec in event_service.movement_specs(event):
                db.add(EventMovement(
                    id=new_id(), household_id=asset.household_id, event_id=event.id,
                    target_type=spec["target_type"], target_id=spec["target_id"],
                    direction=spec["direction"], amount=amount_value, currency=asset.currency,
                    note="资产购置", created_by=user.user_id, updated_by=user.user_id,
                ))
        await audit_service.record_audit(
            db,
            action="event.create_draft" if mode == "draft" else "event.confirm",
            action_label="资产购置事件",
            object_type="business_event",
            object_id=event.id,
            after={"event_no": event.event_no, "asset_id": asset.id,
                   "amount": amount_value, "status": event.status},
            user=user,
            ctx=ctx,
        )
    return asset, event


async def update_asset(
    db: AsyncSession, user: CurrentUser, asset_id: str, payload: dict,
    expected_version: int | None = None, ctx: RequestContext | None = None,
) -> Asset:
    asset = await get_asset(db, user, asset_id)
    if expected_version is not None and asset.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)

    data = _validate_generic(payload, partial=True)
    before = {f: getattr(asset, f) for f in GENERIC_EDITABLE}

    # 分类优先：改分类时同步推导类型，与创建路径保持一致
    if data.get("category_id"):
        derived = await _derive_asset_type(db, data["category_id"])
        if derived:
            data["asset_type"] = derived

    # 状态机：出售/报废/归档只能走专用动作，不允许直接改状态（11 §2、15 §2）
    if "status" in data:
        if data["status"] in INACTIVE_ASSET_STATUSES:
            raise AppError(
                ErrorCode.STATE_INVALID,
                "出售、报废与归档必须通过「处置」或「归档」操作完成，不能直接修改状态",
                field_errors={"status": "请使用处置或归档操作"},
                status_code=409,
            )
        # 已处置/已报废不能直接复活：处置事件的资金变动已经入账，
        # 直接改回在用会让资产重新计入净资产，净值重复。恢复必须走冲销。
        if asset.status in ("disposed", "scrapped") and data["status"] == "active":
            raise AppError(
                ErrorCode.STATE_INVALID,
                "已处置/已报废的资产不能直接改回「在用」；如需恢复，请先冲销对应的处置事件",
                field_errors={"status": "请先冲销处置事件"},
                status_code=409,
            )

    # 类型切换：已有业务数据不允许直接切换（15 §9）
    new_type = data.get("asset_type", asset.asset_type)
    if new_type != asset.asset_type:
        used = await _business_data_count(db, asset.id)
        if used > 0:
            raise AppError(
                ErrorCode.STATE_INVALID,
                "该资产已有估值或资金事件，不能直接切换类型；请通过更正流程处理",
                field_errors={"asset_type": "已有业务数据的资产不可切换类型"},
                status_code=409,
            )

    # 贵金属：只有克重/纯度/计价方式/金价/加工费真的变了才按当日金价重算；
    # 只改备注或名称不应该改变资产价值，也不应该往估值时间线里塞记录
    auto_valued = False
    extension_payload = payload.get("extension")
    if asset.asset_type == "precious_metal":
        from app.db.models.assets import PreciousMetal

        existing = (await db.execute(
            select(PreciousMetal).where(PreciousMetal.asset_id == asset.id)
        )).scalars().first()
        metal_changed = existing is None and bool(extension_payload)
        if existing is not None and extension_payload:
            for key in ("weight", "purity", "pricing_mode", "flat_price", "unit_price", "processing_fee"):
                new_raw = extension_payload.get(key)
                if key not in extension_payload or new_raw in (None, ""):
                    continue
                old_raw = getattr(existing, key)
                if key == "pricing_mode":
                    same = str(new_raw) == str(old_raw)
                else:
                    try:
                        same = Decimal(str(new_raw)) == Decimal(str(old_raw or 0))
                    except Exception:  # noqa: BLE001
                        same = str(new_raw) == str(old_raw)
                if not same:
                    metal_changed = True
                    break
        if metal_changed:
            merged: dict = {}
            if existing is not None:
                merged = {
                    "weight": existing.weight, "purity": existing.purity,
                    "pricing_mode": existing.pricing_mode, "flat_price": existing.flat_price,
                    "unit_price": existing.unit_price, "processing_fee": existing.processing_fee,
                }
            if extension_payload:
                merged.update({k: v for k, v in extension_payload.items() if v not in (None, "")})
            auto_value, auto_acquire = await _metal_auto_values(merged)
            if auto_value is not None:
                data["current_value"] = auto_value
                auto_valued = True
                if auto_acquire is not None and _dec(data.get("acquire_cost", asset.acquire_cost)) <= 0:
                    data["acquire_cost"] = auto_acquire

    # 修改已确认的当前价值必须填写调整原因（06 §2）；系统按金价自动估值不要求手填
    value_changed = False
    new_value = data.get("current_value")
    if "current_value" in data and _dec(new_value) != _dec(asset.current_value):
        value_changed = True
        if auto_valued:
            data["last_valuation_date"] = local_now()
            data.setdefault("value_source", "market")
            payload = {**payload, "reason": "按当日金价自动估值"}
        else:
            reason = (payload.get("reason") or "").strip()
            if not reason:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "修改已确认的当前价值必须填写调整原因",
                    field_errors={"reason": "请填写调整原因"},
                )
            data["last_valuation_date"] = local_now()
            if "value_source" not in data:
                data["value_source"] = "manual"

    for field, value in data.items():
        setattr(asset, field, value)

    # 类型扩展
    if extension_payload:
        from app.services import asset_extensions as ext_service

        # 只校验本次提交中显式置空的必填字段；未提交的字段保持原值
        empty_required = [
            field for field in REQUIRED_BY_TYPE.get(asset.asset_type, ())
            if field in extension_payload and extension_payload.get(field) in (None, "")
        ]
        if empty_required:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "资产类型必填字段未填写完整",
                field_errors={f"extension.{field}": "该类型必填字段" for field in empty_required},
            )
        await ext_service.update_extension(db, asset, extension_payload)
    # 借出款：编辑时同步明细（以前这里被忽略，改动会静默丢失）
    if payload.get("receivable"):
        from app.services import receivables as receivable_service

        await receivable_service.update_for_asset(db, user, asset, payload["receivable"])


    if value_changed:
        db.add(AssetValuation(
            id=new_id(), household_id=asset.household_id, asset_id=asset.id,
            value=new_value, currency=asset.currency,
            valued_at=data.get("last_valuation_date") or local_now(),
            source=data.get("value_source") or "manual",
            reason=payload.get("reason"),
            created_by=user.user_id, updated_by=user.user_id,
        ))

    asset.version += 1
    asset.updated_by = user.user_id

    after = {f: getattr(asset, f) for f in GENERIC_EDITABLE}
    await audit_service.record_audit(
        db,
        action="asset.update",
        action_label="编辑资产",
        object_type="asset",
        object_id=asset.id,
        before=before,
        after=after,
        reason=payload.get("reason"),
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return asset


async def _business_data_count(db: AsyncSession, asset_id: str) -> int:
    valuations = int(
        (await db.execute(
            select(func.count(AssetValuation.id)).where(AssetValuation.asset_id == asset_id)
        )).scalar_one()
    )
    events = int(
        (await db.execute(
            select(func.count(BusinessEvent.id)).where(
                BusinessEvent.asset_id == asset_id, BusinessEvent.deleted_at.is_(None)
            )
        )).scalar_one()
    )
    return valuations + events


async def list_valuations(
    db: AsyncSession, user: CurrentUser, asset_id: str, *, limit: int = 100
) -> list[AssetValuation]:
    await get_asset(db, user, asset_id)
    return list(
        (await db.execute(
            select(AssetValuation)
            .where(AssetValuation.asset_id == asset_id, AssetValuation.deleted_at.is_(None))
            .order_by(AssetValuation.valued_at.desc(), AssetValuation.created_at.desc())
            .limit(limit)
        )).scalars().all()
    )


def serialize_valuation(v: AssetValuation) -> dict:
    return {
        "id": v.id,
        "asset_id": v.asset_id,
        "value": money(v.value),
        "currency": v.currency,
        "valued_at": local_iso(v.valued_at) if v.valued_at else None,
        "source": v.source,
        "source_label": enum_zh.zh("valuation_source", v.source),
        "source_note": v.source_note,
        "reason": v.reason,
        "note": v.note,
        "created_at": utc_iso(v.created_at),
    }


async def add_valuation(
    db: AsyncSession, user: CurrentUser, asset_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> AssetValuation:
    """新增估值：写入估值时间线并更新资产当前价值（06 §2 新增估值）。"""
    asset = await get_asset(db, user, asset_id)
    field_errors: dict[str, str] = {}

    if payload.get("value") in (None, ""):
        field_errors["value"] = "请填写估值金额"
        value = Decimal("0")
    else:
        try:
            value = Decimal(str(payload["value"]))
            if not value.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors["value"] = "金额格式不正确"
            value = Decimal("0")
        else:
            if value < 0:
                field_errors["value"] = "估值金额不能为负数"

    source = payload.get("source") or "manual"
    if source not in enum_zh.VALUATION_SOURCE:
        field_errors["source"] = "估值来源不合法"

    valued_at = _parse_dt(payload.get("valued_at"), "valued_at") or local_now()

    reason = (payload.get("reason") or "").strip()
    had_valuation = asset.last_valuation_date is not None
    if had_valuation and value != _dec(asset.current_value) and not reason:
        field_errors["reason"] = "调整已确认价值必须填写原因"

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "估值信息校验失败", field_errors=field_errors)

    if asset.status in INACTIVE_ASSET_STATUSES:
        raise AppError(
            ErrorCode.STATE_INVALID,
            f"该资产状态为「{enum_zh.zh('asset_status', asset.status)}」，不能继续登记估值",
            status_code=409,
        )

    valuation = AssetValuation(
        id=new_id(),
        household_id=asset.household_id,
        asset_id=asset.id,
        value=value,
        currency=asset.currency,
        valued_at=valued_at,
        source=source,
        source_note=payload.get("source_note"),
        reason=reason or None,
        note=payload.get("note"),
        attachment_id=payload.get("attachment_id"),
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(valuation)

    before = {"current_value": asset.current_value, "last_valuation_date": asset.last_valuation_date}
    asset.current_value = value
    asset.last_valuation_date = valued_at
    asset.value_source = source
    asset.version += 1
    asset.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="asset.valuation",
        action_label="新增估值",
        object_type="asset",
        object_id=asset.id,
        before=before,
        after={"current_value": asset.current_value, "last_valuation_date": asset.last_valuation_date,
               "value_source": source},
        reason=reason or None,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return valuation


async def dispose_asset(
    db: AsyncSession, user: CurrentUser, asset_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> tuple[Asset, BusinessEvent]:
    """处置资产（06 §2：必须填写处置日期、金额、去向账户和原因）。

    生成已确认的 asset_dispose 事件：资产减少 + 资金增加（金额为 0 的报废不生成账户变动）。
    """
    asset = await get_asset(db, user, asset_id)
    field_errors: dict[str, str] = {}

    if asset.status in INACTIVE_ASSET_STATUSES:
        raise AppError(
            ErrorCode.STATE_INVALID,
            f"该资产状态为「{enum_zh.zh('asset_status', asset.status)}」，不能重复处置",
            status_code=409,
        )

    dispose_type = payload.get("dispose_type") or "sold"
    if dispose_type not in ("sold", "scrapped"):
        field_errors["dispose_type"] = "处置方式只能是出售或报废"

    try:
        amount = Decimal(str(payload.get("amount") if payload.get("amount") not in (None, "") else "0"))
        if not amount.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        field_errors["amount"] = "金额格式不正确"
        amount = Decimal("0")
    if amount < 0:
        field_errors["amount"] = "金额不能为负数"

    account_id = payload.get("account_id")
    if amount > 0 and not account_id:
        field_errors["account_id"] = "有处置收入时必须选择去向账户"

    reason = (payload.get("reason") or "").strip()
    if not reason:
        field_errors["reason"] = "请填写处置原因"

    occurred_at = _parse_dt(payload.get("occurred_at"), "occurred_at") or local_now()

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "处置信息校验失败", field_errors=field_errors)

    if account_id:
        from app.db.models.business import Account

        account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
        if account is None or account.household_id != user.household_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "去向账户不存在",
                           field_errors={"account_id": "去向账户不存在"})
        if account.status != "active":
            raise AppError(ErrorCode.VALIDATION_ERROR, "去向账户已停用",
                           field_errors={"account_id": "账户已停用"})

    previous_status = asset.status

    # 已实现损益 = 处置收入 − 成本基数（15 §6.2：差额由确定性服务计算）
    # 贵金属的成本基数含加工费与税费（首饰加工费不等于金属价值）；其余资产以购置成本为基数。
    cost_basis = _dec(asset.acquire_cost)
    if asset.asset_type == "precious_metal":
        from app.db.models.assets import PreciousMetal

        metal = (await db.execute(
            select(PreciousMetal).where(PreciousMetal.asset_id == asset.id)
        )).scalars().first()
        if metal is not None:
            cost_basis += _dec(metal.processing_fee) + _dec(metal.tax_cost)
    realized_gain = amount - cost_basis

    event = BusinessEvent(
        id=new_id(), household_id=asset.household_id, event_no=await _new_event_no(),
        event_type="asset_dispose", status="confirmed",
        occurred_at=occurred_at, confirmed_at=occurred_at,
        amount=amount, currency=asset.currency,
        original_amount=amount, original_currency=asset.currency,
        base_currency=asset.currency, conversion_status="same_currency",
        account_id=account_id, asset_id=asset.id, member_id=asset.owner_member_id,
        title=f"资产处置 · {asset.name}（{'出售' if dispose_type == 'sold' else '报废'}）",
        counterparty=payload.get("counterparty"), source="manual",
        realized_gain=realized_gain,
        note=payload.get("note"), version=1,
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(event)
    await db.flush()

    from app.services import events as event_service

    for spec in event_service.movement_specs(event):
        db.add(EventMovement(
            id=new_id(), household_id=asset.household_id, event_id=event.id,
            target_type=spec["target_type"], target_id=spec["target_id"],
            direction=spec["direction"], amount=amount, currency=asset.currency,
            note="资产处置", created_by=user.user_id, updated_by=user.user_id,
        ))

    asset.status = "disposed" if dispose_type == "sold" else "scrapped"
    asset.version += 1
    asset.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="asset.dispose",
        action_label="处置资产",
        object_type="asset",
        object_id=asset.id,
        related_object_id=event.id,
        before={"status": previous_status, "current_value": asset.current_value},
        after={"status": asset.status, "dispose_amount": amount,
               "cost_basis": cost_basis, "realized_gain": realized_gain,
               "account_id": account_id, "event_no": event.event_no},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return asset, event


async def archive_asset(
    db: AsyncSession, user: CurrentUser, asset_id: str, reason: str,
    ctx: RequestContext | None = None,
) -> Asset:
    """归档资产（15 §2：归档不能直接删除，必须留下原因；历史与关联事件保留）。"""
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "归档必须填写原因",
                       field_errors={"reason": "请填写归档原因"})
    asset = await get_asset(db, user, asset_id)
    if asset.status == "archived":
        raise AppError(ErrorCode.STATE_INVALID, "该资产已归档", status_code=409)

    before = {"status": asset.status}
    asset.status = "archived"
    asset.version += 1
    asset.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="asset.archive",
        action_label="归档资产",
        object_type="asset",
        object_id=asset.id,
        before=before,
        after={"status": asset.status},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return asset


async def restore_status_after_reversal(db: AsyncSession, asset_id: str, user: CurrentUser) -> None:
    """资产处置事件被冲销后，恢复资产处置前状态（状态取自处置时的审计记录）。"""
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalars().first()
    if asset is None:
        return
    if asset.status not in ("disposed", "scrapped"):
        return

    log = (await db.execute(
        select(audit_service.AuditLog)
        .where(
            audit_service.AuditLog.object_type == "asset",
            audit_service.AuditLog.object_id == asset_id,
            audit_service.AuditLog.action == "asset.dispose",
        )
        .order_by(audit_service.AuditLog.created_at.desc())
        .limit(1)
    )).scalars().first()

    restored = "active"
    if log and log.before_summary:
        try:
            restored = json.loads(log.before_summary).get("status") or "active"
        except json.JSONDecodeError:
            restored = "active"

    before = {"status": asset.status}
    asset.status = restored
    asset.version += 1
    asset.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="asset.dispose_reverted",
        action_label="冲销资产处置并恢复状态",
        object_type="asset",
        object_id=asset.id,
        before=before,
        after={"status": asset.status},
        user=user,
    )
