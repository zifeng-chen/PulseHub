"""周期固定收入服务（工资、养老金、固定房租收入等金额固定、按周期重复）。

对称 `recurring.py` 的固定支出：
- 登记一次固定收入，到期自动生成一笔已确认的**收入**事件（走统一事件模型，
  账户余额、收入统计口径与手工记账完全一致）；
- 关联家庭成员（member_id），多成员家庭可区分各自收入来源；
- 到期推进 next_due_date，形成连续账目；
- 停用/删除不影响已生成的历史收入事件。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id
from app.core.money import fmt_amount
from app.core.timeutil import local_now, to_local_naive
from app.db.models.business import BusinessEvent, EventMovement, RecurringIncome
from app.services import audit as audit_service

FREQUENCIES = ("monthly", "weekly", "yearly")

RECURRING_INCOME_EDITABLE = (
    "title", "category_id", "account_id", "member_id", "currency", "amount",
    "frequency", "day_of_month", "next_due_date", "status", "note",
)

_STATUS_LABEL = {"active": "生效中", "paused": "已暂停", "stopped": "已停用"}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def serialize(item: RecurringIncome, *, category_label: str | None = None,
              member_name: str | None = None) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "category_id": item.category_id,
        "category_label": category_label or "",
        "account_id": item.account_id,
        "member_id": item.member_id,
        "member_name": member_name or "",
        "currency": item.currency,
        "amount": fmt_amount(item.amount),
        "frequency": item.frequency,
        "frequency_label": enum_zh.zh("payment_frequency", item.frequency),
        "day_of_month": item.day_of_month,
        "next_due_date": item.next_due_date.isoformat() if item.next_due_date else None,
        "status": item.status,
        "status_label": _STATUS_LABEL.get(item.status, item.status),
        "note": item.note,
        "version": item.version,
    }


def _compute_next_due(frequency: str, day_of_month: int | None, base: datetime) -> datetime:
    """推算下一个发薪日（本地墙钟 naive）。"""
    if frequency == "weekly":
        return base + timedelta(days=7)
    if frequency == "yearly":
        year = base.year + 1
        month = 1 if day_of_month is None else day_of_month
        day = 1
        return base.replace(year=year, month=month, day=day)
    # monthly：下个月的同一天；月末自动收敛
    day = day_of_month or base.day
    year = base.year
    month = base.month + 1
    if month > 12:
        month = 1
        year += 1
    import calendar

    last = calendar.monthrange(year, month)[1]
    return base.replace(year=year, month=month, day=min(day, last))


def _validate(payload: dict, *, partial: bool) -> tuple[dict, dict]:
    field_errors: dict[str, str] = {}
    data: dict = {}

    if "title" in payload or not partial:
        title = (payload.get("title") or "").strip()
        if not title:
            field_errors["title"] = "请填写收入名称"
        else:
            data["title"] = title

    if "amount" in payload or not partial:
        try:
            amount = Decimal(str(payload.get("amount") or "0"))
            if not amount.is_finite() or amount <= 0:
                raise ValueError("non-positive")
        except Exception:  # noqa: BLE001
            field_errors["amount"] = "金额必须是大于 0 的数字"
        else:
            data["amount"] = amount

    if "frequency" in payload and payload["frequency"] not in (None, ""):
        if payload["frequency"] not in FREQUENCIES:
            field_errors["frequency"] = "周期不合法"
        else:
            data["frequency"] = payload["frequency"]

    if "day_of_month" in payload and payload["day_of_month"] not in (None, ""):
        try:
            day = int(payload["day_of_month"])
            if not 1 <= day <= 31:
                raise ValueError("out-of-range")
        except (TypeError, ValueError):
            field_errors["day_of_month"] = "发薪日必须是 1-31 的整数"
        else:
            data["day_of_month"] = day

    if "category_id" in payload:
        data["category_id"] = payload["category_id"] or None
    if "account_id" in payload:
        data["account_id"] = payload["account_id"] or None
    if "member_id" in payload:
        data["member_id"] = payload["member_id"] or None
    if "currency" in payload and payload["currency"]:
        data["currency"] = payload["currency"]
    if "status" in payload and payload["status"] in ("active", "paused", "stopped"):
        data["status"] = payload["status"]
    if "next_due_date" in payload and payload["next_due_date"] not in (None, ""):
        try:
            data["next_due_date"] = to_local_naive(
                datetime.fromisoformat(str(payload["next_due_date"]).replace("Z", "+00:00"))
            )
        except ValueError:
            field_errors["next_due_date"] = "日期格式应为 ISO 8601"
    if "note" in payload:
        data["note"] = payload["note"] or None

    return data, field_errors


async def list_recurring_incomes(
    db: AsyncSession, user: CurrentUser, *, status: str | None = None
) -> list[RecurringIncome]:
    stmt = select(RecurringIncome).where(
        RecurringIncome.household_id == user.household_id,
        RecurringIncome.deleted_at.is_(None),
    )
    if status:
        stmt = stmt.where(RecurringIncome.status == status)
    stmt = stmt.order_by(RecurringIncome.status, RecurringIncome.next_due_date)
    return list((await db.execute(stmt)).scalars().all())


async def get_recurring_income(db: AsyncSession, user: CurrentUser, item_id: str) -> RecurringIncome:
    item = (await db.execute(
        select(RecurringIncome).where(
            RecurringIncome.id == item_id,
            RecurringIncome.household_id == user.household_id,
            RecurringIncome.deleted_at.is_(None),
        )
    )).scalars().first()
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "固定收入不存在")
    return item


async def create_recurring_income(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None,
) -> RecurringIncome:
    data, field_errors = _validate(payload, partial=False)
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "固定收入信息校验失败", field_errors=field_errors)

    now = local_now()
    frequency = data.get("frequency", "monthly")
    day = data.get("day_of_month")
    next_due = data.get("next_due_date") or _compute_next_due(frequency, day, now)

    item = RecurringIncome(
        id=new_id(),
        household_id=user.household_id or "",
        title=data["title"],
        category_id=data.get("category_id"),
        account_id=data.get("account_id"),
        member_id=data.get("member_id"),
        currency=data.get("currency", "CNY"),
        amount=data["amount"],
        frequency=frequency,
        day_of_month=day,
        next_due_date=to_local_naive(next_due) if next_due else None,
        status="active",
        note=data.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(item)
    await db.flush()
    await audit_service.record_audit(
        db, action="recurring_income.create", action_label="新增固定收入",
        object_type="recurring_income", object_id=item.id,
        after={"title": item.title, "amount": fmt_amount(item.amount), "frequency": item.frequency},
        user=user, ctx=ctx,
    )
    return item


async def update_recurring_income(
    db: AsyncSession, user: CurrentUser, item_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> RecurringIncome:
    item = await get_recurring_income(db, user, item_id)
    data, field_errors = _validate(payload, partial=True)
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "固定收入信息校验失败", field_errors=field_errors)

    before = {f: getattr(item, f) for f in RECURRING_INCOME_EDITABLE}
    for field, value in data.items():
        setattr(item, field, value)
    item.version += 1
    item.updated_by = user.user_id

    await audit_service.record_audit(
        db, action="recurring_income.update", action_label="编辑固定收入",
        object_type="recurring_income", object_id=item.id,
        before=before, after={f: getattr(item, f) for f in RECURRING_INCOME_EDITABLE},
        user=user, ctx=ctx,
    )
    return item


async def pause_recurring_income(
    db: AsyncSession, user: CurrentUser, item_id: str, ctx: RequestContext | None = None,
) -> RecurringIncome:
    item = await get_recurring_income(db, user, item_id)
    item.status = "paused"
    item.version += 1
    item.updated_by = user.user_id
    await db.flush()
    await audit_service.record_audit(
        db, action="recurring_income.pause", action_label="暂停固定收入",
        object_type="recurring_income", object_id=item.id, user=user, ctx=ctx,
    )
    return item


async def resume_recurring_income(
    db: AsyncSession, user: CurrentUser, item_id: str, ctx: RequestContext | None = None,
) -> RecurringIncome:
    item = await get_recurring_income(db, user, item_id)
    item.status = "active"
    if item.next_due_date is None or to_local_naive(item.next_due_date) < local_now():
        item.next_due_date = _compute_next_due(item.frequency, item.day_of_month, local_now())
    item.version += 1
    item.updated_by = user.user_id
    await db.flush()
    await audit_service.record_audit(
        db, action="recurring_income.resume", action_label="恢复固定收入",
        object_type="recurring_income", object_id=item.id, user=user, ctx=ctx,
    )
    return item


async def delete_recurring_income(
    db: AsyncSession, user: CurrentUser, item_id: str, ctx: RequestContext | None = None,
) -> None:
    item = await get_recurring_income(db, user, item_id)
    item.deleted_at = datetime.now(timezone.utc)
    item.version += 1
    item.updated_by = user.user_id
    await db.flush()
    await audit_service.record_audit(
        db, action="recurring_income.delete", action_label="删除固定收入",
        object_type="recurring_income", object_id=item.id, user=user, ctx=ctx,
    )


async def settle_due(db: AsyncSession, user: CurrentUser) -> int:
    """把到期的固定收入自动落成一笔已确认收入事件，返回本次生成笔数。

    对称 `recurring.settle_due`；幂等：到期推进 next_due_date，同一次调用不重复生成。
    """
    now = local_now()
    items = list((await db.execute(
        select(RecurringIncome).where(
            RecurringIncome.household_id == user.household_id,
            RecurringIncome.status == "active",
            RecurringIncome.deleted_at.is_(None),
            RecurringIncome.next_due_date.is_not(None),
            RecurringIncome.next_due_date <= now,
        )
    )).scalars().all())

    from app.services import events as event_service

    created = 0
    for item in items:
        due = item.next_due_date
        event = BusinessEvent(
            id=new_id(),
            household_id=item.household_id,
            event_no=event_service._new_event_no(),
            event_type="income",
            status="confirmed",
            occurred_at=due,
            confirmed_at=now,
            amount=item.amount,
            currency=item.currency,
            original_amount=item.amount,
            original_currency=item.currency,
            base_currency=item.currency,
            conversion_status="same_currency",
            category_id=item.category_id,
            account_id=item.account_id,
            member_id=item.member_id,
            title=item.title,
            source="manual",
            note=f"固定收入自动记账（{enum_zh.zh('payment_frequency', item.frequency)}）",
            version=1,
            created_by=user.user_id,
            updated_by=user.user_id,
        )
        db.add(event)
        await db.flush()
        if item.account_id:
            db.add(EventMovement(
                id=new_id(), household_id=item.household_id, event_id=event.id,
                target_type="account", target_id=item.account_id, direction="in",
                amount=item.amount, currency=item.currency, note="固定收入",
                created_by=user.user_id, updated_by=user.user_id,
            ))
        # 推进下一个发薪日
        item.next_due_date = _compute_next_due(item.frequency, item.day_of_month, to_local_naive(due))
        item.version += 1
        item.updated_by = user.user_id
        created += 1

    await db.flush()
    return created
