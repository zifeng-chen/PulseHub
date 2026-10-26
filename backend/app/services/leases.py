"""房产租赁服务（06 §9、15 §3.3、02 §2）。

规则：
- 房产主对象复用 real_estates，不另建 properties 主数据；
- 应收登记不改变账户余额；实收必须关联到账账户并计入收入；
- 押金单独显示为待退还资金，收取/退还都不计入收入或支出；
- 逾期由应付日与当前日期派生，不建立后台任务；
- 终止租约需二次确认令牌；状态机 draft -> active -> expired/terminated（11 §2）。
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.timeutil import to_local_aware, local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.money import fmt_amount
from app.core.logging import mask_value
from app.db.models.business import Account, Asset, BusinessEvent
from app.db.models.leases import (
    Lease,
    LeaseDeposit,
    LeaseReceivable,
    LeaseSettlement,
    LeaseSettlementItem,
)
from app.services import audit as audit_service
from app.services import confirm as confirm_service


LEASE_TYPES = {"rent_receivable", "rent_received", "deposit_receive", "deposit_return"}

CALIBER = {
    "receivable": "应收登记不改变账户余额；实收确认后计入收入并核销应收",
    "deposit": "押金收取/退还只改变账户余额，不计入收入或支出，单独显示为待退还资金",
    "overdue": "逾期 = 未收且应付日早于当日，由确定性服务派生",
    "collection_rate": "收租率 = 已实收金额 /（已实收 + 未收应收）",
}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _aware(dt: datetime | None) -> datetime | None:
    """给数据库里的本地墙钟时间补上本地时区，保证与 now 可比。"""
    return to_local_aware(dt)


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


def _new_lease_no() -> str:
    stamp = local_now().strftime("%Y%m%d")
    return f"LS{stamp}{new_id()[:6].upper()}"


# ---------------- 计费周期 ----------------

# 付款周期 → 每期包含的月数（周租单独按 12/52 折算）
FREQUENCY_MONTHS = {
    "monthly": 1,
    "quarterly": 3,
    "half_yearly": 6,
    "yearly": 12,
    "one_time": 1,
}


def _add_months(base: datetime, months: int) -> datetime:
    """按自然月推进；目标月没有该日时落到月末（1/31 + 1 月 → 2/28）。"""
    index = base.month - 1 + months
    year = base.year + index // 12
    month = index % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day)


def period_amount(lease: Lease) -> Decimal:
    """每期应收金额 = 月租 × 周期月数。周租按 12/52 折算，避免用「每月 4 周」少算。"""
    rent = _dec(lease.monthly_rent)
    if rent <= 0:
        return Decimal("0")
    freq = lease.payment_frequency or "monthly"
    if freq == "weekly":
        return (rent * Decimal("12") / Decimal("52")).quantize(Decimal("0.01"))
    return (rent * Decimal(FREQUENCY_MONTHS.get(freq, 1))).quantize(Decimal("0.01"))


def next_period_date(base: datetime | None, frequency: str | None) -> datetime | None:
    """下一期收租日：从本期应付日（没有则到账日）按付款周期推进。

    一次性租约没有下一期，返回 None；周租按 7 天推进。
    """
    if base is None:
        return None
    freq = frequency or "monthly"
    if freq == "one_time":
        return None
    if freq == "weekly":
        return base + timedelta(days=7)
    return _add_months(base, FREQUENCY_MONTHS.get(freq, 1))


def next_due_after(
    lease: Lease, received_on: datetime, matched_due: datetime | None = None,
) -> datetime | None:
    """下一期应付日：优先接在本期应付日之后，否则按**合同起租日**的周期往后对齐。

    只按到账日机械加一个周期会跟合同脱节（例如 5 月 17 日起租的季付，
    10 月 24 日收租后下一期应是 11 月 17 日，而不是次年 1 月 24 日）。
    """
    freq = lease.payment_frequency or "monthly"
    if freq == "one_time":
        return None
    if matched_due is not None:
        return next_period_date(matched_due, freq)

    start = to_local_naive(lease.start_date) if lease.start_date else None
    if start is None:
        return next_period_date(received_on, freq)

    cursor = start
    nxt = next_period_date(cursor, freq)
    # 从起租日一格格推到第一个「晚于本次到账日」的应付日；加次数上限防脏数据死循环
    for _ in range(600):
        if nxt is None or nxt > received_on:
            break
        cursor = nxt
        nxt = next_period_date(cursor, freq)
    return nxt


def _earliest_pending(receivables: list[LeaseReceivable]) -> LeaseReceivable | None:
    pending = [r for r in receivables if r.status == "pending"]
    if not pending:
        return None
    return min(pending, key=lambda r: (r.due_date or datetime.max))


async def receipt_preview(
    db: AsyncSession, user: CurrentUser, lease_id: str, occurred_at: str | None = None,
) -> dict:
    """登记实收前的即时计算：本期金额、匹配的待收应收、下次收租日。"""
    lease = await _get_lease(db, user, lease_id)
    receivables = list((await db.execute(
        select(LeaseReceivable).where(
            LeaseReceivable.lease_id == lease.id,
            LeaseReceivable.deleted_at.is_(None),
        )
    )).scalars().all())
    matched = _earliest_pending(receivables)
    received_on = _parse_dt(occurred_at, "occurred_at") or local_now()
    # 有应收时接在本期应付日之后；没有则按合同起租日的周期对齐
    nxt = next_due_after(
        lease, received_on,
        to_local_naive(matched.due_date) if matched is not None and matched.due_date else None,
    )
    amount = period_amount(lease)
    return {
        "amount": fmt_amount(amount),
        "currency": lease.currency,
        "payment_frequency": lease.payment_frequency,
        "payment_frequency_label": enum_zh.zh("payment_frequency", lease.payment_frequency),
        "occurred_at": local_iso(received_on),
        "receivable_id": matched.id if matched is not None else None,
        "receivable_amount": fmt_amount(matched.amount) if matched is not None else None,
        "due_date": local_iso(matched.due_date) if matched is not None and matched.due_date else None,
        "next_due_date": local_iso(nxt),
        "period_start": local_iso(nxt),
        "period_end": local_iso(
            (next_period_date(nxt, lease.payment_frequency) or nxt) - timedelta(days=1)
        ) if nxt else None,
        "lease_end_date": local_iso(lease.end_date),
    }


# ---------------- 序列化 ----------------

def serialize_receivable(r: LeaseReceivable, today: datetime | None = None) -> dict:
    now = _aware(today) or datetime.now(timezone.utc)
    overdue = r.status == "pending" and _aware(r.due_date) is not None and _aware(r.due_date) < now
    return {
        "id": r.id,
        "lease_id": r.lease_id,
        "period_start": local_iso(r.period_start) if r.period_start else None,
        "period_end": local_iso(r.period_end) if r.period_end else None,
        "due_date": local_iso(r.due_date) if r.due_date else None,
        "amount": fmt_amount(r.amount),
        "currency": r.currency,
        "status": r.status,
        "status_label": enum_zh.zh("receivable_status", r.status),
        "overdue": overdue,
        "paid_at": local_iso(r.paid_at) if r.paid_at else None,
        "paid_amount": fmt_amount(r.paid_amount),
        "event_id": r.event_id,
        "note": r.note,
        "version": r.version,
        "created_at": utc_iso(r.created_at),
    }


def serialize_deposit(d: LeaseDeposit) -> dict:
    return {
        "id": d.id,
        "lease_id": d.lease_id,
        "amount": fmt_amount(d.amount),
        "currency": d.currency,
        "status": d.status,
        "status_label": enum_zh.zh("deposit_status", d.status),
        "received_at": local_iso(d.received_at) if d.received_at else None,
        "return_date": local_iso(d.return_date) if d.return_date else None,
        "note": d.note,
        "version": d.version,
        "created_at": utc_iso(d.created_at),
    }


def _next_receipt_date(receivables: list[LeaseReceivable]) -> str | None:
    """下一期收租日：取最早一笔待收租金的应付日。

    只从「已生成的收租计划」里取，不按周期硬推算——凭空算出来的日期会和实际
    收款安排不一致，反而让人误判。没有计划时返回 None，由前端提示先生成应收。
    """
    pending = [r for r in receivables if r.status == "pending" and r.due_date]
    if not pending:
        return None
    return local_iso(min(r.due_date for r in pending))


def serialize_lease(
    lease: Lease,
    real_estate: Asset | None = None,
    receivables: list[LeaseReceivable] | None = None,
    deposits: list[LeaseDeposit] | None = None,
    reveal: bool = False,
    category_label: str | None = None,
) -> dict:
    # 「已收押金」= 手工勾选，或确实登记过到账的押金记录
    deposit_received = bool(lease.deposit_received) or any(
        d.status == "held" and d.received_at for d in (deposits or [])
    )
    # 押金来源：record=登记过到账记录；flag=只勾了「已收」；none=未收
    has_held_record = any(d.status == "held" for d in (deposits or []))
    if has_held_record:
        deposit_source = "record"
    elif deposit_received:
        deposit_source = "flag"
    else:
        deposit_source = "none"
    held_from_records = sum(
        (_dec(d.amount) for d in (deposits or []) if d.status == "held"), Decimal("0")
    )
    # 只勾选未登记时，押金就是租约上写的金额，不能显示 0
    held_amount = held_from_records if has_held_record else (
        _dec(lease.deposit_amount) if deposit_received else Decimal("0")
    )
    return {
        "id": lease.id,
        "lease_no": lease.lease_no,
        "category_id": lease.category_id,
        "category_label": category_label or "",
        "real_estate_id": lease.real_estate_id,
        "real_estate_name": real_estate.name if real_estate else None,
        "tenant_name": lease.tenant_name,
        "tenant_contact": (lease.tenant_contact if reveal else
                           (mask_value(lease.tenant_contact) if lease.tenant_contact else None)),
        "lease_purpose": lease.lease_purpose,
        "monthly_rent": fmt_amount(lease.monthly_rent),
        "payment_frequency": lease.payment_frequency,
        "payment_frequency_label": enum_zh.zh("payment_frequency", lease.payment_frequency),
        "currency": lease.currency,
        "start_date": local_iso(lease.start_date) if lease.start_date else None,
        "end_date": local_iso(lease.end_date) if lease.end_date else None,
        "deposit_amount": fmt_amount(lease.deposit_amount),
        "receiving_account_id": lease.receiving_account_id,
        "property_repair_bearer": lease.property_repair_bearer,
        "property_repair_bearer_label": (
            enum_zh.zh("repair_bearer", lease.property_repair_bearer) if lease.property_repair_bearer else None
        ),
        "reminder_days": lease.reminder_days,
        "status": lease.status,
        "status_label": enum_zh.zh("lease_status", lease.status),
        "note": lease.note,
        "version": lease.version,
        "created_at": utc_iso(lease.created_at),
        "updated_at": utc_iso(lease.updated_at),
        "receivable_summary": {
            "count": len(receivables or []),
            "pending": sum(1 for r in (receivables or []) if r.status == "pending"),
            "paid": sum(1 for r in (receivables or []) if r.status == "paid"),
            "total_pending": fmt_amount(
                sum((_dec(r.amount) for r in (receivables or []) if r.status == 'pending'), Decimal('0'))
            ),
        },
        # 保留输出仅为兼容；房产是否计入净资产以资产自身的开关为准
        "include_in_net_worth": lease.include_in_net_worth is not False,
        "previous_lease_id": lease.previous_lease_id,
        "deposit_received": deposit_received,
        "deposit_include_in_net_worth": bool(lease.deposit_include_in_net_worth),
        "deposit_summary": {
            "count": len(deposits or []),
            "source": deposit_source,
            "held": fmt_amount(held_amount),
            "returned": fmt_amount(
                sum((_dec(d.amount) for d in (deposits or []) if d.status == 'returned'), Decimal('0'))
            ),
            # 押金「是否已收」：有 held 且已登记到账时间才算收到，仅记录事实、不计收入
            "received": deposit_received,
            "received_at": next(
                (local_iso(d.received_at) for d in (deposits or [])
                 if d.status == "held" and d.received_at), None,
            ),
        },
        "next_receipt_date": _next_receipt_date(receivables or []),
    }


# ---------------- 校验 ----------------

async def _load_real_estate(db: AsyncSession, user: CurrentUser, asset_id: str) -> Asset:
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalars().first()
    if asset is None or asset.deleted_at is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "关联的不动产不存在",
                       field_errors={"real_estate_id": "请选择有效的不动产"})
    if asset.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    if asset.asset_type != "real_estate":
        raise AppError(ErrorCode.VALIDATION_ERROR, "只能对不动产建立租约",
                       field_errors={"real_estate_id": "该资产不是不动产"})
    return asset


async def _check_account(db: AsyncSession, user: CurrentUser, account_id: str | None) -> None:
    if not account_id:
        return
    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
    if account is None or account.deleted_at is not None or account.household_id != user.household_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "账户不存在",
                       field_errors={"account_id": "账户不存在"})
    if account.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, "该账户已停用，不能用于收款",
                       field_errors={"account_id": "账户已停用"})


def _validate_lease_payload(payload: dict, *, partial: bool) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}

    if "tenant_name" in payload or not partial:
        tenant_name = (payload.get("tenant_name") or "").strip()
        if not tenant_name:
            field_errors["tenant_name"] = "请填写租客名称"
        else:
            data["tenant_name"] = tenant_name

    if "payment_frequency" in payload and payload["payment_frequency"] not in (None, ""):
        if payload["payment_frequency"] not in enum_zh.PAYMENT_FREQUENCY:
            field_errors["payment_frequency"] = "付款周期不合法"
        else:
            data["payment_frequency"] = payload["payment_frequency"]

    for money_field in ("monthly_rent", "deposit_amount"):
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

    for date_field in ("start_date", "end_date"):
        if date_field in payload:
            data[date_field] = _parse_dt(payload.get(date_field), date_field)

    if "reminder_days" in payload and payload["reminder_days"] not in (None, ""):
        try:
            days = int(payload["reminder_days"])
        except (TypeError, ValueError):
            field_errors["reminder_days"] = "提醒天数必须是整数"
        else:
            if days < 0:
                field_errors["reminder_days"] = "提醒天数不能为负数"
            else:
                data["reminder_days"] = days

    if "property_repair_bearer" in payload and payload["property_repair_bearer"] not in (None, ""):
        if payload["property_repair_bearer"] not in enum_zh.REPAIR_BEARER:
            field_errors["property_repair_bearer"] = "承担方取值不合法"
        else:
            data["property_repair_bearer"] = payload["property_repair_bearer"]

    for plain in ("lease_purpose", "category_id", "tenant_contact", "currency", "receiving_account_id",
                  "attachment_id", "note"):
        if plain in payload:
            data[plain] = payload[plain] or None

    if "include_in_net_worth" in payload:
        data["include_in_net_worth"] = bool(payload["include_in_net_worth"])
    if "deposit_received" in payload:
        data["deposit_received"] = bool(payload["deposit_received"])
    if "deposit_include_in_net_worth" in payload:
        data["deposit_include_in_net_worth"] = bool(payload["deposit_include_in_net_worth"])

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "租约信息校验失败", field_errors=field_errors)
    return data


async def get_lease(db: AsyncSession, user: CurrentUser, lease_id: str) -> Lease:
    """对外暴露的取租约入口（合同模板等只读场景用）。"""
    return await _get_lease(db, user, lease_id)


async def _get_lease(db: AsyncSession, user: CurrentUser, lease_id: str) -> Lease:
    lease = (await db.execute(select(Lease).where(Lease.id == lease_id))).scalars().first()
    if lease is None or lease.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "租约不存在", status_code=404)
    if lease.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return lease


# ---------------- 房产列表 ----------------

async def list_properties(
    db: AsyncSession, user: CurrentUser, *, keyword: str | None = None, status: str | None = None
) -> list[dict]:
    """房产租赁列表（06 §9）：复用 real_estates，附加出租状态摘要。"""
    from app.db.models.assets import RealEstate

    conditions = [Asset.household_id == user.household_id, Asset.deleted_at.is_(None),
                  Asset.asset_type == "real_estate"]
    if keyword:
        conditions.append(Asset.name.ilike(f"%{keyword}%"))

    assets = list((await db.execute(select(Asset).where(*conditions))).scalars().all())
    if not assets:
        return []

    asset_ids = [a.id for a in assets]
    re_rows = {
        r.asset_id: r
        for r in (await db.execute(
            select(RealEstate).where(RealEstate.asset_id.in_(asset_ids))
        )).scalars().all()
    }
    active_leases = list((await db.execute(
        select(Lease).where(Lease.real_estate_id.in_(asset_ids), Lease.deleted_at.is_(None),
                            Lease.status.in_(("active", "draft")))
    )).scalars().all())
    leases_by_re = {}
    for l in active_leases:
        leases_by_re.setdefault(l.real_estate_id, []).append(l)

    items = []
    for a in assets:
        re_row = re_rows.get(a.id)
        leases = leases_by_re.get(a.id, [])
        current = next((l for l in leases if l.status == "active"), None)
        renting = current is not None
        if status == "rented" and not renting:
            continue
        if status == "vacant" and renting:
            continue
        items.append({
            "id": a.id,
            "name": a.name,
            "address": re_row.address if re_row else None,
            "current_value": fmt_amount(a.current_value),
            "currency": a.currency,
            "status": a.status,
            "has_lease": bool(renting),
            "lease_status": current.status if current else None,
            "lease_status_label": enum_zh.zh("lease_status", current.status) if current else None,
            "current_lease_id": current.id if current else None,
            "monthly_rent": fmt_amount(current.monthly_rent) if current else None,
            "end_date": local_iso(current.end_date) if current and current.end_date else None,
            "lease_count": len(leases),
            "vacancy_start_date": (
                local_iso(re_row.vacancy_start_date) if re_row and re_row.vacancy_start_date else None
            ),
        })
    return items


# ---------------- 租约 CRUD ----------------

async def list_leases(
    db: AsyncSession, user: CurrentUser, *,
    keyword: str | None = None, status: str | None = None, real_estate_id: str | None = None,
    category_id: str | None = None,
) -> list[Lease]:
    conditions = [Lease.household_id == user.household_id, Lease.deleted_at.is_(None)]
    if status:
        conditions.append(Lease.status == status)
    if real_estate_id:
        conditions.append(Lease.real_estate_id == real_estate_id)
    if category_id:
        conditions.append(Lease.category_id == category_id)
    if keyword:
        conditions.append(Lease.tenant_name.ilike(f"%{keyword}%") | Lease.lease_no.ilike(f"%{keyword}%"))

    leases = list((await db.execute(
        select(Lease).where(*conditions).order_by(Lease.created_at.desc())
    )).scalars().all())
    return leases


async def _leases_payload(db: AsyncSession, leases: list[Lease]) -> list[dict]:
    asset_ids = {l.real_estate_id for l in leases}
    assets = {
        a.id: a
        for a in (await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars().all()
    }
    lease_ids = [l.id for l in leases]
    receivables_map: dict[str, list[LeaseReceivable]] = {}
    deposits_map: dict[str, list[LeaseDeposit]] = {}
    if lease_ids:
        for row in (await db.execute(
            select(LeaseReceivable).where(
                LeaseReceivable.lease_id.in_(lease_ids),
                LeaseReceivable.deleted_at.is_(None),
            )
        )).scalars().all():
            receivables_map.setdefault(row.lease_id, []).append(row)
        for row in (await db.execute(
            select(LeaseDeposit).where(
                LeaseDeposit.lease_id.in_(lease_ids),
                LeaseDeposit.deleted_at.is_(None),
            )
        )).scalars().all():
            deposits_map.setdefault(row.lease_id, []).append(row)

    household_id = leases[0].household_id if leases else None
    cat_map = await _category_map(db, household_id)
    return [
        serialize_lease(
            l, real_estate=assets.get(l.real_estate_id),
            receivables=receivables_map.get(l.id, []),
            deposits=deposits_map.get(l.id, []),
            category_label=cat_map.get(l.category_id) if l.category_id else None,
        )
        for l in leases
    ]


async def _category_map(db: AsyncSession, household_id: str | None) -> dict[str, str]:
    from app.services import catalog as catalog_service

    return await catalog_service.category_label_map(db, household_id)


async def lease_payload(db: AsyncSession, lease: Lease, *, reveal: bool = False) -> dict:
    """单条租约序列化（带房产名称、下一期收租日与押金状态）。"""
    real_estate = (await db.execute(
        select(Asset).where(Asset.id == lease.real_estate_id))).scalars().first()
    receivables = list((await db.execute(
        select(LeaseReceivable).where(
            LeaseReceivable.lease_id == lease.id,
            LeaseReceivable.deleted_at.is_(None),
        )
    )).scalars().all())
    deposits = list((await db.execute(
        select(LeaseDeposit).where(
            LeaseDeposit.lease_id == lease.id,
            LeaseDeposit.deleted_at.is_(None),
        )
    )).scalars().all())
    cat_map = await _category_map(db, lease.household_id)
    return serialize_lease(
        lease, real_estate=real_estate, receivables=receivables, deposits=deposits,
        reveal=reveal,
        category_label=cat_map.get(lease.category_id) if lease.category_id else None,
    )


async def lease_detail(
    db: AsyncSession, user: CurrentUser, lease_id: str, *, reveal: bool = False,
) -> dict:
    lease = await _get_lease(db, user, lease_id)
    real_estate = (await db.execute(
        select(Asset).where(Asset.id == lease.real_estate_id))).scalars().first()
    receivables = list((await db.execute(
        select(LeaseReceivable).where(LeaseReceivable.lease_id == lease.id,
                                      LeaseReceivable.deleted_at.is_(None))
        .order_by(LeaseReceivable.due_date.asc())
    )).scalars().all())
    deposits = list((await db.execute(
        select(LeaseDeposit).where(LeaseDeposit.lease_id == lease.id,
                                   LeaseDeposit.deleted_at.is_(None))
    )).scalars().all())
    # 实收与押金往来走统一事件（rent_received/deposit_*）
    events = list((await db.execute(
        select(BusinessEvent).where(
            BusinessEvent.lease_id == lease.id, BusinessEvent.deleted_at.is_(None))
        .order_by(BusinessEvent.occurred_at.desc())
    )).scalars().all())
    from app.services import events as event_service

    cat_map = await _category_map(db, lease.household_id)
    data = serialize_lease(
        lease, real_estate=real_estate, receivables=receivables, deposits=deposits,
        reveal=reveal,
        category_label=cat_map.get(lease.category_id) if lease.category_id else None,
    )
    data["receivables"] = [serialize_receivable(r) for r in receivables]
    data["deposits"] = [serialize_deposit(d) for d in deposits]
    # 页签已按主题拆开：应收看「应收」、押金看「押金」，
    # 这里只留真正的租金实收，避免同一笔钱在两个页签里各出现一次
    PLAN_OR_DEPOSIT_EVENTS = {"rent_receivable", "deposit_receive", "deposit_return"}
    data["events"] = [
        event_service.serialize_event(e) for e in events
        if e.event_type not in PLAN_OR_DEPOSIT_EVENTS
    ]

    # 水电煤户号跟着房产走：租约详情直接显示，不需要再去不动产里翻
    from app.services import asset_extensions as ext_service

    utilities: list[dict] = []
    if lease.real_estate_id:
        try:
            utility_rows = await ext_service.list_utility_accounts(db, user, lease.real_estate_id)
            utilities = [ext_service.serialize_utility(r) for r in utility_rows]
        except AppError:
            # 房产已删除/无权限时不阻断租约详情
            utilities = []
    data["utilities"] = utilities
    data["settlements"] = [
        serialize_settlement(row, rows)
        for row, rows in await list_settlements(db, lease.id)
    ]
    data["caliber"] = CALIBER
    data["ai"] = {
        "allowed": ["收租率", "逾期", "合同到期", "押金余额"],
        "forbidden": ["不自动催收", "不混淆押金收入", "不自动确认应收"],
    }
    return data


async def create_lease(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None,
) -> Lease:
    real_estate_id = payload.get("real_estate_id")
    if not real_estate_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请选择关联房产",
                       field_errors={"real_estate_id": "请选择关联房产"})
    real_estate = await _load_real_estate(db, user, real_estate_id)
    data = _validate_lease_payload(payload, partial=False)
    await _check_account(db, user, data.get("receiving_account_id"))

    status = payload.get("status") or "active"
    if status not in ("draft", "active"):
        status = "active"

    lease = Lease(
        id=new_id(),
        household_id=user.household_id,
        lease_no=_new_lease_no(),
        category_id=data.get("category_id"),
        real_estate_id=real_estate.id,
        tenant_name=data["tenant_name"],
        tenant_contact=data.get("tenant_contact"),
        lease_purpose=data.get("lease_purpose"),
        monthly_rent=data.get("monthly_rent", Decimal("0")),
        payment_frequency=data.get("payment_frequency", "monthly"),
        currency=data.get("currency") or real_estate.currency or "CNY",
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
        deposit_amount=data.get("deposit_amount", Decimal("0")),
        receiving_account_id=data.get("receiving_account_id"),
        property_repair_bearer=data.get("property_repair_bearer"),
        reminder_days=data.get("reminder_days"),
        include_in_net_worth=data.get("include_in_net_worth", True),
        deposit_received=data.get("deposit_received", False),
        deposit_include_in_net_worth=data.get("deposit_include_in_net_worth", False),
        status=status,
        attachment_id=data.get("attachment_id"),
        note=data.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(lease)
    await db.flush()

    if status == "active":
        from app.db.models.assets import RealEstate

        re_row = (await db.execute(
            select(RealEstate).where(RealEstate.asset_id == real_estate.id))).scalars().first()
        if re_row is not None:
            re_row.has_lease = True
            re_row.current_lease_id = lease.id
            re_row.vacancy_start_date = None

    await audit_service.record_audit(
        db,
        action="lease.create",
        action_label="新建租约",
        object_type="lease",
        object_id=lease.id,
        related_object_id=real_estate.id,
        after={"lease_no": lease.lease_no, "tenant_name": lease.tenant_name,
               "monthly_rent": lease.monthly_rent, "status": lease.status},
        user=user,
        ctx=ctx,
    )
    return lease


async def update_lease(
    db: AsyncSession, user: CurrentUser, lease_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> Lease:
    lease = await _get_lease(db, user, lease_id)
    if lease.status in ("terminated",):
        raise AppError(ErrorCode.STATE_INVALID, "已终止的租约不能编辑", status_code=409)

    expected_version = payload.get("version")
    if expected_version is not None and int(expected_version) != lease.version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)

    data = _validate_lease_payload(payload, partial=True)
    await _check_account(db, user, data.get("receiving_account_id"))

    before = {f: getattr(lease, f) for f in data}
    for field, value in data.items():
        setattr(lease, field, value)
    lease.version += 1
    lease.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="lease.update",
        action_label="编辑租约",
        object_type="lease",
        object_id=lease.id,
        before=before,
        after={f: getattr(lease, f) for f in data},
        user=user,
        ctx=ctx,
    )
    return lease


async def terminate_lease(
    db: AsyncSession, user: CurrentUser, lease_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> Lease:
    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise AppError(ErrorCode.VALIDATION_ERROR, "终止租约必须填写原因",
                       field_errors={"reason": "请填写终止原因"})
    confirm_service.consume_token(payload.get("confirm_token"), user,
                                  "lease.terminate", "lease", lease_id)
    lease = await _get_lease(db, user, lease_id)
    if lease.status in ("terminated", "expired"):
        raise AppError(ErrorCode.STATE_INVALID, "该租约已终止或到期", status_code=409)

    before_status = lease.status
    lease.status = "terminated"
    lease.version += 1
    lease.updated_by = user.user_id

    from app.db.models.assets import RealEstate

    re_row = (await db.execute(
        select(RealEstate).where(RealEstate.asset_id == lease.real_estate_id))).scalars().first()
    if re_row is not None and re_row.current_lease_id == lease.id:
        re_row.has_lease = False
        re_row.current_lease_id = None
        re_row.vacancy_start_date = local_now()

    await audit_service.record_audit(
        db,
        action="lease.terminate",
        action_label="终止租约",
        object_type="lease",
        object_id=lease.id,
        before={"status": before_status},
        after={"status": lease.status},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    return lease


# ---------------- 应收 / 实收 ----------------

async def create_receivable(
    db: AsyncSession, user: CurrentUser, lease_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> LeaseReceivable:
    lease = await _get_lease(db, user, lease_id)
    amount = payload.get("amount")
    try:
        amount_value = Decimal(str(amount))
        if not amount_value.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额格式不正确",
                       field_errors={"amount": "金额必须是数字"})
    if amount_value <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "应收金额必须大于 0",
                       field_errors={"amount": "金额必须大于 0"})

    receivable = LeaseReceivable(
        id=new_id(),
        household_id=user.household_id,
        lease_id=lease.id,
        period_start=_parse_dt(payload.get("period_start"), "period_start"),
        period_end=_parse_dt(payload.get("period_end"), "period_end"),
        due_date=_parse_dt(payload.get("due_date"), "due_date") or local_now(),
        amount=amount_value,
        currency=lease.currency,
        status="pending",
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(receivable)
    await db.flush()

    # 应收事件（不改变账户余额）
    from app.services import events as event_service

    event = await event_service.create_draft(db, user, {
        "event_type": "rent_receivable",
        "title": f"租金应收 · {lease.tenant_name}",
        "amount": str(amount_value),
        "currency": lease.currency,
        "occurred_at": (payload.get("due_date") or None),
        "lease_id": lease.id,
        "member_id": payload.get("member_id"),
        "note": payload.get("note"),
        "source": "manual",
    }, ctx)
    await event_service.confirm_event(db, user, event.id, None, ctx)
    receivable.event_id = event.id

    await audit_service.record_audit(
        db,
        action="lease.receivable_create",
        action_label="登记租金应收",
        object_type="lease_receivable",
        object_id=receivable.id,
        related_object_id=lease.id,
        after={"amount": amount_value, "due_date": receivable.due_date},
        user=user,
        ctx=ctx,
    )
    return receivable


async def create_receipt(
    db: AsyncSession, user: CurrentUser, lease_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    """登记租金实收：生成 rent_received 事件并确认（计入收入），核销应收。"""
    lease = await _get_lease(db, user, lease_id)
    account_id = payload.get("account_id")
    if not account_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "实收必须关联到账账户",
                       field_errors={"account_id": "请选择到账账户"})
    await _check_account(db, user, account_id)

    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
    # 不传金额时按付款周期折算：月租 2200 + 按季 → 6600，而不是只有一个月
    amount = payload.get("amount") or payload.get("monthly_rent") or period_amount(lease)
    try:
        amount_value = Decimal(str(amount))
        if not amount_value.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额格式不正确",
                       field_errors={"amount": "金额必须是数字"})
    if amount_value <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "实收金额必须大于 0",
                       field_errors={"amount": "金额必须大于 0"})

    from app.services import catalog as catalog_service
    from app.services import events as event_service

    # 租金实收必须带分类（归「租金收入」），否则收入分析里会出现「未分类」。
    income_category = await catalog_service.ensure_category_id(
        db, lease.household_id, "income", "租金收入",
    )
    event = await event_service.create_draft(db, user, {
        "event_type": "rent_received",
        "title": f"租金实收 · {lease.tenant_name}",
        "amount": str(amount_value),
        "currency": account.currency if account else lease.currency,
        "occurred_at": payload.get("occurred_at"),
        "account_id": account_id,
        "lease_id": lease.id,
        "member_id": payload.get("member_id"),
        "category_id": income_category,
        "counterparty": lease.tenant_name,
        "note": payload.get("note"),
        "source": "manual",
    }, ctx)
    await event_service.confirm_event(db, user, event.id, None, ctx)

    # 核销应收：显式指定优先；没指定就自动匹配最早一笔待收，避免收完钱应收还挂着
    receivable_id = payload.get("receivable_id")
    receivable: LeaseReceivable | None = None
    if receivable_id:
        receivable = (await db.execute(
            select(LeaseReceivable).where(LeaseReceivable.id == receivable_id))).scalars().first()
        if receivable is None or receivable.household_id != user.household_id \
                or receivable.lease_id != lease.id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "应收记录不存在",
                           field_errors={"receivable_id": "应收记录不存在"})
        if receivable.status == "paid":
            raise AppError(ErrorCode.STATE_INVALID, "该笔应收已核销", status_code=409)
    else:
        pending_rows = list((await db.execute(
            select(LeaseReceivable).where(
                LeaseReceivable.lease_id == lease.id,
                LeaseReceivable.status == "pending",
                LeaseReceivable.deleted_at.is_(None),
            )
        )).scalars().all())
        receivable = _earliest_pending(pending_rows)

    if receivable is not None:
        # 允许多次收租：累加已收金额，够额才置 paid，超出部分不写进应收（属预收/押金性质）
        total = _dec(receivable.amount)
        paid_now = _dec(receivable.paid_amount) + amount_value
        receivable.paid_amount = min(total, paid_now)
        receivable.paid_at = local_now()
        receivable.status = "paid" if receivable.paid_amount >= total else "pending"
        receivable.version += 1

    # 收完这一期，按周期把下一期应收补上（没有待收时才生成，避免重复堆叠）。
    # 租期已结束或一次性租约不生成，免得凭空多出合同外的应收。
    # 会话是 autoflush=False，先把核销结果落下去，否则下面查「还有没有待收」仍会看到刚结清那笔
    await db.flush()
    received_on = to_local_naive(_parse_dt(payload.get("occurred_at"), "occurred_at")) or local_now()
    next_due = next_due_after(
        lease, received_on,
        to_local_naive(receivable.due_date) if receivable is not None and receivable.due_date else None,
    )
    still_pending = (await db.execute(
        select(LeaseReceivable.id).where(
            LeaseReceivable.lease_id == lease.id,
            LeaseReceivable.status == "pending",
            LeaseReceivable.deleted_at.is_(None),
        ).limit(1)
    )).scalars().first()
    lease_end = to_local_naive(lease.end_date) if lease.end_date else None
    if next_due is not None and still_pending is None and (lease_end is None or next_due <= lease_end):
        db.add(LeaseReceivable(
            id=new_id(),
            household_id=user.household_id,
            lease_id=lease.id,
            period_start=next_due,
            period_end=(next_period_date(next_due, lease.payment_frequency) or next_due) - timedelta(days=1),
            due_date=next_due,
            amount=period_amount(lease),
            currency=lease.currency,
            status="pending",
            note="由实收自动生成的下一期",
            version=1,
            created_by=user.user_id,
            updated_by=user.user_id,
        ))

    await audit_service.record_audit(
        db,
        action="lease.receipt_create",
        action_label="登记租金实收",
        object_type="lease",
        object_id=lease.id,
        related_object_id=event.id,
        after={"amount": amount_value, "account_id": account_id},
        user=user,
        ctx=ctx,
    )
    return event


# ---------------- 押金 ----------------

async def receive_deposit(
    db: AsyncSession, user: CurrentUser, lease_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> LeaseDeposit:
    lease = await _get_lease(db, user, lease_id)
    # 押金只能收一次：一份租约的押金是定额，重复登记会让待退还金额翻倍
    existing_deposit = (await db.execute(
        select(LeaseDeposit.id).where(
            LeaseDeposit.lease_id == lease.id,
            LeaseDeposit.deleted_at.is_(None),
        ).limit(1)
    )).scalars().first()
    if existing_deposit is not None:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该租约的押金已经收取过，不能重复收取",
            field_errors={"amount": "押金只能收取一次，金额有变化请修改租约押金"},
            status_code=409,
        )
    account_id = payload.get("account_id")
    if not account_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "收取押金必须关联到账账户",
                       field_errors={"account_id": "请选择到账账户"})
    await _check_account(db, user, account_id)
    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()

    amount = payload.get("amount") or lease.deposit_amount
    try:
        amount_value = Decimal(str(amount))
        if not amount_value.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额格式不正确",
                       field_errors={"amount": "金额必须是数字"})
    if amount_value <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "押金金额必须大于 0",
                       field_errors={"amount": "金额必须大于 0"})

    deposit = LeaseDeposit(
        id=new_id(),
        household_id=user.household_id,
        lease_id=lease.id,
        amount=amount_value,
        currency=account.currency if account else lease.currency,
        status="held",
        received_at=local_now(),
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(deposit)
    await db.flush()
    lease.deposit_received = True
    lease.version += 1
    lease.updated_by = user.user_id

    from app.services import events as event_service

    event = await event_service.create_draft(db, user, {
        "event_type": "deposit_receive",
        "title": f"押金收取 · {lease.tenant_name}",
        "amount": str(amount_value),
        "currency": account.currency if account else lease.currency,
        "occurred_at": payload.get("occurred_at"),
        "account_id": account_id,
        "lease_id": lease.id,
        "member_id": payload.get("member_id"),
        "counterparty": lease.tenant_name,
        "note": payload.get("note"),
        "source": "manual",
    }, ctx)
    await event_service.confirm_event(db, user, event.id, None, ctx)

    await audit_service.record_audit(
        db,
        action="lease.deposit_receive",
        action_label="收取押金",
        object_type="lease_deposit",
        object_id=deposit.id,
        related_object_id=lease.id,
        after={"amount": amount_value, "status": "held"},
        user=user,
        ctx=ctx,
    )
    return deposit


async def return_deposit(
    db: AsyncSession, user: CurrentUser, lease_id: str, deposit_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> LeaseDeposit:
    lease = await _get_lease(db, user, lease_id)
    deposit = (await db.execute(
        select(LeaseDeposit).where(LeaseDeposit.id == deposit_id))).scalars().first()
    if deposit is None or deposit.deleted_at is not None \
            or deposit.household_id != user.household_id or deposit.lease_id != lease.id:
        raise AppError(ErrorCode.NOT_FOUND, "押金记录不存在", status_code=404)
    if deposit.status != "held":
        raise AppError(ErrorCode.STATE_INVALID, "该笔押金已退还", status_code=409)

    account_id = payload.get("account_id")
    if not account_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "退还押金必须关联付款账户",
                       field_errors={"account_id": "请选择付款账户"})
    await _check_account(db, user, account_id)

    from app.services import events as event_service

    event = await event_service.create_draft(db, user, {
        "event_type": "deposit_return",
        "title": f"押金退还 · {lease.tenant_name}",
        "amount": fmt_amount(deposit.amount),
        "currency": deposit.currency,
        "occurred_at": payload.get("occurred_at"),
        "account_id": account_id,
        "lease_id": lease.id,
        "member_id": payload.get("member_id"),
        "counterparty": lease.tenant_name,
        "note": payload.get("note"),
        "source": "manual",
    }, ctx)
    await event_service.confirm_event(db, user, event.id, None, ctx)

    deposit.status = "returned"
    deposit.return_date = local_now()
    deposit.version += 1
    deposit.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="lease.deposit_return",
        action_label="退还押金",
        object_type="lease_deposit",
        object_id=deposit.id,
        related_object_id=lease.id,
        before={"status": "held"},
        after={"status": "returned", "return_date": deposit.return_date},
        user=user,
        ctx=ctx,
    )
    return deposit


# ---------------- 脉枢台统计 ----------------

async def lease_summary(db: AsyncSession, household_id: str, base_currency: str = "CNY") -> dict:
    leases = list((await db.execute(
        select(Lease).where(Lease.household_id == household_id, Lease.deleted_at.is_(None))
    )).scalars().all())
    active = [l for l in leases if l.status == "active"]
    lease_ids = [l.id for l in leases]

    receivables: list[LeaseReceivable] = []
    deposits: list[LeaseDeposit] = []
    if lease_ids:
        receivables = list((await db.execute(
            select(LeaseReceivable).where(LeaseReceivable.lease_id.in_(lease_ids),
                                          LeaseReceivable.deleted_at.is_(None))
        )).scalars().all())
        deposits = list((await db.execute(
            select(LeaseDeposit).where(LeaseDeposit.lease_id.in_(lease_ids),
                                       LeaseDeposit.deleted_at.is_(None))
        )).scalars().all())

    now = datetime.now(timezone.utc)
    pending_total = sum((_dec(r.amount) for r in receivables if r.status == "pending"), Decimal("0"))
    paid_total = sum((_dec(r.paid_amount) for r in receivables if r.status == "paid"), Decimal("0"))
    overdue_total = sum(
        (_dec(r.amount) for r in receivables
         if r.status == "pending" and _aware(r.due_date) is not None and _aware(r.due_date) < now),
        Decimal("0"),
    )
    held_deposits = sum((_dec(d.amount) for d in deposits if d.status == "held"), Decimal("0"))

    denom = pending_total + paid_total
    collection_rate = None
    if denom > 0:
        collection_rate = f"{(paid_total / denom * 100):.1f}"

    return {
        "count": len(leases),
        "active_count": len(active),
        "receivable_pending": fmt_amount(pending_total),
        "receivable_paid": fmt_amount(paid_total),
        "collection_rate": collection_rate,
        "overdue_amount": fmt_amount(overdue_total),
        "overdue_count": sum(
            1 for r in receivables
            if r.status == "pending" and _aware(r.due_date) is not None and _aware(r.due_date) < now),
        "deposit_held": fmt_amount(held_deposits),
        "expiring_count": sum(
            1 for l in active if l.end_date is not None
            and 0 <= (l.end_date.date() - now.date()).days <= 90),
        "caliber": CALIBER,
    }

# ---------------- 续租 ----------------

async def renew_lease(
    db: AsyncSession, user: CurrentUser, lease_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> Lease:
    """续租：基于原租约生成一份新租约，旧租约转为「已到期」。

    押金默认沿用（续租一般不重新收押金），需要调整时在新建后单独改。
    """
    old = await _get_lease(db, user, lease_id)
    if old.status == "terminated":
        raise AppError(ErrorCode.STATE_INVALID, "已终止的租约不能续租", status_code=409)

    start = _parse_dt(payload.get("start_date"), "start_date")
    end = _parse_dt(payload.get("end_date"), "end_date")
    if start is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请填写新租期的起租日",
                       field_errors={"start_date": "请选择起租日"})
    if end is not None and end <= start:
        raise AppError(ErrorCode.VALIDATION_ERROR, "到期日必须晚于起租日",
                       field_errors={"end_date": "到期日必须晚于起租日"})

    field_errors: dict[str, str] = {}
    rent = payload.get("monthly_rent")
    if rent in (None, ""):
        monthly_rent = _dec(old.monthly_rent)
    else:
        try:
            monthly_rent = Decimal(str(rent))
        except Exception:  # noqa: BLE001
            field_errors["monthly_rent"] = "月租格式不正确"
            monthly_rent = _dec(old.monthly_rent)
    deposit = payload.get("deposit_amount")
    if deposit in (None, ""):
        deposit_amount = _dec(old.deposit_amount)
    else:
        try:
            deposit_amount = Decimal(str(deposit))
        except Exception:  # noqa: BLE001
            field_errors["deposit_amount"] = "押金格式不正确"
            deposit_amount = _dec(old.deposit_amount)
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "续租信息校验失败", field_errors=field_errors)

    new = Lease(
        id=new_id(),
        household_id=user.household_id,
        lease_no=_new_lease_no(),
        category_id=old.category_id,
        real_estate_id=old.real_estate_id,
        tenant_name=old.tenant_name,
        tenant_contact=old.tenant_contact,
        lease_purpose=old.lease_purpose,
        monthly_rent=monthly_rent,
        payment_frequency=old.payment_frequency,
        currency=old.currency,
        start_date=start,
        end_date=end,
        deposit_amount=deposit_amount,
        receiving_account_id=old.receiving_account_id,
        property_repair_bearer=old.property_repair_bearer,
        reminder_days=old.reminder_days,
        status="active",
        previous_lease_id=old.id,
        # 押金延续：续租不重新收，沿用原状态
        deposit_received=bool(old.deposit_received),
        deposit_include_in_net_worth=bool(old.deposit_include_in_net_worth),
        note=payload.get("note") or old.note,
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(new)

    # 旧租约：还在生效的转为已到期，避免同一房产出现两份 active
    if old.status == "active":
        old.status = "expired"
        old.version += 1
        old.updated_by = user.user_id

    from app.db.models.assets import RealEstate

    re_row = (await db.execute(
        select(RealEstate).where(RealEstate.asset_id == old.real_estate_id))).scalars().first()
    if re_row is not None:
        re_row.has_lease = True
        re_row.current_lease_id = new.id
        re_row.vacancy_start_date = None
    await db.flush()

    await audit_service.record_audit(
        db, action="lease.renew", action_label="续租",
        object_type="lease", object_id=new.id, related_object_id=old.id,
        before={"lease_id": old.id, "end_date": local_iso(old.end_date) if old.end_date else None},
        after={"lease_id": new.id, "start_date": local_iso(start), "end_date": local_iso(end) if end else None},
        user=user, ctx=ctx,
    )
    return new


# ---------------- 退租结算 ----------------

def serialize_settlement(
    settlement: LeaseSettlement, items: list[LeaseSettlementItem],
) -> dict:
    return {
        "id": settlement.id,
        "lease_id": settlement.lease_id,
        "settle_date": local_iso(settlement.settle_date),
        "deposit_amount": fmt_amount(settlement.deposit_amount),
        "utility_total": fmt_amount(settlement.utility_total),
        "damage_total": fmt_amount(settlement.damage_total),
        "deduction_total": fmt_amount(
            _dec(settlement.utility_total) + _dec(settlement.damage_total)
        ),
        "refund_amount": fmt_amount(settlement.refund_amount),
        "status": settlement.status,
        "refund_account_id": settlement.refund_account_id,
        "note": settlement.note,
        "items": [
            {
                "id": i.id,
                "item_type": i.item_type,
                "item_type_label": "水电煤" if i.item_type == "utility" else "破损赔偿",
                "label": i.label,
                "amount": fmt_amount(i.amount),
                "note": i.note,
            }
            for i in items
        ],
    }


async def list_settlements(
    db: AsyncSession, lease_id: str,
) -> list[tuple[LeaseSettlement, list[LeaseSettlementItem]]]:
    rows = list((await db.execute(
        select(LeaseSettlement).where(
            LeaseSettlement.lease_id == lease_id,
            LeaseSettlement.deleted_at.is_(None),
        ).order_by(LeaseSettlement.settle_date.desc())
    )).scalars().all())
    if not rows:
        return []
    items = list((await db.execute(
        select(LeaseSettlementItem).where(
            LeaseSettlementItem.settlement_id.in_([r.id for r in rows]),
            LeaseSettlementItem.deleted_at.is_(None),
        )
    )).scalars().all())
    grouped: dict[str, list[LeaseSettlementItem]] = {}
    for item in items:
        grouped.setdefault(item.settlement_id, []).append(item)
    return [(row, grouped.get(row.id, [])) for row in rows]


async def settle_lease(
    db: AsyncSession, user: CurrentUser, lease_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> dict:
    """退租结算：押金 − 水电煤欠费 − 破损赔偿 = 应退押金，并终止租约。

    记账口径：
    - 押金不是收入，退还也不进支出；
    - 水电煤与赔偿只是「从押金里扣」，不额外生成支出（真要对外缴款时另记一笔缴费）；
    - 押金已入账（有 held 记录）时按应退金额生成一笔押金退还流水；
      只勾选「已收」但没入账的，只记录结算，不动账户。
    """
    lease = await _get_lease(db, user, lease_id)
    if lease.status == "terminated":
        raise AppError(ErrorCode.STATE_INVALID, "该租约已退租", status_code=409)

    deposits = list((await db.execute(
        select(LeaseDeposit).where(
            LeaseDeposit.lease_id == lease.id,
            LeaseDeposit.deleted_at.is_(None),
        )
    )).scalars().all())
    held = [d for d in deposits if d.status == "held"]
    if held:
        deposit_amount = sum((_dec(d.amount) for d in held), Decimal("0"))
    elif lease.deposit_received:
        deposit_amount = _dec(lease.deposit_amount)
    else:
        deposit_amount = Decimal("0")

    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list):
        raise AppError(ErrorCode.VALIDATION_ERROR, "结算明细格式不正确",
                       field_errors={"items": "明细必须是列表"})

    parsed: list[dict] = []
    field_errors: dict[str, str] = {}
    utility_total = Decimal("0")
    damage_total = Decimal("0")
    for index, raw in enumerate(raw_items):
        item_type = raw.get("item_type") or "damage"
        if item_type not in ("utility", "damage"):
            field_errors[f"items.{index}.item_type"] = "类型只能是水电煤或破损赔偿"
            continue
        label = (raw.get("label") or "").strip()
        if not label:
            field_errors[f"items.{index}.label"] = "请填写项目名称"
            continue
        try:
            amount = Decimal(str(raw.get("amount") or 0))
            if amount < 0 or not amount.is_finite():
                raise ValueError
        except Exception:  # noqa: BLE001
            field_errors[f"items.{index}.amount"] = "金额不正确"
            continue
        if amount == 0:
            continue
        if item_type == "utility":
            utility_total += amount
        else:
            damage_total += amount
        parsed.append({
            "item_type": item_type, "label": label, "amount": amount,
            "note": (raw.get("note") or "").strip() or None,
        })

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "结算明细校验失败", field_errors=field_errors)

    deduction = utility_total + damage_total
    refund = deposit_amount - deduction
    if refund < 0:
        refund = Decimal("0")

    account_id = payload.get("refund_account_id")
    if held and refund > 0:
        if not account_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "退还押金必须选择付款账户",
                           field_errors={"refund_account_id": "请选择付款账户"})
        await _check_account(db, user, account_id)

    settle_date = _parse_dt(payload.get("settle_date"), "settle_date") or local_now()
    settlement = LeaseSettlement(
        id=new_id(),
        household_id=user.household_id,
        lease_id=lease.id,
        settle_date=settle_date,
        deposit_amount=deposit_amount,
        utility_total=utility_total,
        damage_total=damage_total,
        refund_amount=refund,
        status="settled",
        refund_account_id=account_id,
        note=(payload.get("note") or "").strip() or None,
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(settlement)
    await db.flush()

    for row in parsed:
        db.add(LeaseSettlementItem(
            id=new_id(), household_id=user.household_id, settlement_id=settlement.id,
            item_type=row["item_type"], label=row["label"], amount=row["amount"],
            note=row["note"], created_by=user.user_id, updated_by=user.user_id,
        ))

    # 押金已入账：按应退金额生成退还流水；未入账：只改状态
    if held and refund > 0:
        from app.services import events as event_service

        event = await event_service.create_draft(db, user, {
            "event_type": "deposit_return",
            "title": f"退租押金退还 · {lease.tenant_name}",
            "amount": fmt_amount(refund),
            "currency": lease.currency,
            "occurred_at": local_iso(settle_date),
            "account_id": account_id,
            "lease_id": lease.id,
            "counterparty": lease.tenant_name,
            "note": f"退租结算：扣押水电煤 {fmt_amount(utility_total)}、破损赔偿 {fmt_amount(damage_total)}",
            "source": "manual",
        }, ctx)
        await event_service.confirm_event(db, user, event.id, None, ctx)

    for deposit in held:
        deposit.status = "returned"
        deposit.return_date = settle_date
        deposit.version += 1
        deposit.updated_by = user.user_id

    lease.status = "terminated"
    lease.version += 1
    lease.updated_by = user.user_id
    if lease.deposit_received:
        # 押金已处理完，标记为未持有，避免详情页继续显示「待退还」
        lease.deposit_received = False

    from app.db.models.assets import RealEstate

    re_row = (await db.execute(
        select(RealEstate).where(RealEstate.asset_id == lease.real_estate_id))).scalars().first()
    if re_row is not None and re_row.current_lease_id == lease.id:
        re_row.has_lease = False
        re_row.current_lease_id = None
        re_row.vacancy_start_date = settle_date

    await audit_service.record_audit(
        db, action="lease.settle", action_label="退租结算",
        object_type="lease_settlement", object_id=settlement.id, related_object_id=lease.id,
        before={"lease_status": "active", "deposit_amount": fmt_amount(deposit_amount)},
        after={
            "refund_amount": fmt_amount(refund),
            "utility_total": fmt_amount(utility_total),
            "damage_total": fmt_amount(damage_total),
        },
        user=user, ctx=ctx,
    )
    await db.flush()
    item_rows = list((await db.execute(
        select(LeaseSettlementItem).where(LeaseSettlementItem.settlement_id == settlement.id)
    )).scalars().all())
    return serialize_settlement(settlement, item_rows)
