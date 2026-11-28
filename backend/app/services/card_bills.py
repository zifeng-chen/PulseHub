"""账户型信用卡账单（Account + credit_limit 建模的卡）。

为什么单独有这张表：信用卡在本项目有两种建模方式——
- 负债型：Liability + CreditCard + `credit_card_bills`（M3 的信用卡页用这套）；
- 账户型：Account + credit_limit（家庭实际使用的方式，卡就是账户，欠款=负余额）。

账户型没有卡实体，于是账单落 `account_card_bills`。账单只回答两个问题：
「本期该还多少」「几号到期」，**不改变余额**——余额永远由流水决定；
还款（内部转账到这张卡）只冲减账单的 paid_amount，多还部分算溢缴款。

口径：
- 账单金额 = 账单周期内该卡的已确认消费（expense）− 退款（refund）；
- 账单周期 = [上一个账单日, 本期账单日)，按账户的 bill_day 取整月；
- 到期日 = 账单日之后最近的 due_day（若 due_day ≤ bill_day 则顺延到下月）；
- 未还金额 = max(0, 账单金额 − 已还金额)；还款按到期日从早到晚冲减。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id
from app.core.money import fmt_amount
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.finance import AccountCardBill, CardBillOffset
from app.services import audit as audit_service


def _dec(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def money(value) -> str:
    """金额对外统一输出（与负债/资产服务同口径）。"""
    return fmt_amount(_dec(value))


def _add_months(day: date, months: int, keep_day: int | None = None) -> date:
    """按月平移并夹到月末（1 月 31 日 + 1 月 → 2 月 28/29 日）。"""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    target_day = keep_day if keep_day else day.day
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(target_day, last))


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


async def _card_account(db: AsyncSession, user: CurrentUser, account_id: str) -> Account:
    account = (await db.execute(
        select(Account).where(Account.id == account_id, Account.deleted_at.is_(None))
    )).scalars().first()
    if account is None or account.household_id != user.household_id:
        raise AppError(ErrorCode.NOT_FOUND, "账户不存在", status_code=404)
    if account.credit_limit is None:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该账户不是信用卡（没有授信额度），不需要账单",
            status_code=409,
        )
    return account


def serialize_bill(bill: AccountCardBill, account_name: str = "") -> dict:
    total = _dec(bill.total_amount)
    paid = _dec(bill.paid_amount)
    offset = _dec(bill.offset_amount)
    unpaid = max(Decimal("0"), total - paid - offset)
    return {
        "id": bill.id,
        "account_id": bill.account_id,
        "account_name": account_name,
        "period_start": local_iso(bill.period_start) if bill.period_start else None,
        "period_end": local_iso(bill.period_end) if bill.period_end else None,
        "bill_date": local_iso(bill.bill_date) if bill.bill_date else None,
        "due_date": local_iso(bill.due_date) if bill.due_date else None,
        "total_amount": money(total),
        "minimum_payment": money(_dec(bill.minimum_payment)) if bill.minimum_payment is not None else None,
        "paid_amount": money(paid),
        "offset_amount": money(offset),
        "unpaid_amount": money(unpaid),
        "currency": bill.currency,
        "settled": unpaid <= 0,
        "overdue": bool(
            unpaid > 0 and bill.due_date and to_local_naive(bill.due_date) < local_now()
        ),
        "note": bill.note,
        "caliber": (
            "账单金额 = 周期内已确认消费 − 退款；未还 = 账单金额 − 已还 − 抵扣；"
            "已还由还款（内部转账到该卡）累加，抵扣由抵扣金/积分减免，两者都不计入收支"
        ),
    }


async def _period_spend(
    db: AsyncSession, household_id: str, account_id: str, start: datetime, end: datetime,
) -> Decimal:
    """账单周期内该卡的消费净额：已确认支出 − 退款（不含还款，还款是内部转账）。"""
    rows = (await db.execute(
        select(BusinessEvent.event_type, BusinessEvent.amount).where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.account_id == account_id,
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.status == "confirmed",
            BusinessEvent.event_type.in_(("expense", "refund")),
            BusinessEvent.occurred_at >= start,
            BusinessEvent.occurred_at < end,
        )
    )).all()
    spend = Decimal("0")
    for event_type, amount in rows:
        value = _dec(amount)
        spend += value if event_type == "expense" else -value
    return spend


async def generate_bill(
    db: AsyncSession,
    user: CurrentUser,
    account_id: str,
    payload: dict,
    ctx: RequestContext | None = None,
) -> AccountCardBill:
    """生成一期账单。不传金额时按账单周期汇总该卡消费。"""
    account = await _card_account(db, user, account_id)

    bill_date = _as_date(payload.get("bill_date"))
    if bill_date is None:
        today = local_now().date()
        if account.bill_day:
            bill_date = _add_months(today.replace(day=1), 0, keep_day=account.bill_day)
            if bill_date > today:
                bill_date = _add_months(bill_date, -1, keep_day=account.bill_day)
        else:
            bill_date = today

    period_start_date = _as_date(payload.get("period_start"))
    period_end_date = _as_date(payload.get("period_end")) or bill_date
    if period_start_date is None:
        period_start_date = _add_months(period_end_date, -1, keep_day=account.bill_day or period_end_date.day)
    if period_start_date >= period_end_date:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "账单周期起点必须早于终点",
            field_errors={"period_start": "周期起点必须早于账单日"},
        )

    period_start = datetime.combine(period_start_date, datetime.min.time())
    period_end = datetime.combine(period_end_date, datetime.min.time())

    existing = (await db.execute(
        select(AccountCardBill).where(
            AccountCardBill.account_id == account.id,
            AccountCardBill.deleted_at.is_(None),
            AccountCardBill.period_end == period_end,
        )
    )).scalars().first()
    if existing is not None:
        raise AppError(
            ErrorCode.STATE_INVALID,
            f"{period_end_date:%Y-%m-%d} 这一期的账单已经生成过了，可在账单里查看已还金额",
            status_code=409,
        )

    raw_total = payload.get("total_amount")
    if raw_total in (None, ""):
        total = await _period_spend(db, account.household_id, account.id, period_start, period_end)
        auto_amount = True
    else:
        try:
            total = _dec(raw_total)
            if not total.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "账单金额格式不正确",
                           field_errors={"total_amount": "金额必须是数字"})
        auto_amount = False
    if total < 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "账单金额不能是负数（退款多于消费请检查周期）",
                       field_errors={"total_amount": "账单金额不能为负数"})

    minimum = None
    if payload.get("minimum_payment") not in (None, ""):
        minimum = _dec(payload["minimum_payment"])
        if minimum < 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "最低还款不能是负数",
                           field_errors={"minimum_payment": "最低还款不能是负数"})

    due_date = _as_date(payload.get("due_date"))
    if due_date is None and account.due_day:
        candidate = _add_months(bill_date.replace(day=1), 0, keep_day=account.due_day)
        if candidate <= bill_date:
            candidate = _add_months(candidate, 1, keep_day=account.due_day)
        due_date = candidate

    bill = AccountCardBill(
        id=new_id(),
        household_id=account.household_id,
        account_id=account.id,
        period_start=period_start,
        period_end=period_end,
        bill_date=datetime.combine(bill_date, datetime.min.time()),
        due_date=datetime.combine(due_date, datetime.min.time()) if due_date else None,
        total_amount=total,
        minimum_payment=minimum,
        paid_amount=Decimal("0"),
        currency=account.currency,
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(bill)
    await db.flush()

    await audit_service.record_audit(
        db,
        action="account_card.bill_create",
        action_label="生成信用卡账单",
        object_type="account",
        object_id=account.id,
        related_object_id=bill.id,
        after={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "total_amount": str(total),
            "auto_amount": auto_amount,
        },
        user=user,
        ctx=ctx,
    )
    return bill


async def list_bills(
    db: AsyncSession, user: CurrentUser, account_id: str | None = None,
    *, unpaid_only: bool = False, limit: int = 60,
) -> list[AccountCardBill]:
    stmt = select(AccountCardBill).where(
        AccountCardBill.household_id == user.household_id,
        AccountCardBill.deleted_at.is_(None),
    )
    if account_id:
        stmt = stmt.where(AccountCardBill.account_id == account_id)
    stmt = stmt.order_by(AccountCardBill.bill_date.desc()).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    if unpaid_only:
        rows = [b for b in rows if _dec(b.total_amount) - _dec(b.paid_amount) > 0]
    return rows


async def apply_payment(
    db: AsyncSession, household_id: str, account_id: str, amount: Decimal, user_id: str | None = None,
) -> Decimal:
    """把一笔还款按到期日从早到晚冲减到该卡的未结清账单上。

    只动 paid_amount（上限为账单金额），返回实际冲减金额；多还部分是溢缴款，
    由账户余额体现，不写进账单。
    """
    if amount <= 0:
        return Decimal("0")
    bills = list((await db.execute(
        select(AccountCardBill).where(
            AccountCardBill.household_id == household_id,
            AccountCardBill.account_id == account_id,
            AccountCardBill.deleted_at.is_(None),
        ).order_by(AccountCardBill.due_date.asc(), AccountCardBill.bill_date.asc())
    )).scalars().all())

    remaining = amount
    allocated = Decimal("0")
    for bill in bills:
        if remaining <= 0:
            break
        unpaid = _dec(bill.total_amount) - _dec(bill.paid_amount)
        if unpaid <= 0:
            continue
        take = min(unpaid, remaining)
        bill.paid_amount = _dec(bill.paid_amount) + take
        bill.version += 1
        if user_id:
            bill.updated_by = user_id
        remaining -= take
        allocated += take
    if allocated > 0:
        await db.flush()
    return allocated


async def release_payment(
    db: AsyncSession, household_id: str, account_id: str, amount: Decimal, user_id: str | None = None,
) -> Decimal:
    """冲销还款时把已还金额退回去（与 apply_payment 相反，从最近一期往回退）。"""
    if amount <= 0:
        return Decimal("0")
    bills = list((await db.execute(
        select(AccountCardBill).where(
            AccountCardBill.household_id == household_id,
            AccountCardBill.account_id == account_id,
            AccountCardBill.deleted_at.is_(None),
        ).order_by(AccountCardBill.due_date.desc(), AccountCardBill.bill_date.desc())
    )).scalars().all())

    remaining = amount
    released = Decimal("0")
    for bill in bills:
        if remaining <= 0:
            break
        paid = _dec(bill.paid_amount)
        if paid <= 0:
            continue
        take = min(paid, remaining)
        bill.paid_amount = paid - take
        bill.version += 1
        if user_id:
            bill.updated_by = user_id
        remaining -= take
        released += take
    if released > 0:
        await db.flush()
    return released


async def unpaid_parts(db: AsyncSession, household_id: str) -> dict[str, dict]:
    """按卡汇总未结清账单：{account_id: {unpaid, due_date, has_bill, overdue}}。"""
    rows = list((await db.execute(
        select(AccountCardBill).where(
            AccountCardBill.household_id == household_id,
            AccountCardBill.deleted_at.is_(None),
        )
    )).scalars().all())
    now = local_now()
    parts: dict[str, dict] = {}
    for bill in rows:
        unpaid = _dec(bill.total_amount) - _dec(bill.paid_amount)
        bucket = parts.setdefault(bill.account_id, {
            "unpaid": Decimal("0"), "due_date": None, "has_bill": False, "overdue": False,
        })
        bucket["has_bill"] = True
        if unpaid <= 0:
            continue
        bucket["unpaid"] += unpaid
        due = to_local_naive(bill.due_date) if bill.due_date else None
        if due is not None and (bucket["due_date"] is None or due < bucket["due_date"]):
            bucket["due_date"] = due
        if due is not None and due < now:
            bucket["overdue"] = True
    return parts


# ---------------- 账单抵扣（抵扣金 / 积分） ----------------

OFFSET_TYPES = {"credit": "抵扣金", "points": "积分抵现"}


def serialize_offset(row: CardBillOffset) -> dict:
    return {
        "id": row.id,
        "bill_id": row.bill_id,
        "account_id": row.account_id,
        "offset_type": row.offset_type,
        "offset_type_label": OFFSET_TYPES.get(row.offset_type, row.offset_type),
        "amount": money(_dec(row.amount)),
        "points": row.points,
        "note": row.note,
        "status": row.status,
        "created_at": local_iso(row.created_at) if row.created_at else None,
    }


async def _bill_of(db: AsyncSession, user: CurrentUser, bill_id: str) -> AccountCardBill:
    bill = (await db.execute(
        select(AccountCardBill).where(
            AccountCardBill.id == bill_id,
            AccountCardBill.household_id == user.household_id,
            AccountCardBill.deleted_at.is_(None),
        )
    )).scalars().first()
    if bill is None:
        raise AppError(ErrorCode.NOT_FOUND, "账单不存在", status_code=404)
    return bill


def _bill_unpaid(bill: AccountCardBill) -> Decimal:
    return max(
        Decimal("0"),
        _dec(bill.total_amount) - _dec(bill.paid_amount) - _dec(bill.offset_amount),
    )


async def get_bill(db: AsyncSession, user: CurrentUser, bill_id: str) -> AccountCardBill:
    return await _bill_of(db, user, bill_id)


async def apply_offset(
    db: AsyncSession, user: CurrentUser, account_id: str, bill_id: str,
    amount, offset_type: str = "credit", points: int | None = None, note: str | None = None,
) -> tuple[CardBillOffset, BusinessEvent]:
    """用抵扣金 / 积分抵减账单：不产生现金流水，但减少欠款与账单未还。

    同步写一笔 card_offset 事件（方向 in 到信用卡账户，统计上不计收支），
    这样账户余额、净资产会自动跟着变，不需要额外对账。
    """
    amount = _dec(amount)
    if amount <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "抵扣金额必须大于 0", status_code=422)
    if offset_type not in OFFSET_TYPES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "抵扣方式不合法", status_code=422)

    account = await _card_account(db, user, account_id)
    bill = await _bill_of(db, user, bill_id)
    if bill.account_id != account_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "账单与信用卡不匹配", status_code=422)

    unpaid = _bill_unpaid(bill)
    if unpaid <= 0:
        raise AppError(ErrorCode.STATE_INVALID, "该账单已结清，无需抵扣", status_code=409)
    if amount > unpaid + Decimal("0.0001"):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"抵扣金额超过未还金额（未还 {money(unpaid)}）",
            field_errors={"amount": f"最多可抵 {money(unpaid)}"},
            status_code=422,
        )

    now = local_now()
    offset = CardBillOffset(
        id=new_id(), household_id=user.household_id or bill.household_id,
        bill_id=bill.id, account_id=account.id,
        offset_type=offset_type, amount=amount, points=points,
        note=note, status="confirmed", version=1,
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(offset)

    bill.offset_amount = _dec(bill.offset_amount) + amount
    bill.version += 1
    bill.updated_by = user.user_id

    label = OFFSET_TYPES[offset_type]
    tail = f"（积分 {points}）" if points else ""
    event = BusinessEvent(
        id=new_id(), household_id=user.household_id,
        event_no=f"EV{now.strftime('%Y%m%d')}{new_id()[:6].upper()}",
        event_type="card_offset", status="confirmed",
        occurred_at=now, confirmed_at=now,
        amount=amount, currency=account.currency,
        original_amount=amount, original_currency=account.currency,
        base_currency=account.currency, conversion_status="same_currency",
        account_id=account.id,
        title=f"账单抵扣 · {account.name}",
        source="manual",
        note=f"{label}{tail}：{note}" if note else f"{label}{tail}",
        version=1, created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(event)
    await db.flush()

    db.add(EventMovement(
        id=new_id(), household_id=user.household_id, event_id=event.id,
        target_type="account", target_id=account.id, direction="in",
        amount=amount, currency=account.currency,
        note=f"信用卡{label}（不计收支）",
        created_by=user.user_id, updated_by=user.user_id,
    ))
    offset.event_id = event.id
    await db.flush()
    return offset, event


async def release_offset(
    db: AsyncSession, user: CurrentUser, offset_id: str,
) -> CardBillOffset:
    """撤销抵扣：账单抵扣额退回、原事件作废；资金变动保留但不再计入余额。"""
    offset = (await db.execute(
        select(CardBillOffset).where(
            CardBillOffset.id == offset_id,
            CardBillOffset.household_id == user.household_id,
            CardBillOffset.deleted_at.is_(None),
        )
    )).scalars().first()
    if offset is None:
        raise AppError(ErrorCode.NOT_FOUND, "抵扣记录不存在", status_code=404)
    if offset.status != "confirmed":
        raise AppError(ErrorCode.STATE_INVALID, "该抵扣已撤销", status_code=409)

    bill = await _bill_of(db, user, offset.bill_id)
    bill.offset_amount = max(Decimal("0"), _dec(bill.offset_amount) - _dec(offset.amount))
    bill.version += 1
    bill.updated_by = user.user_id

    offset.status = "reversed"
    offset.version += 1
    offset.updated_by = user.user_id

    if offset.event_id:
        event = (await db.execute(
            select(BusinessEvent).where(BusinessEvent.id == offset.event_id)
        )).scalars().first()
        if event is not None and event.status == "confirmed":
            event.status = "reversed"
            event.version += 1
            event.updated_by = user.user_id
    await db.flush()
    return offset


async def list_offsets(db: AsyncSession, user: CurrentUser, bill_id: str) -> list[CardBillOffset]:
    return list((await db.execute(
        select(CardBillOffset).where(
            CardBillOffset.bill_id == bill_id,
            CardBillOffset.household_id == user.household_id,
            CardBillOffset.deleted_at.is_(None),
        ).order_by(CardBillOffset.created_at.desc())
    )).scalars().all())


async def offset_caliber() -> str:
    return ("抵扣 = 抵扣金或积分减免账单：账单未还与信用卡欠款同步减少，"
            "但不产生现金流水，不计入收入与支出；撤销后账单抵扣额原样退回。")
