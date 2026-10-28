"""负债与信用卡领域服务（06 §6/§7、15 §3.2/§5.2、18 §4 M3）。

口径（后端确定性计算，前端不自行计算）：
- 剩余本金 = 初始金额 + Σ(已确认负债资金变动 in) − Σ(out)；
  负债方向语义：in = 本金增加（借款到账、信用卡消费/账单形成），out = 本金减少（还款）；
- 还款按「本金 / 利息 / 费用」拆分：系统给出建议利息（剩余本金 × 周期利率），
  用户可调整但必须说明原因，且本金 + 利息 + 费用 = 还款总额；
- 支出只包含利息与费用，本金不重复计入支出（01 §4）；
- 信用卡还款不重复计消费；已用额度 = 剩余本金，可用额度 = 额度 − 已用额度；
- 房贷必须关联具体不动产、车贷必须关联具体车辆，资产页面只显示摘要（15 §3.2、15 §5.2）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.timeutil import to_local_aware, local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.money import fmt_amount
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.logging import mask_value
from app.db.models.business import Account, Asset, BusinessEvent, EventMovement
from app.db.models.finance import (
    CreditCard,
    CreditCardBill,
    Liability,
    LiabilityRepayment,
    Mortgage,
    VehicleLoan,
)
from app.services import audit as audit_service
from app.services import catalog as catalog_service

LIABILITY_EDITABLE = (
    "name", "liability_type", "category_id", "creditor", "currency", "initial_amount",
    "opening_outstanding", "remaining_periods", "interest_rate",
    "rate_type", "rate_adjustment_cycle", "repayment_method", "term_months", "start_date",
    "end_date", "payment_frequency", "payment_day", "monthly_payment", "first_payment_date",
    "next_payment_date", "linked_account_id", "linked_asset_id", "member_id", "note",
    "include_in_net_worth",
)

# 频率 -> 每年期数
_FREQ_PER_YEAR = {
    "monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1, "weekly": 52, "one_time": 1,
}
_FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12}

CALIBER = {
    "outstanding": "剩余本金 = 初始金额 + 已确认负债资金变动（in − out）；草稿、取消、冲销一律不计入",
    "repayment_split": "还款按本金/利息/费用拆分；系统建议利息 = 剩余本金 × 周期利率；调整拆分必须填写原因",
    "expense": "支出只包含利息与费用，还款本金不重复计入支出（01 §4）",
    "credit_card": "已用额度 = 剩余本金；可用额度 = 额度 − 已用额度；信用卡还款不重复计消费",
    "liability_net_worth": "净资产扣除「未结清」负债的剩余本金（仅基础币种）",
    "linkage": "房贷必须关联具体不动产、车贷必须关联具体车辆；余额与利息只在负债模块维护",
}


def base_outstanding(liability: Liability) -> Decimal:
    """剩余本金的起算点：优先用建档时点剩余，否则用贷款金额。"""
    if liability.opening_outstanding is not None:
        return _dec(liability.opening_outstanding)
    return _dec(liability.initial_amount)


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money4(value) -> Decimal:
    return _dec(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


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


def _aware(dt: datetime | None) -> datetime | None:
    """给数据库里的本地墙钟时间补上本地时区，保证与 now 可比。"""
    return to_local_aware(dt)


def periodic_rate(liability: Liability) -> Decimal:
    """周期利率（百分数 → 小数）：年利率按还款频率折算。"""
    rate = _dec(liability.interest_rate)
    if rate == 0:
        return Decimal("0")
    per_year = _FREQ_PER_YEAR.get(liability.payment_frequency or "monthly", 12)
    return rate / Decimal("100") / Decimal(str(per_year))


def _periods_from(principal: Decimal, term_months: int | None, remaining_periods: int | None,
                  payment_frequency: str | None) -> int | None:
    """由本金/期数信息推算「周期数」（每期还款次数）。

    优先用 remaining_periods；否则用 term_months 按还款频率折算。
    """
    if remaining_periods and remaining_periods > 0:
        return remaining_periods
    if term_months and term_months > 0:
        per_year = _FREQ_PER_YEAR.get(payment_frequency or "monthly", 12)
        months_per_period = Decimal("12") / Decimal(str(per_year))
        return int(Decimal(str(term_months)) / months_per_period)
    return None


def compute_monthly_payment(principal: Decimal, *, interest_rate: Decimal | None,
                            repayment_method: str | None, payment_frequency: str | None,
                            term_months: int | None, remaining_periods: int | None) -> Decimal | None:
    """确定性计算「每期应还」（与前端等额本息公式同源，但用 Decimal 避免浮点误差）。

    - equal_installment：等额本息整期固定值
    - equal_principal：等额本金取**首期**（本金均摊 + 首期利息），注意这不是固定月供，
      汇总/现金流预测不应直接把它当每期固定值
    - interest_first：每期只还利息
    - lump_sum：到期一次性还本付息
    - other：无法确定，返回 None（由用户手填）
    无本金或期数不足时返回 None。
    """
    principal = _dec(principal)
    if principal <= 0:
        return None
    if repayment_method not in ("equal_installment", "equal_principal", "interest_first", "lump_sum"):
        return None
    n = _periods_from(principal, term_months, remaining_periods, payment_frequency)
    if not n or n <= 0:
        return None
    rate = _dec(interest_rate)
    per_year = _FREQ_PER_YEAR.get(payment_frequency or "monthly", 12)
    r = rate / Decimal("100") / Decimal(str(per_year))

    if repayment_method == "equal_installment":
        if r == 0:
            payment = principal / Decimal(str(n))
        else:
            factor = (Decimal("1") + r) ** n
            payment = principal * r * factor / (factor - Decimal("1"))
        return _money4(payment)
    if repayment_method == "equal_principal":
        return _money4(principal / Decimal(str(n)) + principal * r)
    if repayment_method == "interest_first":
        return _money4(principal * r)
    # lump_sum：一次性还本付息（整期利息）
    return _money4(principal * (Decimal("1") + r * n))


def suggested_split(liability: Liability, outstanding: Decimal, amount: Decimal,
                    fee: Decimal = Decimal("0")) -> tuple[Decimal, Decimal, Decimal]:
    """系统建议的本金/利息/费用拆分（用户可调整，但需说明原因）。"""
    interest = _money4(outstanding * periodic_rate(liability))
    if interest < 0:
        interest = Decimal("0")
    available = amount - fee
    if available <= 0:
        interest = Decimal("0")
    elif interest > available:
        interest = available
    principal = available - interest
    if principal < 0:
        principal = Decimal("0")
    return _money4(principal), _money4(interest), _money4(fee)


def compute_next_payment(liability: Liability, today: datetime) -> datetime | None:
    """按还款频率与还款日推算下次还款日（不做节假日顺延）。"""
    freq = liability.payment_frequency or "monthly"
    if freq == "one_time":
        anchor = _aware(liability.next_payment_date or liability.end_date
                        or liability.first_payment_date or liability.start_date)
        return anchor if anchor and anchor.date() >= today.date() else None

    anchor = _aware(
        liability.next_payment_date or liability.first_payment_date or liability.start_date
    )
    if anchor is None:
        # 只填了「还款日」的存量贷款：按本月/下月的还款日推一个下次还款日，
        # 否则还款记录登记后无从顺延（详情里会一直显示「—」）。
        day = liability.payment_day
        if not day:
            return None
        base = today.date() if hasattr(today, "date") else today
        year, month = base.year, base.month
        candidate = datetime(year, month, min(day, _days_in_month(year, month)))
        if candidate.date() < base:
            month_next = month + _FREQ_MONTHS.get(freq, 1)
            year += (month_next - 1) // 12
            month = (month_next - 1) % 12 + 1
            candidate = datetime(year, month, min(day, _days_in_month(year, month)))
        return _aware(candidate)
    if anchor.date() >= today.date():
        return anchor

    if freq == "weekly":
        step = timedelta(days=7)
        cursor = anchor
        guard = 0
        while cursor.date() < today.date() and guard < 520:
            cursor += step
            guard += 1
        return cursor

    months = _FREQ_MONTHS.get(freq, 1)
    payment_day = liability.payment_day
    cursor = anchor
    guard = 0
    while cursor.date() < today.date() and guard < 480:
        year = cursor.year
        month = cursor.month + months
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(payment_day or cursor.day, _days_in_month(year, month))
        cursor = cursor.replace(year=year, month=month, day=day)
        guard += 1
    return cursor


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (datetime(year, month + 1, 1) - timedelta(days=1)).day


def effective_status(liability: Liability, outstanding: Decimal, today: datetime) -> str:
    if outstanding <= 0:
        return "settled"
    nxt = compute_next_payment(liability, today)
    if nxt is not None and nxt.date() < today.date():
        return "overdue"
    due = _aware(liability.next_payment_date)
    if due is not None and due.date() < today.date() and outstanding > 0:
        return "overdue"
    return liability.status or "active"


def serialize_liability(
    liability: Liability,
    outstanding: Decimal | None = None,
    *,
    extension: dict | None = None,
    today: datetime | None = None,
    card: dict | None = None,
    extra: dict | None = None,
    category_label: str | None = None,
) -> dict:
    now = today or local_now()
    out = base_outstanding(liability) if outstanding is None else outstanding
    status = effective_status(liability, out, now)
    data = {
        "id": liability.id,
        "name": liability.name,
        "liability_type": liability.liability_type,
        "liability_type_label": enum_zh.zh("liability_type", liability.liability_type),
        "category_id": liability.category_id,
        "category_label": category_label or "",
        "creditor": liability.creditor,
        "currency": liability.currency,
        "initial_amount": money(liability.initial_amount),
        "opening_outstanding": (
            money(liability.opening_outstanding) if liability.opening_outstanding is not None else None
        ),
        "remaining_periods": liability.remaining_periods,
        "term_periods": _term_periods(liability),
        "outstanding": money(out),
        # 负债是净资产的负项：关闭后该笔欠款不计入家庭净值（如公司贷款）
        "include_in_net_worth": bool(liability.include_in_net_worth),
        "interest_rate": f"{_dec(liability.interest_rate):.4f}" if liability.interest_rate is not None else None,
        "rate_type": liability.rate_type,
        "rate_type_label": enum_zh.zh("rate_type", liability.rate_type) if liability.rate_type else "",
        "rate_adjustment_cycle": liability.rate_adjustment_cycle,
        "repayment_method": liability.repayment_method,
        "repayment_method_label": (
            enum_zh.zh("repayment_method", liability.repayment_method)
            if liability.repayment_method else ""
        ),
        "term_months": liability.term_months,
        "start_date": local_iso(liability.start_date) if liability.start_date else None,
        "end_date": local_iso(liability.end_date) if liability.end_date else None,
        "payment_frequency": liability.payment_frequency,
        "payment_frequency_label": enum_zh.zh("payment_frequency", liability.payment_frequency),
        "payment_day": liability.payment_day,
        "monthly_payment": money(liability.monthly_payment) if liability.monthly_payment is not None else None,
        "first_payment_date": local_iso(liability.first_payment_date) if liability.first_payment_date else None,
        "next_payment_date": (
            local_iso(compute_next_payment(liability, now)) if out > 0 else None
        ),
        "linked_account_id": liability.linked_account_id,
        "linked_asset_id": liability.linked_asset_id,
        "member_id": liability.member_id,
        "status": status,
        "status_label": enum_zh.zh("liability_status", status),
        "settled": out <= 0,
        "note": liability.note,
        "version": liability.version,
        "created_at": utc_iso(liability.created_at),
        "updated_at": utc_iso(liability.updated_at),
        "caliber": {"outstanding": CALIBER["outstanding"]},
    }
    if extension:
        data["extension"] = extension
    if card:
        data.update(card)
    if extra:
        data.update(extra)
    return data


async def outstanding_for(
    db: AsyncSession, household_id: str, liability_ids: list[str] | None = None
) -> dict[str, Decimal]:
    """批量计算负债剩余本金：初始金额 + 已确认资金变动净额。"""
    stmt = (
        select(
            EventMovement.target_id,
            EventMovement.direction,
            func.coalesce(func.sum(EventMovement.amount), 0),
        )
        .join(BusinessEvent, BusinessEvent.id == EventMovement.event_id)
        .where(
            EventMovement.household_id == household_id,
            EventMovement.target_type == "liability",
            EventMovement.target_id.is_not(None),
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
        )
        .group_by(EventMovement.target_id, EventMovement.direction)
    )
    if liability_ids:
        stmt = stmt.where(EventMovement.target_id.in_(liability_ids))
    rows = (await db.execute(stmt)).all()
    net: dict[str, Decimal] = {}
    for target_id, direction, total in rows:
        amount = _dec(total)
        net[target_id] = net.get(target_id, Decimal("0")) + (amount if direction == "in" else -amount)

    conditions = [Liability.household_id == household_id, Liability.deleted_at.is_(None)]
    if liability_ids:
        conditions.append(Liability.id.in_(liability_ids))
    liabilities = list((await db.execute(select(Liability).where(*conditions))).scalars().all())

    result: dict[str, Decimal] = {}
    for item in liabilities:
        result[item.id] = _money4(base_outstanding(item) + net.get(item.id, Decimal("0")))
    return result


async def get_liability(
    db: AsyncSession, user: CurrentUser, liability_id: str, *, for_update: bool = False,
) -> Liability:
    stmt = select(Liability).where(Liability.id == liability_id)
    if for_update:
        # 登记还款要「先读余额再扣减」，加行锁避免并发两笔都通过校验
        stmt = stmt.with_for_update()
    liability = (await db.execute(stmt)).scalars().first()
    if liability is None or liability.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "负债不存在", status_code=404)
    if liability.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return liability


async def list_liabilities(
    db: AsyncSession, user: CurrentUser, *,
    keyword: str | None = None,
    liability_type: str | None = None,
    category_id: str | None = None,
    status: str | None = None,
    member_id: str | None = None,
) -> list[tuple[Liability, Decimal]]:
    conditions = [Liability.household_id == user.household_id, Liability.deleted_at.is_(None)]
    if keyword:
        conditions.append(Liability.name.ilike(f"%{keyword}%"))
    if liability_type:
        conditions.append(Liability.liability_type == liability_type)
    if category_id:
        conditions.append(Liability.category_id == category_id)
    if member_id:
        conditions.append(Liability.member_id == member_id)
    rows = list(
        (await db.execute(select(Liability).where(*conditions).order_by(Liability.created_at.asc())))
        .scalars().all()
    )
    outstanding = await outstanding_for(db, user.household_id or "", [r.id for r in rows])
    now = local_now()
    if status:
        rows = [
            r for r in rows
            if effective_status(r, outstanding.get(r.id, _dec(r.initial_amount)), now) == status
        ]
    return [(r, outstanding.get(r.id, base_outstanding(r))) for r in rows]


async def liability_summary(db: AsyncSession, household_id: str, base_currency: str = "CNY") -> dict:
    """负债汇总（06 §6 列表 + 07 §5 负债分析）：剩余本金、月度应还、到期与利息费用。"""
    liabilities = list(
        (await db.execute(
            select(Liability).where(
                Liability.household_id == household_id, Liability.deleted_at.is_(None)
            )
        )).scalars().all()
    )
    outstanding = await outstanding_for(db, household_id, [l.id for l in liabilities])
    now = local_now()

    buckets: dict[str, dict] = {}
    by_type: dict[str, Decimal] = {}
    monthly_due = Decimal("0")
    monthly_due_count = 0   # 有月供金额的负债笔数：为 0 时前端显示「—」而不是 ¥0.00
    overdue_count = 0
    settled_count = 0
    upcoming: list[dict] = []

    for liability in liabilities:
        out = outstanding.get(liability.id, Decimal("0"))
        status = effective_status(liability, out, now)
        if status == "settled" or out <= 0:
            settled_count += 1
            continue
        if status == "overdue":
            overdue_count += 1
        bucket = buckets.setdefault(liability.currency, {"total": Decimal("0"), "count": 0})
        bucket["total"] += out
        bucket["count"] += 1
        by_type[liability.liability_type] = by_type.get(liability.liability_type, Decimal("0")) + out
        if liability.currency == base_currency and liability.monthly_payment is not None:
            monthly_due += _dec(liability.monthly_payment)
            monthly_due_count += 1
        nxt = compute_next_payment(liability, now)
        if nxt is not None:
            upcoming.append({
                "liability_id": liability.id,
                "name": liability.name,
                "liability_type": liability.liability_type,
                "liability_type_label": enum_zh.zh("liability_type", liability.liability_type),
                "next_payment_date": local_iso(nxt),
                "monthly_payment": money(liability.monthly_payment) if liability.monthly_payment is not None else None,
                "currency": liability.currency,
                "outstanding": money(out),
                "status": status,
                "status_label": enum_zh.zh("liability_status", status),
            })

    base_total = _dec(buckets.get(base_currency, {}).get("total", 0))
    return {
        "base_currency": base_currency,
        "count": len(liabilities),
        "settled_count": settled_count,
        "overdue_count": overdue_count,
        "total_outstanding": money(base_total),
        "monthly_due": money(monthly_due),
        "monthly_due_count": monthly_due_count,
        "by_currency": [
            {"currency": cur, "total": money(b["total"]), "count": b["count"]}
            for cur, b in buckets.items()
        ],
        "by_type": [
            {
                "liability_type": key,
                "liability_type_label": enum_zh.zh("liability_type", key),
                "outstanding": money(value),
                "currency": base_currency,
            }
            for key, value in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "upcoming": sorted(upcoming, key=lambda x: x["next_payment_date"])[:10],
        "multi_currency": len(buckets) > 1,
        "caliber": CALIBER,
    }


def _validate_payload(payload: dict, *, partial: bool) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}

    if "name" in payload or not partial:
        name = (payload.get("name") or "").strip()
        if not name:
            field_errors["name"] = "请填写债务名称"
        else:
            data["name"] = name

    if "liability_type" in payload or not partial:
        liability_type = payload.get("liability_type")
        if liability_type not in enum_zh.LIABILITY_TYPE:
            field_errors["liability_type"] = "债务类型不合法"
        else:
            data["liability_type"] = liability_type

    if "currency" in payload:
        currency = (payload.get("currency") or "CNY").upper()
        if not (2 <= len(currency) <= 8):
            field_errors["currency"] = "币种代码不合法"
        else:
            data["currency"] = currency

    for money_field in ("initial_amount", "opening_outstanding", "interest_rate", "monthly_payment"):
        if money_field in payload and payload[money_field] not in (None, ""):
            try:
                value = Decimal(str(payload[money_field]))
                if not value.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors[money_field] = "数值格式不正确"
            else:
                if value < 0:
                    field_errors[money_field] = "数值不能为负数"
                else:
                    data[money_field] = value

    if "term_months" in payload and payload["term_months"] not in (None, ""):
        try:
            term = int(payload["term_months"])
        except (TypeError, ValueError):
            field_errors["term_months"] = "贷款年限必须是整数月"
        else:
            if term < 0:
                field_errors["term_months"] = "贷款年限不能为负数"
            else:
                data["term_months"] = term

    if "remaining_periods" in payload and payload["remaining_periods"] not in (None, ""):
        try:
            remaining = int(payload["remaining_periods"])
        except (TypeError, ValueError):
            field_errors["remaining_periods"] = "剩余期数必须是整数"
        else:
            if remaining < 0:
                field_errors["remaining_periods"] = "剩余期数不能为负数"
            else:
                data["remaining_periods"] = remaining

    if "payment_day" in payload and payload["payment_day"] not in (None, ""):
        try:
            day = int(payload["payment_day"])
        except (TypeError, ValueError):
            field_errors["payment_day"] = "还款日必须是 1-31 的整数"
        else:
            if not 1 <= day <= 31:
                field_errors["payment_day"] = "还款日必须是 1-31 的整数"
            else:
                data["payment_day"] = day

    for date_field in ("start_date", "end_date", "first_payment_date", "next_payment_date"):
        if date_field in payload:
            data[date_field] = _parse_dt(payload.get(date_field), date_field)

    for enum_field, meta in (
        ("rate_type", enum_zh.RATE_TYPE),
        ("repayment_method", enum_zh.REPAYMENT_METHOD),
        ("payment_frequency", enum_zh.PAYMENT_FREQUENCY),
    ):
        if enum_field in payload and payload[enum_field] not in (None, ""):
            if payload[enum_field] not in meta:
                field_errors[enum_field] = "取值不合法"
            else:
                data[enum_field] = payload[enum_field]

    for plain in ("creditor", "category_id", "rate_adjustment_cycle", "linked_account_id",
                  "linked_asset_id", "member_id", "note"):
        if plain in payload:
            data[plain] = payload[plain] or None

    # 是否计入净资产（负债是负项）
    if "include_in_net_worth" in payload:
        data["include_in_net_worth"] = bool(payload["include_in_net_worth"])

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "负债信息校验失败", field_errors=field_errors)
    return data


async def _check_linked_asset(db: AsyncSession, user: CurrentUser, liability_type: str,
                              asset_id: str | None) -> Asset | None:
    """房贷必须关联不动产、车贷必须关联车辆（15 §3.2、15 §5.2）。"""
    if liability_type in ("mortgage", "vehicle_loan"):
        if not asset_id:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "房贷必须关联具体不动产、车贷必须关联具体车辆",
                field_errors={"linked_asset_id": "请选择关联资产"},
            )
    if not asset_id:
        return None
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalars().first()
    if asset is None or asset.deleted_at is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "关联资产不存在",
                       field_errors={"linked_asset_id": "关联资产不存在"})
    if asset.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    if liability_type == "mortgage" and asset.asset_type != "real_estate":
        raise AppError(ErrorCode.VALIDATION_ERROR, "房贷只能关联不动产类型的资产",
                       field_errors={"linked_asset_id": "该资产不是不动产"})
    if liability_type == "vehicle_loan" and asset.asset_type != "vehicle":
        raise AppError(ErrorCode.VALIDATION_ERROR, "车贷只能关联车辆类型的资产",
                       field_errors={"linked_asset_id": "该资产不是车辆"})
    return asset


async def _validate_credit_card_payload(db: AsyncSession, user: CurrentUser,
                                        payload: dict, *, partial: bool) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}
    if "credit_limit" in payload or not partial:
        try:
            limit = Decimal(str(payload.get("credit_limit") or "0"))
            if not limit.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors["extension.credit_limit"] = "额度格式不正确"
        else:
            if limit <= 0:
                field_errors["extension.credit_limit"] = "信用卡额度必须大于 0"
            else:
                data["credit_limit"] = limit
    for money_field in ("credit_limit",):
        pass
    for day_field in ("bill_day", "due_day"):
        if day_field in payload and payload[day_field] not in (None, ""):
            try:
                day = int(payload[day_field])
            except (TypeError, ValueError):
                field_errors[f"extension.{day_field}"] = "必须是 1-31 的整数"
            else:
                if not 1 <= day <= 31:
                    field_errors[f"extension.{day_field}"] = "必须是 1-31 的整数"
                else:
                    data[day_field] = day
    for plain in ("issuer", "card_no", "linked_account_id", "note"):
        if plain in payload:
            data[plain] = payload[plain]
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "信用卡信息校验失败", field_errors=field_errors)
    return data


async def liability_extension(db: AsyncSession, liability: Liability) -> dict | None:
    if liability.liability_type == "mortgage":
        row = (await db.execute(
            select(Mortgage).where(Mortgage.liability_id == liability.id)
        )).scalars().first()
        if row is None:
            return None
        return {
            "contract_no": row.contract_no,
            "loan_method": row.loan_method,
            "loan_method_label": enum_zh.zh("loan_method", row.loan_method),
            "rate_adjustment_cycle": row.rate_adjustment_cycle,
            "prepayment_rules": row.prepayment_rules,
            "note": row.note,
        }
    if liability.liability_type == "vehicle_loan":
        row = (await db.execute(
            select(VehicleLoan).where(VehicleLoan.liability_id == liability.id)
        )).scalars().first()
        if row is None:
            return None
        return {
            "contract_no": row.contract_no,
            "loan_method": row.loan_method,
            "loan_method_label": enum_zh.zh("loan_method", row.loan_method),
            "note": row.note,
        }
    return None


async def card_info(db: AsyncSession, liability: Liability, outstanding: Decimal,
                    *, reveal: bool = False) -> dict | None:
    """信用卡概要：额度、已用、可用、本期账单、最低还款、已还金额（06 §7）。"""
    if liability.liability_type != "credit_card":
        return None
    card = (await db.execute(
        select(CreditCard).where(CreditCard.liability_id == liability.id)
    )).scalars().first()
    if card is None:
        return None
    limit = _dec(card.credit_limit)
    used = outstanding
    bills = list((await db.execute(
        select(CreditCardBill)
        .where(CreditCardBill.card_id == card.id, CreditCardBill.deleted_at.is_(None))
        .order_by(CreditCardBill.bill_date.desc(), CreditCardBill.created_at.desc())
    )).scalars().all())
    now = local_now()
    current = next((b for b in bills if bill_status(b, now) != "paid"), None) or (bills[0] if bills else None)
    return {
        "card": {
            "id": card.id,
            "issuer": card.issuer,
            "card_no": card.card_no if reveal else (mask_value(card.card_no) if card.card_no else None),
            "credit_limit": money(limit),
            "bill_day": card.bill_day,
            "due_day": card.due_day,
            "linked_account_id": card.linked_account_id,
            "used_amount": money(used),
            "available_amount": money(limit - used),
            "note": card.note,
        },
        "current_bill": serialize_bill(current, now) if current else None,
        "bills": [serialize_bill(b, now) for b in bills],
        "bill_count": len(bills),
    }


def bill_status(bill: CreditCardBill, today: datetime) -> str:
    total = _dec(bill.total_amount)
    paid = _dec(bill.paid_amount)
    if total > 0 and paid >= total:
        return "paid"
    due = _aware(bill.due_date)
    if due is not None and due.date() < today.date():
        return "overdue"
    if paid > 0:
        return "partial"
    if total <= 0:
        return "paid"
    return "pending"


def serialize_bill(bill: CreditCardBill, today: datetime | None = None) -> dict:
    now = today or local_now()
    total = _dec(bill.total_amount)
    paid = _dec(bill.paid_amount)
    status = bill_status(bill, now)
    return {
        "id": bill.id,
        "card_id": bill.card_id,
        "period_start": local_iso(bill.period_start) if bill.period_start else None,
        "period_end": local_iso(bill.period_end) if bill.period_end else None,
        "bill_date": local_iso(bill.bill_date) if bill.bill_date else None,
        "due_date": local_iso(bill.due_date) if bill.due_date else None,
        "total_amount": money(total),
        "minimum_payment": money(bill.minimum_payment) if bill.minimum_payment is not None else None,
        "paid_amount": money(paid),
        "remaining_amount": money(total - paid),
        "currency": bill.currency,
        "status": status,
        "status_label": enum_zh.zh("bill_status", status),
        "note": bill.note,
        "created_at": utc_iso(bill.created_at),
    }


async def _sync_asset_link(
    db: AsyncSession, liability: Liability, asset_id: str | None,
    *, previous_asset_id: str | None = None,
) -> None:
    """维护「负债 ↔ 资产」双向引用。

    房贷/车贷只在负债侧存 linked_asset_id，资产侧需要 linked_liability_id 才能
    在资产详情里显示关联负债；只写单边会出现「详情说未关联、下面却有贷款信息」。
    """
    from app.db.models.assets import RealEstate, Vehicle

    async def _load(target_id: str | None):
        if not target_id:
            return None
        return (await db.execute(select(Asset).where(Asset.id == target_id))).scalars().first()

    # 解绑：把旧资产指向本负债的反向引用清掉
    if previous_asset_id and previous_asset_id != asset_id:
        old_asset = await _load(previous_asset_id)
        if old_asset is not None and old_asset.linked_liability_id == liability.id:
            old_asset.linked_liability_id = None

    asset = await _load(asset_id)
    if asset is None:
        return
    if asset.linked_liability_id in (None, "", liability.id):
        asset.linked_liability_id = liability.id
        asset.updated_by = liability.updated_by
    # 关联了贷款就把资产上的「是否有房贷/车贷」标记补上，避免详情自相矛盾
    if liability.liability_type == "mortgage":
        row = (await db.execute(
            select(RealEstate).where(RealEstate.asset_id == asset.id)
        )).scalars().first()
        if row is not None and not row.has_mortgage:
            row.has_mortgage = True
    elif liability.liability_type == "vehicle_loan":
        row = (await db.execute(
            select(Vehicle).where(Vehicle.asset_id == asset.id)
        )).scalars().first()
        if row is not None and not row.has_loan:
            row.has_loan = True


async def create_liability(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None
) -> Liability:
    data = _validate_payload(payload, partial=False)
    liability_type = data["liability_type"]
    await _check_linked_asset(db, user, liability_type, data.get("linked_asset_id"))

    card_data: dict | None = None
    if liability_type == "credit_card":
        card_payload = payload.get("extension") or {}
        card_data = await _validate_credit_card_payload(db, user, card_payload, partial=False)

    # 「每期应还」由后端确定性计算（03 §3 金额口径），前端不再自行计算写库。
    # 用户显式给了 monthly_payment 就采信；否则按本金/利率/期数/方式算，算不出才留空。
    monthly_payment = data.get("monthly_payment")
    if monthly_payment is None and liability_type != "credit_card":
        monthly_payment = compute_monthly_payment(
            data.get("opening_outstanding") if data.get("opening_outstanding") is not None
            else data.get("initial_amount", Decimal("0")),
            interest_rate=data.get("interest_rate"),
            repayment_method=data.get("repayment_method"),
            payment_frequency=data.get("payment_frequency", "monthly"),
            term_months=data.get("term_months"),
            remaining_periods=data.get("remaining_periods"),
        )

    # 业务时间按本地墙钟存放（app/core/timeutil.py）：用 UTC 会让「今天到期」在凌晨算成昨天，
    # 导致下次还款日直接跳到下个月。
    now = local_now()
    liability = Liability(
        id=new_id(),
        household_id=user.household_id or "",
        name=data["name"],
        liability_type=liability_type,
        category_id=data.get("category_id"),
        creditor=data.get("creditor"),
        currency=data.get("currency", "CNY"),
        initial_amount=data.get("initial_amount", Decimal("0")),
        opening_outstanding=data.get("opening_outstanding"),
        remaining_periods=data.get("remaining_periods"),
        interest_rate=data.get("interest_rate"),
        rate_type=data.get("rate_type"),
        rate_adjustment_cycle=data.get("rate_adjustment_cycle"),
        repayment_method=data.get("repayment_method"),
        term_months=data.get("term_months"),
        start_date=data.get("start_date") or now,
        end_date=data.get("end_date"),
        payment_frequency=data.get("payment_frequency", "monthly"),
        payment_day=data.get("payment_day"),
        monthly_payment=monthly_payment,
        first_payment_date=data.get("first_payment_date"),
        next_payment_date=data.get("next_payment_date"),
        linked_account_id=data.get("linked_account_id"),
        linked_asset_id=data.get("linked_asset_id"),
        member_id=data.get("member_id"),
        status="active",
        note=data.get("note"),
        include_in_net_worth=bool(data.get("include_in_net_worth", True)),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(liability)
    await db.flush()

    if liability_type == "mortgage":
        ext = payload.get("extension") or {}
        db.add(Mortgage(
            id=new_id(), household_id=liability.household_id, liability_id=liability.id,
            contract_no=ext.get("contract_no"),
            loan_method=ext.get("loan_method"),
            rate_adjustment_cycle=ext.get("rate_adjustment_cycle") or liability.rate_adjustment_cycle,
            prepayment_rules=ext.get("prepayment_rules"),
            note=ext.get("note"),
            created_by=user.user_id, updated_by=user.user_id,
        ))
    elif liability_type == "vehicle_loan":
        ext = payload.get("extension") or {}
        db.add(VehicleLoan(
            id=new_id(), household_id=liability.household_id, liability_id=liability.id,
            contract_no=ext.get("contract_no"),
            loan_method=ext.get("loan_method"),
            note=ext.get("note"),
            created_by=user.user_id, updated_by=user.user_id,
        ))
    elif liability_type == "credit_card":
        db.add(CreditCard(
            id=new_id(), household_id=liability.household_id, liability_id=liability.id,
            issuer=(card_data or {}).get("issuer") or data.get("creditor"),
            card_no=(card_data or {}).get("card_no"),
            credit_limit=(card_data or {}).get("credit_limit", Decimal("0")),
            bill_day=(card_data or {}).get("bill_day"),
            due_day=(card_data or {}).get("due_day"),
            linked_account_id=(card_data or {}).get("linked_account_id") or data.get("linked_account_id"),
            note=(card_data or {}).get("note"),
            created_by=user.user_id, updated_by=user.user_id,
        ))

    if liability.linked_asset_id:
        await _sync_asset_link(db, liability, liability.linked_asset_id)

    await audit_service.record_audit(
        db,
        action="liability.create",
        action_label="新增负债",
        object_type="liability",
        object_id=liability.id,
        related_object_id=liability.linked_asset_id,
        after={
            "name": liability.name, "liability_type": liability.liability_type,
            "currency": liability.currency, "initial_amount": liability.initial_amount,
            "interest_rate": liability.interest_rate,
        },
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return liability


async def update_liability(
    db: AsyncSession, user: CurrentUser, liability_id: str, payload: dict,
    expected_version: int | None = None, ctx: RequestContext | None = None,
) -> Liability:
    liability = await get_liability(db, user, liability_id)
    if expected_version is not None and liability.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)

    data = _validate_payload(payload, partial=True)
    new_type = data.get("liability_type", liability.liability_type)
    linked_asset_id = data.get("linked_asset_id", liability.linked_asset_id)

    # 已发生还款的负债不允许直接改类型或改初始金额（会造成余额口径错乱）
    repayment_count = int((await db.execute(
        select(func.count(LiabilityRepayment.id)).where(LiabilityRepayment.liability_id == liability.id)
    )).scalar_one())
    if new_type != liability.liability_type and repayment_count > 0:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该负债已有还款记录，不能直接修改债务类型；请通过更正流程处理",
            field_errors={"liability_type": "已有还款记录，不可切换类型"},
            status_code=409,
        )
    if "initial_amount" in data and _dec(data["initial_amount"]) != _dec(liability.initial_amount) \
            and repayment_count > 0:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该负债已有还款记录，不能直接修改初始金额；请登记调整事件",
            field_errors={"initial_amount": "已有还款记录，不可直接修改初始金额"},
            status_code=409,
        )

    if "opening_outstanding" in data and repayment_count > 0 \
            and _dec(data["opening_outstanding"]) != _dec(liability.opening_outstanding):
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该负债已有还款记录，不能直接改建档剩余本金；请登记调整事件",
            field_errors={"opening_outstanding": "已有还款记录，不可直接修改剩余金额"},
            status_code=409,
        )

    await _check_linked_asset(db, user, new_type, linked_asset_id)

    before = {f: getattr(liability, f) for f in LIABILITY_EDITABLE}
    previous_asset_id = liability.linked_asset_id

    # 「每期应还」口径：用户改了利率/期数/方式/频率却没手填月供时，后端重算回填，
    # 避免前端浮点计算的分位误差污染还款计划与月供汇总（B-6）。
    if liability.liability_type != "credit_card" and "monthly_payment" not in data:
        calc_inputs = ("opening_outstanding", "initial_amount", "interest_rate",
                       "repayment_method", "payment_frequency", "term_months", "remaining_periods")
        if any(f in data for f in calc_inputs):
            recomputed = compute_monthly_payment(
                data.get("opening_outstanding", liability.opening_outstanding)
                if data.get("opening_outstanding", liability.opening_outstanding) is not None
                else data.get("initial_amount", liability.initial_amount),
                interest_rate=data.get("interest_rate", liability.interest_rate),
                repayment_method=data.get("repayment_method", liability.repayment_method),
                payment_frequency=data.get("payment_frequency", liability.payment_frequency or "monthly"),
                term_months=data.get("term_months", liability.term_months),
                remaining_periods=data.get("remaining_periods", liability.remaining_periods),
            )
            if recomputed is not None:
                liability.monthly_payment = recomputed

    for field, value in data.items():
        setattr(liability, field, value)
    liability.version += 1
    liability.updated_by = user.user_id

    # 关联资产变了要同步资产侧的 linked_liability_id（含解绑旧资产）
    if "linked_asset_id" in data:
        await _sync_asset_link(
            db, liability, liability.linked_asset_id, previous_asset_id=previous_asset_id,
        )

    # 类型扩展
    ext = payload.get("extension")
    if ext:
        if new_type == "mortgage":
            row = (await db.execute(select(Mortgage).where(Mortgage.liability_id == liability.id))).scalars().first()
            if row is None:
                db.add(Mortgage(id=new_id(), household_id=liability.household_id,
                                liability_id=liability.id, **{k: ext.get(k) for k in
                                                              ("contract_no", "loan_method", "rate_adjustment_cycle",
                                                               "prepayment_rules", "note")}))
            else:
                for field in ("contract_no", "loan_method", "rate_adjustment_cycle", "prepayment_rules", "note"):
                    if field in ext:
                        setattr(row, field, ext[field])
        elif new_type == "vehicle_loan":
            row = (await db.execute(select(VehicleLoan).where(VehicleLoan.liability_id == liability.id))).scalars().first()
            if row is None:
                db.add(VehicleLoan(id=new_id(), household_id=liability.household_id,
                                   liability_id=liability.id,
                                   contract_no=ext.get("contract_no"),
                                   loan_method=ext.get("loan_method"), note=ext.get("note")))
            else:
                for field in ("contract_no", "loan_method", "note"):
                    if field in ext:
                        setattr(row, field, ext[field])
        elif new_type == "credit_card":
            card_data = await _validate_credit_card_payload(db, user, ext, partial=True)
            row = (await db.execute(select(CreditCard).where(CreditCard.liability_id == liability.id))).scalars().first()
            if row is None:
                db.add(CreditCard(id=new_id(), household_id=liability.household_id,
                                  liability_id=liability.id,
                                  credit_limit=card_data.get("credit_limit", Decimal("0")),
                                  issuer=card_data.get("issuer"), card_no=card_data.get("card_no"),
                                  bill_day=card_data.get("bill_day"), due_day=card_data.get("due_day"),
                                  linked_account_id=card_data.get("linked_account_id"),
                                  note=card_data.get("note")))
            else:
                for field, value in card_data.items():
                    setattr(row, field, value)

    after = {f: getattr(liability, f) for f in LIABILITY_EDITABLE}
    await audit_service.record_audit(
        db,
        action="liability.update",
        action_label="编辑负债",
        object_type="liability",
        object_id=liability.id,
        before=before,
        after=after,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return liability


async def delete_liability(
    db: AsyncSession, user: CurrentUser, liability_id: str, reason: str,
    ctx: RequestContext | None = None,
) -> Liability:
    """删除负债为软删除（历史保留）；已有还款记录的负债不允许删除，只能结清。"""
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除负债必须填写原因",
                       field_errors={"reason": "请填写删除原因"})
    liability = await get_liability(db, user, liability_id)
    outstanding = (await outstanding_for(db, user.household_id or "", [liability.id])).get(liability.id)
    if outstanding is not None and outstanding > 0:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该负债仍有剩余本金，不能删除；请先登记还款或改为结清",
            status_code=409,
        )
    liability.deleted_at = datetime.now(timezone.utc)
    liability.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="liability.delete",
        action_label="删除负债",
        object_type="liability",
        object_id=liability.id,
        before={"name": liability.name, "liability_type": liability.liability_type},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return liability


async def list_repayments(
    db: AsyncSession, user: CurrentUser, liability_id: str
) -> list[LiabilityRepayment]:
    await get_liability(db, user, liability_id)
    return list((await db.execute(
        select(LiabilityRepayment)
        .where(LiabilityRepayment.liability_id == liability_id,
               LiabilityRepayment.deleted_at.is_(None))
        .order_by(LiabilityRepayment.occurred_at.desc(), LiabilityRepayment.created_at.desc())
    )).scalars().all())


async def get_repayment(
    db: AsyncSession, user: CurrentUser, repayment_id: str
) -> LiabilityRepayment:
    """按还款单 id 取记录（供幂等重放返回原结果）。"""
    row = (await db.execute(
        select(LiabilityRepayment).where(
            LiabilityRepayment.id == repayment_id,
            LiabilityRepayment.deleted_at.is_(None),
        )
    )).scalars().first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "还款记录不存在", status_code=404)
    await get_liability(db, user, row.liability_id)   # 家庭归属校验
    return row


def serialize_repayment(row: LiabilityRepayment) -> dict:
    return {
        "id": row.id,
        "liability_id": row.liability_id,
        "amount": money(row.amount),
        "principal": money(row.principal),
        "interest": money(row.interest),
        "fee": money(row.fee),
        "currency": row.currency,
        "occurred_at": local_iso(row.occurred_at) if row.occurred_at else None,
        "account_id": row.account_id,
        "bill_id": row.bill_id,
        "member_id": row.member_id,
        "reason": row.reason,
        "note": row.note,
        "principal_event_id": row.principal_event_id,
        "interest_event_id": row.interest_event_id,
        "created_at": utc_iso(row.created_at),
    }


async def repayment_plan(db: AsyncSession, user: CurrentUser, liability_id: str) -> dict:
    """还款计划（06 §6 详情页签）：按还款方式测算剩余期数。"""
    liability = await get_liability(db, user, liability_id)
    outstanding = (await outstanding_for(db, user.household_id or "", [liability.id])).get(
        liability.id, base_outstanding(liability)
    )
    periods = _remaining_periods(liability, outstanding)
    total = _term_periods(liability) or periods
    paid = max(0, total - periods)
    plan = _build_plan(liability, outstanding, periods, start_period=paid + 1, total_periods=total)
    return {
        "liability_id": liability.id,
        "outstanding": money(outstanding),
        "method": liability.repayment_method,
        "method_label": enum_zh.zh("repayment_method", liability.repayment_method)
        if liability.repayment_method else "",
        "frequency": liability.payment_frequency,
        "frequency_label": enum_zh.zh("payment_frequency", liability.payment_frequency),
        "periods": periods,
        "total_periods": total,
        "paid_periods": paid,
        "per_period": money(plan[0]["payment"]) if plan else None,
        "schedule": plan,
        "caliber": (
            "还款计划按「剩余本金 ÷ 剩余期数」与所选还款方式测算，每期金额为应还总额（本金 + 利息）；"
            "实际以银行账单与还款记录为准"
        ),
    }


def _plan_interest(plan: list[dict]) -> Decimal:
    """计划总利息（各行 interest 已是 money 字符串）。"""
    return sum((_dec(r["interest"]) for r in plan), Decimal("0"))


def _periods_for_payment(principal: Decimal, payment: Decimal, rate: Decimal) -> int:
    """给定每期应还，反推需要多少期还清（等额本息）。

    n = -ln(1 - P·r/A) / ln(1+r)；利率为 0 时退化为 ceil(P/A)。
    """
    if payment <= 0 or principal <= 0:
        return 0
    if rate <= 0:
        return int(-(-principal // payment))  # 向上取整
    factor = principal * rate / payment
    if factor >= 1:
        # 每期还款连利息都不够，永远还不完
        return 0
    import math

    n = -math.log(1 - float(factor)) / math.log(1 + float(rate))
    return max(1, int(math.ceil(n)))


async def prepay_simulation(
    db: AsyncSession, user: CurrentUser, liability_id: str, amount: Decimal,
) -> dict:
    """提前还款测算：一次性还 `amount` 后，两种常见方案各能省多少利息。

    - **缩短年限**：月供不变，期数减少（总利息省得最多）
    - **减少月供**：期数不变，每期金额降低（月现金流更宽松）

    全部基于库内剩余本金、利率与还款方式测算；实际以银行账单为准。
    """
    liability = await get_liability(db, user, liability_id)
    outstanding = (await outstanding_for(db, user.household_id or "", [liability.id])).get(
        liability.id, base_outstanding(liability)
    )
    if outstanding <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "该笔负债已结清，无需测算")
    if amount <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "提前还款金额需大于 0",
                       field_errors={"amount": "请填写大于 0 的金额"})
    if amount >= outstanding:
        raise AppError(ErrorCode.VALIDATION_ERROR, "提前还款金额不能大于剩余本金",
                       field_errors={"amount": "金额超过剩余本金"})

    # 期数必须来自用户录入的「剩余期数」或「贷款年限」：
    # _remaining_periods 在没有录入时会回退成 12 期（只为展示一年的还款计划），
    # 拿它做提前还款测算会把「33 万本金 ÷ 12 期」算成荒谬结果，这里必须先拦住。
    if liability.remaining_periods is None and not liability.term_months:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "该笔负债还没填「剩余期数」或「贷款年限」，无法测算提前还款",
            field_errors={"remaining_periods": "请先到负债编辑里补全剩余期数"},
        )
    periods = _remaining_periods(liability, outstanding)
    if periods <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "该笔负债未设置剩余期数，无法测算")

    rate = periodic_rate(liability)
    new_principal = outstanding - amount
    method = liability.repayment_method or "equal_installment"

    base_plan = _build_plan(liability, outstanding, periods, total_periods=periods)
    base_interest = _plan_interest(base_plan)
    base_payment = _dec(base_plan[0]["payment"]) if base_plan else Decimal("0")

    # 数据自洽性：等额本息/等额本金的「月供 × 期数」必须能覆盖本金，
    # 否则说明月供或期数录错了（如月供只填了利息），任何测算都没有意义。
    if method in ("equal_installment", "equal_principal") and base_payment * Decimal(periods) < outstanding:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"该笔负债数据不自洽：每期应还 {money(base_payment)} × 剩余 {periods} 期，"
            f"不足以还清剩余本金 {money(outstanding)}，请先核对「剩余期数」与「每期应还」",
            field_errors={"remaining_periods": "月供与剩余期数不匹配，请核对"},
        )

    # 方案一：缩短年限（月供不变）
    shorter = _periods_for_payment(new_principal, base_payment, rate)
    shorter_ok = 0 < shorter < periods
    plan_short = (
        _build_plan(liability, new_principal, shorter, total_periods=shorter,
                    override_payment=base_payment)
        if shorter_ok else []
    )
    interest_short = _plan_interest(plan_short)

    # 方案二：减少月供（期数不变）
    plan_reduce = _build_plan(liability, new_principal, periods, total_periods=periods,
                              use_stored=False)
    reduce_payment_value = _dec(plan_reduce[0]["payment"]) if plan_reduce else Decimal("0")
    reduce_ok = bool(plan_reduce) and 0 < reduce_payment_value < base_payment
    interest_reduce = _plan_interest(plan_reduce)

    def _saved(value: Decimal) -> str:
        saved = base_interest - value
        return money(saved if saved > 0 else Decimal("0"))

    return {
        "liability_id": liability.id,
        "outstanding": money(outstanding),
        "amount": money(amount),
        "new_outstanding": money(new_principal),
        "currency": liability.currency,
        "repayment_method": method,
        "base": {
            "periods": periods,
            "monthly_payment": money(base_payment),
            "total_interest": money(base_interest),
        },
        "shorten_term": {
            "periods": shorter if shorter_ok else periods,
            "saved_periods": max(0, periods - shorter) if shorter_ok else 0,
            "monthly_payment": money(base_payment),
            "total_interest": money(interest_short) if shorter_ok else money(base_interest),
            "saved_interest": _saved(interest_short) if shorter_ok else "0",
            "available": shorter_ok,
        },
        "reduce_payment": {
            "periods": periods,
            "monthly_payment": money(reduce_payment_value) if reduce_ok else "0",
            "total_interest": money(interest_reduce) if reduce_ok else money(base_interest),
            "saved_interest": _saved(interest_reduce) if reduce_ok else "0",
            "available": reduce_ok,
            "monthly_reduction": (
                money(base_payment - reduce_payment_value) if reduce_ok else "0"
            ),
        },
        "caliber": (
            "按剩余本金、当前利率与还款方式测算，未计入提前还款手续费/违约金；"
            "实际以银行账单为准"
        ),
    }


def _term_periods(liability: Liability) -> int | None:
    """合同总期数（按还款频率把年限换算成期数）。"""
    per_year = _FREQ_PER_YEAR.get(liability.payment_frequency or "monthly", 12)
    if liability.term_months:
        return max(1, int(round(liability.term_months * per_year / 12)))
    return None


def _remaining_periods(liability: Liability, outstanding: Decimal) -> int:
    """剩余期数：优先用建档时录入的「还剩多少期」，否则按合同总期限。"""
    if outstanding <= 0:
        return 0
    if liability.remaining_periods is not None:
        return max(0, int(liability.remaining_periods))
    per_year = _FREQ_PER_YEAR.get(liability.payment_frequency or "monthly", 12)
    if liability.term_months:
        return max(1, int(round(liability.term_months * per_year / 12)))
    return min(per_year, 12)  # 未填写年限时默认展示一年


def _add_periods(base: datetime, freq: str, count: int, payment_day: int | None) -> datetime:
    """按还款频率把日期往后推 count 期；有还款日时对齐到该日（月末自动收敛）。"""
    if count == 0:
        return base
    if freq == "weekly":
        return base + timedelta(days=7 * count)
    if freq == "one_time":
        return base
    months = _FREQ_MONTHS.get(freq, 1) * count
    year = base.year
    month = base.month + months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(payment_day or base.day, _days_in_month(year, month))
    return base.replace(year=year, month=month, day=day)


def _build_plan(
    liability: Liability, outstanding: Decimal, periods: int,
    *, start_period: int = 1, total_periods: int | None = None,
    use_stored: bool = True, override_payment: Decimal | None = None,
) -> list[dict]:
    """按剩余本金与剩余期数生成还款计划；每行都是「未还」，金额为应还总额。

    use_stored=False 时忽略账单上录的「每期应还」，按公式重算月供（提前还款模拟用）；
    override_payment 直接指定每期应还（模拟「月供不变、缩短期数」）。
    """
    if periods <= 0 or outstanding <= 0:
        return []
    rate = periodic_rate(liability)
    method = liability.repayment_method or "equal_installment"
    freq = liability.payment_frequency or "monthly"
    first_due = (
        _aware(liability.next_payment_date)
        or compute_next_payment(liability, local_now())
        or local_now()
    )
    rows: list[dict] = []
    balance = outstanding

    def add(idx: int, principal: Decimal, interest: Decimal, payment: Decimal,
            balance_after: Decimal) -> None:
        rows.append({
            "period": start_period + idx,
            "total_periods": total_periods,
            "due_date": local_iso(_add_periods(first_due, freq, idx, liability.payment_day)),
            "principal": money(principal),
            "interest": money(interest),
            "payment": money(payment),
            "remaining": money(balance_after if balance_after > 0 else Decimal("0")),
            "status": "unpaid",
        })

    if method == "equal_principal":
        principal_each = _money4(outstanding / Decimal(periods))
        for idx in range(periods):
            interest = _money4(balance * rate)
            principal = principal_each if idx < periods - 1 else balance
            payment = principal + interest
            balance = balance - principal
            add(idx, principal, interest, payment, balance)
    elif method == "interest_first":
        for idx in range(periods):
            interest = _money4(balance * rate)
            principal = balance if idx == periods - 1 else Decimal("0")
            payment = principal + interest
            balance = balance - principal
            add(idx, principal, interest, payment, balance)
    elif method == "lump_sum":
        interest_total = _money4(balance * rate * Decimal(periods))
        add(0, outstanding, interest_total, outstanding + interest_total, Decimal("0"))
    else:  # equal_installment（默认）
        if rate == 0:
            payment = _money4(outstanding / Decimal(periods))
        else:
            factor = (Decimal("1") + rate) ** periods
            payment = _money4(outstanding * rate * factor / (factor - Decimal("1")))
        # 用户录了账单上的「每期应还」时以它为准（银行常按整数金额收，末期再补差），
        # 但必须能覆盖当期利息，否则退回测算值。
        if override_payment is not None and override_payment > 0:
            stored = override_payment
        else:
            stored = _dec(liability.monthly_payment) if use_stored else Decimal("0")
        if stored > 0 and stored > balance * rate:
            payment = _money4(stored)
        for idx in range(periods):
            interest = _money4(balance * rate)
            principal = payment - interest
            if idx == periods - 1 or principal > balance:
                # 末期补齐：把前面各期四舍五入的差额并入最后一期
                principal = balance
                payment = principal + interest
            balance = balance - principal
            add(idx, principal, interest, payment, balance)
    return rows


async def add_repayment(
    db: AsyncSession, user: CurrentUser, liability_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> LiabilityRepayment:
    """登记还款：按本金/利息/费用拆分，生成本金事件与利息费用支出事件。"""
    liability = await get_liability(db, user, liability_id, for_update=True)
    field_errors: dict[str, str] = {}

    try:
        amount = Decimal(str(payload.get("amount") or "0"))
        if not amount.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "还款总额格式不正确",
                       field_errors={"amount": "金额必须是数字"})
    if amount <= 0:
        field_errors["amount"] = "还款总额必须大于 0"

    fee = Decimal("0")
    if payload.get("fee") not in (None, ""):
        try:
            fee = Decimal(str(payload["fee"]))
            if not fee.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors["fee"] = "费用格式不正确"

    outstanding = (await outstanding_for(db, user.household_id or "", [liability.id])).get(
        liability.id, Decimal("0")
    )
    if outstanding <= 0:
        raise AppError(ErrorCode.STATE_INVALID, "该负债已结清，无需再登记还款", status_code=409)

    suggested_principal, suggested_interest, _ = suggested_split(liability, outstanding, amount, fee)

    principal_raw = payload.get("principal")
    interest_raw = payload.get("interest")
    adjusted = False
    if principal_raw in (None, "") and interest_raw in (None, ""):
        principal, interest = suggested_principal, suggested_interest
    else:
        try:
            principal = Decimal(str(principal_raw if principal_raw not in (None, "") else "0"))
            interest = Decimal(str(interest_raw if interest_raw not in (None, "") else "0"))
            if not interest.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "本金或利息格式不正确",
                           field_errors={"principal": "金额必须是数字"})
        adjusted = True

    if principal < 0 or interest < 0 or fee < 0:
        field_errors["amount"] = "本金、利息与费用都不能为负数"
    if principal + interest + fee != amount:
        field_errors["amount"] = (
            f"本金 + 利息 + 费用 必须等于还款总额 {money(amount)}，"
            f"当前为 {money(principal + interest + fee)}"
        )
    if principal > outstanding:
        field_errors["principal"] = f"本金不能超过剩余本金 {money(outstanding)}"
    if adjusted and not (payload.get("reason") or "").strip():
        field_errors["reason"] = "调整系统建议的本金/利息拆分必须说明原因"
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "还款信息校验失败", field_errors=field_errors)

    account_id = payload.get("account_id")
    if not account_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "登记还款必须选择还款账户",
                       field_errors={"account_id": "请选择还款账户"})
    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
    if account is None or account.household_id != user.household_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "还款账户不存在",
                       field_errors={"account_id": "还款账户不存在"})
    if account.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, "还款账户已停用",
                       field_errors={"account_id": "账户已停用"})

    occurred_at = _parse_dt(payload.get("occurred_at"), "occurred_at") or local_now()
    reason = (payload.get("reason") or "").strip() or None

    # 还款分类：本金与利息共用同一个分类，消费分析里才是一笔「房贷还款」而非拆成两笔
    # 还带一笔「未分类」的本金。按债务类型落到对应还款分类，都能在「还款」一级下找到。
    repayment_category = {
        "mortgage": "房贷还款",
        "vehicle_loan": "车贷还款",
        "credit_card": "信用卡还款",
    }.get(liability.liability_type, "其他贷款还款")
    repayment_category_id = await catalog_service.ensure_category_id(
        db, liability.household_id, "expense", repayment_category, parent_name="还款",
    )

    # 1) 本金事件（liability_repay）：本金计入支出（现金流出），且必须带分类
    principal_event: BusinessEvent | None = None
    if principal > 0:
        principal_event = BusinessEvent(
            id=new_id(), household_id=liability.household_id,
            event_no=_new_event_no(), event_type="liability_repay", status="confirmed",
            occurred_at=occurred_at, confirmed_at=occurred_at,
            amount=principal, currency=liability.currency,
            original_amount=principal, original_currency=liability.currency,
            base_currency=liability.currency, conversion_status="same_currency",
            account_id=account.id, liability_id=liability.id,
            category_id=repayment_category_id,
            member_id=liability.member_id,
            title=f"负债还款本金 · {liability.name}", source="manual",
            note=reason or payload.get("note"), version=1,
            created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(principal_event)
        await db.flush()
        db.add_all([
            EventMovement(
                id=new_id(), household_id=liability.household_id, event_id=principal_event.id,
                target_type="account", target_id=account.id, direction="out",
                amount=principal, currency=liability.currency, note="还款本金",
                created_by=user.user_id, updated_by=user.user_id,
            ),
            EventMovement(
                id=new_id(), household_id=liability.household_id, event_id=principal_event.id,
                target_type="liability", target_id=liability.id, direction="out",
                amount=principal, currency=liability.currency, note="本金减少",
                created_by=user.user_id, updated_by=user.user_id,
            ),
        ])

    # 2) 利息与费用事件（expense）：计入支出，与本金同分类
    interest_event: BusinessEvent | None = None
    interest_total = interest + fee
    if interest_total > 0:
        interest_event = BusinessEvent(
            id=new_id(), household_id=liability.household_id,
            event_no=_new_event_no(), event_type="expense", status="confirmed",
            occurred_at=occurred_at, confirmed_at=occurred_at,
            amount=interest_total, currency=liability.currency,
            original_amount=interest_total, original_currency=liability.currency,
            base_currency=liability.currency, conversion_status="same_currency",
            account_id=account.id, liability_id=liability.id,
            category_id=repayment_category_id,
            member_id=liability.member_id,
            title=f"负债利息与费用 · {liability.name}", source="manual",
            note=reason, version=1,
            created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(interest_event)
        await db.flush()
        db.add(EventMovement(
            id=new_id(), household_id=liability.household_id, event_id=interest_event.id,
            target_type="account", target_id=account.id, direction="out",
            amount=interest_total, currency=liability.currency, note="利息与费用",
            created_by=user.user_id, updated_by=user.user_id,
        ))

    repayment = LiabilityRepayment(
        id=new_id(),
        household_id=liability.household_id,
        liability_id=liability.id,
        amount=amount,
        principal=principal,
        interest=interest,
        fee=fee,
        currency=liability.currency,
        occurred_at=occurred_at,
        account_id=account.id,
        bill_id=payload.get("bill_id"),
        principal_event_id=principal_event.id if principal_event else None,
        interest_event_id=interest_event.id if interest_event else None,
        member_id=liability.member_id,
        reason=reason,
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(repayment)
    await db.flush()

    # 信用卡账单回写
    if payload.get("bill_id"):
        bill = (await db.execute(
            select(CreditCardBill).where(
                CreditCardBill.id == payload["bill_id"],
                CreditCardBill.deleted_at.is_(None),
            )
        )).scalars().first()
        if bill is None or bill.household_id != liability.household_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "账单不存在",
                           field_errors={"bill_id": "账单不存在或已删除"})
        # 只能核销当前这张卡的账单：信用卡实体通过 liability_id 关联负债
        from app.db.models.finance import CreditCard

        card = (await db.execute(
            select(CreditCard).where(
                CreditCard.liability_id == liability.id,
                CreditCard.deleted_at.is_(None),
            )
        )).scalars().first()
        if card is None:
            raise AppError(ErrorCode.VALIDATION_ERROR, "当前负债不是信用卡",
                           field_errors={"bill_id": "该负债没有关联信用卡"})
        if str(bill.card_id) != str(card.id):
            raise AppError(ErrorCode.VALIDATION_ERROR, "该账单不属于当前卡片",
                           field_errors={"bill_id": "账单与当前卡不匹配"})
        # 多还的部分属于溢缴款，不写进本期账单；已还金额最多等于账单金额
        bill.paid_amount = _money4(min(
            _dec(bill.total_amount),
            _dec(bill.paid_amount) + amount,
        ))
        bill.version += 1
        bill.updated_by = user.user_id

    # 3) 推进还款计划：下次还款日顺延一期、剩余期数减一，结清时收口
    remaining_principal = outstanding - principal
    freq = liability.payment_frequency or "monthly"
    if remaining_principal <= 0:
        liability.status = "settled"
        liability.next_payment_date = None
        liability.remaining_periods = 0
    else:
        current_due = _aware(liability.next_payment_date)
        # 还的是「当前这一期」（提前还或当期还）才顺延；补录更早的历史还款不动未来还款日
        prior_due = _add_periods(current_due, freq, -1, liability.payment_day) if current_due else None
        if freq != "one_time" and (
            current_due is None or _aware(occurred_at) >= (prior_due or current_due)
        ):
            liability.next_payment_date = to_local_naive(
                _add_periods(current_due or _aware(occurred_at), freq, 1, liability.payment_day)
            )
        if liability.remaining_periods is not None and liability.remaining_periods > 0:
            liability.remaining_periods -= 1
        if liability.status == "overdue":
            liability.status = "active"
    liability.version += 1
    liability.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="liability.repayment",
        action_label="登记负债还款",
        object_type="liability",
        object_id=liability.id,
        related_object_id=repayment.id,
        after={
            "amount": amount, "principal": principal, "interest": interest, "fee": fee,
            "account_id": account.id, "suggested_principal": suggested_principal,
            "suggested_interest": suggested_interest, "adjusted": adjusted,
        },
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return repayment


async def add_draw(
    db: AsyncSession, user: CurrentUser, liability_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    """借款到账：账户增加、负债本金增加；不算收入（01 §4）。"""
    liability = await get_liability(db, user, liability_id)
    liability_type = liability.liability_type
    if liability_type in ("mortgage", "credit_card"):
        raise AppError(
            ErrorCode.STATE_INVALID,
            "房贷与信用卡不支持借款到账登记；房贷请直接维护剩余本金，信用卡请登记消费",
            status_code=409,
        )
    try:
        amount = Decimal(str(payload.get("amount") or "0"))
        if not amount.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "借款金额格式不正确",
                       field_errors={"amount": "金额必须是数字"})
    if amount <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "借款金额必须大于 0",
                       field_errors={"amount": "借款金额必须大于 0"})

    account_id = payload.get("account_id")
    if not account_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "借款到账必须选择入账账户",
                       field_errors={"account_id": "请选择入账账户"})
    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
    if account is None or account.household_id != user.household_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "入账账户不存在",
                       field_errors={"account_id": "入账账户不存在"})

    occurred_at = _parse_dt(payload.get("occurred_at"), "occurred_at") or local_now()
    event = BusinessEvent(
        id=new_id(), household_id=liability.household_id, event_no=_new_event_no(),
        event_type="liability_draw", status="confirmed",
        occurred_at=occurred_at, confirmed_at=occurred_at,
        amount=amount, currency=liability.currency,
        original_amount=amount, original_currency=liability.currency,
        base_currency=liability.currency, conversion_status="same_currency",
        account_id=account.id, liability_id=liability.id, member_id=liability.member_id,
        title=f"借款到账 · {liability.name}", source="manual",
        note=payload.get("note"), version=1,
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(event)
    await db.flush()
    db.add_all([
        EventMovement(
            id=new_id(), household_id=liability.household_id, event_id=event.id,
            target_type="account", target_id=account.id, direction="in",
            amount=amount, currency=liability.currency, note="借款到账",
            created_by=user.user_id, updated_by=user.user_id,
        ),
        EventMovement(
            id=new_id(), household_id=liability.household_id, event_id=event.id,
            target_type="liability", target_id=liability.id, direction="in",
            amount=amount, currency=liability.currency, note="本金增加",
            created_by=user.user_id, updated_by=user.user_id,
        ),
    ])
    await audit_service.record_audit(
        db,
        action="liability.draw",
        action_label="借款到账",
        object_type="liability",
        object_id=liability.id,
        related_object_id=event.id,
        after={"amount": amount, "account_id": account.id, "event_no": event.event_no},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return event


def _new_event_no() -> str:
    return f"EV{local_now().strftime('%Y%m%d')}{new_id()[:6].upper()}"


# ---------------- 信用卡（06 §7） ----------------

async def get_card(db: AsyncSession, user: CurrentUser, card_id: str) -> CreditCard:
    card = (await db.execute(select(CreditCard).where(CreditCard.id == card_id))).scalars().first()
    if card is None or card.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "信用卡不存在", status_code=404)
    if card.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return card


async def list_cards(db: AsyncSession, user: CurrentUser, *, reveal: bool = False,
                     keyword: str | None = None) -> list[dict]:
    conditions = [CreditCard.household_id == user.household_id, CreditCard.deleted_at.is_(None)]
    cards = list((await db.execute(select(CreditCard).where(*conditions))).scalars().all())
    if not cards:
        return []
    liabilities = {
        l.id: l for l in (await db.execute(
            select(Liability).where(Liability.id.in_([c.liability_id for c in cards]))
        )).scalars().all()
    }
    outstanding = await outstanding_for(db, user.household_id or "", list(liabilities.keys()))
    now = local_now()
    items: list[dict] = []
    for card in cards:
        liability = liabilities.get(card.liability_id)
        if liability is None or liability.deleted_at is not None:
            continue
        if keyword and keyword not in (liability.name or ""):
            continue
        out = outstanding.get(liability.id, Decimal("0"))
        info = await card_info(db, liability, out, reveal=reveal)
        data = serialize_liability(liability, out, today=now, card=info or {})
        items.append(data)
    return items


async def create_bill(
    db: AsyncSession, user: CurrentUser, card_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> CreditCardBill:
    card = await get_card(db, user, card_id)
    field_errors: dict[str, str] = {}
    try:
        total = Decimal(str(payload.get("total_amount") or "0"))
        if not total.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "账单金额格式不正确",
                       field_errors={"total_amount": "金额必须是数字"})
    if total < 0:
        field_errors["total_amount"] = "账单金额不能为负数"

    minimum = None
    if payload.get("minimum_payment") not in (None, ""):
        try:
            minimum = Decimal(str(payload["minimum_payment"]))
            if not minimum.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors["minimum_payment"] = "最低还款格式不正确"
        else:
            if minimum < 0:
                field_errors["minimum_payment"] = "最低还款不能为负数"

    bill_date = _parse_dt(payload.get("bill_date"), "bill_date")
    due_date = _parse_dt(payload.get("due_date"), "due_date")
    period_start = _parse_dt(payload.get("period_start"), "period_start")
    period_end = _parse_dt(payload.get("period_end"), "period_end")
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "账单信息校验失败", field_errors=field_errors)

    liability = await get_liability(db, user, card.liability_id)
    bill = CreditCardBill(
        id=new_id(),
        household_id=card.household_id,
        card_id=card.id,
        period_start=period_start,
        period_end=period_end,
        bill_date=bill_date or local_now(),
        due_date=due_date,
        total_amount=total,
        minimum_payment=minimum,
        paid_amount=Decimal("0"),
        currency=liability.currency,
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(bill)
    await db.flush()
    await audit_service.record_audit(
        db,
        action="credit_card.bill_create",
        action_label="生成信用卡账单",
        object_type="credit_card",
        object_id=card.id,
        related_object_id=bill.id,
        after={"total_amount": total, "minimum_payment": minimum,
               "bill_date": bill.bill_date, "due_date": due_date},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return bill


async def add_card_expense(
    db: AsyncSession, user: CurrentUser, card_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    """记一笔信用卡消费：生成支出事件 + 负债本金增加（不涉及银行账户资金变动）。"""
    card = await get_card(db, user, card_id)
    liability = await get_liability(db, user, card.liability_id)
    try:
        amount = Decimal(str(payload.get("amount") or "0"))
        if not amount.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "消费金额格式不正确",
                       field_errors={"amount": "金额必须是数字"})
    if amount <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "消费金额必须大于 0",
                       field_errors={"amount": "消费金额必须大于 0"})
    title = (payload.get("title") or "").strip() or "信用卡消费"
    occurred_at = _parse_dt(payload.get("occurred_at"), "occurred_at") or local_now()

    category_id = payload.get("category_id")
    if category_id:
        from app.db.models.config import Category

        category = (await db.execute(select(Category).where(Category.id == category_id))).scalars().first()
        if category is None or category.household_id != user.household_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "分类不存在",
                           field_errors={"category_id": "分类不存在"})
        if category.domain != "expense":
            raise AppError(ErrorCode.VALIDATION_ERROR, "信用卡消费必须使用支出分类",
                           field_errors={"category_id": "该分类不是支出分类"})

    event = BusinessEvent(
        id=new_id(), household_id=liability.household_id, event_no=_new_event_no(),
        event_type="expense", status="confirmed",
        occurred_at=occurred_at, confirmed_at=occurred_at,
        amount=amount, currency=liability.currency,
        original_amount=amount, original_currency=liability.currency,
        base_currency=liability.currency, conversion_status="same_currency",
        liability_id=liability.id, category_id=category_id, member_id=liability.member_id,
        title=title, counterparty=payload.get("counterparty"), source="manual",
        note=payload.get("note"), version=1,
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(event)
    await db.flush()
    db.add(EventMovement(
        id=new_id(), household_id=liability.household_id, event_id=event.id,
        target_type="liability", target_id=liability.id, direction="in",
        amount=amount, currency=liability.currency, note="信用卡消费（欠款增加）",
        created_by=user.user_id, updated_by=user.user_id,
    ))
    await audit_service.record_audit(
        db,
        action="credit_card.expense",
        action_label="信用卡消费",
        object_type="credit_card",
        object_id=card.id,
        related_object_id=event.id,
        after={"amount": amount, "title": title, "event_no": event.event_no},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return event


def serialize_card_event(event: BusinessEvent, movement: EventMovement | None) -> dict:
    return {
        "id": event.id,
        "event_no": event.event_no,
        "event_type": event.event_type,
        "event_type_label": enum_zh.zh("event_type", event.event_type),
        "title": event.title,
        "amount": money(event.amount),
        "currency": event.currency,
        "status": event.status,
        "status_label": enum_zh.zh("event_status", event.status),
        "occurred_at": local_iso(event.occurred_at) if event.occurred_at else None,
        "direction": movement.direction if movement else None,
        "note": event.note,
    }


async def card_activity(
    db: AsyncSession, user: CurrentUser, card_id: str, *, limit: int = 100
) -> list[tuple[BusinessEvent, EventMovement | None]]:
    card = await get_card(db, user, card_id)
    rows = (
        await db.execute(
            select(BusinessEvent, EventMovement)
            .outerjoin(EventMovement, EventMovement.event_id == BusinessEvent.id)
            .where(
                BusinessEvent.liability_id == card.liability_id,
                BusinessEvent.household_id == user.household_id,
                BusinessEvent.deleted_at.is_(None),
            )
            .order_by(BusinessEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]
