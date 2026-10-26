"""账户对账服务（06 §5、18 §4 顺序 7）。

规则：
- 对账不直接覆盖系统余额：差额通过创建「对账调整事件」（income/expense，source=reconciliation）
  并确认后，由资金变动使系统余额对齐人工实际余额；
- 系统余额 = 期初余额 + 截至截止日的已确认资金变动净额；
- 标记已核对时更新账户 last_reconciled_at；
- 差额为 0 时仅标记已核对，不生成调整事件。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import utc_iso
from app.core.money import fmt_amount
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.services import audit as audit_service
from app.services import events as event_service


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


async def _get_account(db: AsyncSession, user: CurrentUser, account_id: str) -> Account:
    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
    if account is None or account.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "账户不存在", status_code=404)
    if account.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return account


async def _movements_as_of(db: AsyncSession, account: Account, as_of: datetime) -> Decimal:
    """截至截止日的已确认资金变动净额。"""
    rows = (await db.execute(
        select(EventMovement.direction, func.coalesce(func.sum(EventMovement.amount), 0))
        .join(BusinessEvent, BusinessEvent.id == EventMovement.event_id)
        .where(
            EventMovement.target_type == "account",
            EventMovement.target_id == account.id,
            EventMovement.household_id == account.household_id,
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.occurred_at <= as_of,
        )
        .group_by(EventMovement.direction)
    )).all()
    net = Decimal("0")
    for direction, total in rows:
        amount = _dec(total)
        net += amount if direction == "in" else -amount
    return net


async def preview(
    db: AsyncSession, user: CurrentUser, account_id: str, as_of: str | None,
) -> dict:
    account = await _get_account(db, user, account_id)
    cutoff = _parse_dt(as_of, "as_of") or local_now()

    net = await _movements_as_of(db, account, cutoff)
    system_balance = _dec(account.opening_balance) + net

    # 未匹配流水：该账户截至截止日的已确认事件，供人工比对
    events = list((await db.execute(
        select(BusinessEvent)
        .where(
            BusinessEvent.household_id == account.household_id,
            BusinessEvent.account_id == account.id,
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.occurred_at <= cutoff,
        )
        .order_by(BusinessEvent.occurred_at.desc())
        .limit(100)
    )).scalars().all())

    return {
        "account_id": account.id,
        "account_name": account.name,
        "currency": account.currency,
        "opening_balance": fmt_amount(account.opening_balance),
        "as_of": local_iso(cutoff),
        "system_balance": fmt_amount(system_balance),
        "last_reconciled_at": local_iso(account.last_reconciled_at) if account.last_reconciled_at else None,
        "unmatched_events": [
            {
                "id": e.id,
                "event_no": e.event_no,
                "event_type": e.event_type,
                "event_type_label": event_service.serialize_event(e)["event_type_label"],
                "title": e.title,
                "amount": fmt_amount(e.amount),
                "currency": e.currency,
                "occurred_at": local_iso(e.occurred_at) if e.occurred_at else None,
                "status": e.status,
            }
            for e in events
        ],
        "caliber": "系统余额 = 期初余额 + 截至截止日的已确认资金变动净额；对账不直接覆盖系统余额",
    }


async def reconcile(
    db: AsyncSession,
    user: CurrentUser,
    account_id: str,
    payload: dict,
    ctx: RequestContext | None = None,
) -> dict:
    """核对并（如需）创建调整事件，使系统余额对齐人工实际余额。"""
    account = await _get_account(db, user, account_id)
    if account.status != "active":
        raise AppError(ErrorCode.STATE_INVALID, "已停用账户不能对账", status_code=409)

    cutoff = _parse_dt(payload.get("as_of"), "as_of") or local_now()
    try:
        actual = Decimal(str(payload.get("actual_balance")))
        if not actual.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "实际余额格式不正确",
                       field_errors={"actual_balance": "请输入正确的实际余额"})
    reason = (payload.get("reason") or "").strip()

    net = await _movements_as_of(db, account, cutoff)
    system_balance = _dec(account.opening_balance) + net
    diff = actual - system_balance

    adjustment_event = None
    if diff != 0:
        if not reason:
            raise AppError(ErrorCode.VALIDATION_ERROR, "存在差额时必须填写调整原因",
                           field_errors={"reason": "请填写差额原因"})
        event_type = "income" if diff > 0 else "expense"
        amount = abs(diff)
        event = await event_service.create_draft(db, user, {
            "event_type": event_type,
            "title": f"对账调整 · {account.name}",
            "amount": fmt_amount(amount),
            "currency": account.currency,
            "occurred_at": local_iso(cutoff),
            "account_id": account.id,
            "note": (
                f"对账调整：截止 {cutoff.strftime('%Y-%m-%d')}，系统余额 "
                f"{fmt_amount(system_balance)}，人工实际余额 {fmt_amount(actual)}，"
                f"差额 {fmt_amount(diff, signed=True)}"
                + (f"；原因：{reason}" if reason else "")
            ),
            "source": "reconciliation",
        }, ctx)
        await event_service.confirm_event(db, user, event.id, None, ctx)
        adjustment_event = event_service.serialize_event(event)

    before = account.last_reconciled_at
    account.last_reconciled_at = cutoff
    account.version += 1
    account.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="reconciliation.complete",
        action_label="账户对账",
        object_type="account",
        object_id=account.id,
        before={"last_reconciled_at": before, "system_balance": fmt_amount(system_balance)},
        after={"last_reconciled_at": cutoff, "actual_balance": fmt_amount(actual),
               "diff": fmt_amount(diff), "adjusted": diff != 0},
        reason=reason or None,
        user=user,
        ctx=ctx,
    )
    return {
        "account_id": account.id,
        "account_name": account.name,
        "currency": account.currency,
        "as_of": local_iso(cutoff),
        "system_balance_before": fmt_amount(system_balance),
        "actual_balance": fmt_amount(actual),
        "diff": fmt_amount(diff),
        "adjusted": diff != 0,
        "adjustment_event": adjustment_event,
        "last_reconciled_at": local_iso(account.last_reconciled_at),
    }
