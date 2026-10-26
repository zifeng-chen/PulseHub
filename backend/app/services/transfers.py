"""内部转账领域服务（06 §4、01 §4、18 §4）。

口径约束：
- 内部转账**只改变账户余额，不改变收入和支出**；
- 转出账户与转入账户不能相同；
- 一笔转账在同一事务内生成两条业务事件（转出/转入）并绑定为一个转账组；
- 手续费属于支出，单独生成一条支出事件，计入支出统计；
- 跨币种转账必须由用户显式提供汇率与到账金额，系统不会默认为 1。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.money import fmt_amount
from app.db.models.business import (
    Account,
    BusinessEvent,
    EventMovement,
    TransferGroup,
)
from app.services import audit as audit_service
from app.services import catalog as catalog_service


def _new_group_no() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"TR{stamp}{new_id()[:6].upper()}"


def _new_event_no() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"EV{stamp}{new_id()[:6].upper()}"


def serialize_transfer(
    group: TransferGroup,
    from_account: Account | None = None,
    to_account: Account | None = None,
) -> dict:
    from app.core import enums as enum_zh

    return {
        "id": group.id,
        "group_no": group.group_no,
        "status": group.status,
        "status_label": enum_zh.zh("transfer_status", group.status),
        "from_account_id": group.from_account_id,
        "from_account_name": from_account.name if from_account else None,
        "to_account_id": group.to_account_id,
        "to_account_name": to_account.name if to_account else None,
        "amount": fmt_amount(group.amount or 0),
        "fee": fmt_amount(group.fee or 0),
        "currency": group.currency,
        "base_currency": group.base_currency or group.currency,
        "exchange_rate": f"{Decimal(str(group.exchange_rate)):.8f}" if group.exchange_rate else None,
        "conversion_status": group.conversion_status,
        "occurred_at": local_iso(group.occurred_at) if group.occurred_at else None,
        "confirmed_at": utc_iso(group.confirmed_at) if group.confirmed_at else None,
        "out_event_id": group.out_event_id,
        "in_event_id": group.in_event_id,
        "fee_event_id": group.fee_event_id,
        "note": group.note,
        "version": group.version,
        "created_at": utc_iso(group.created_at),
    }


async def _load_account(db: AsyncSession, household_id: str, account_id: str, label: str) -> Account:
    account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
    if account is None or account.deleted_at is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"{label}不存在",
                       field_errors={label: "账户不存在"})
    if account.household_id != household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    if account.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, f"{label}已停用，不能用于转账",
                       field_errors={label: "账户已停用"})
    return account


async def create_transfer(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None
) -> tuple[TransferGroup, Account, Account]:
    """创建并确认一笔内部转账（事务内生成转账组 + 转出/转入事件 + 可选手续费支出事件）。"""
    field_errors: dict[str, str] = {}

    from_id = payload.get("from_account_id")
    to_id = payload.get("to_account_id")
    if not from_id:
        field_errors["from_account_id"] = "请选择转出账户"
    if not to_id:
        field_errors["to_account_id"] = "请选择转入账户"
    if from_id and to_id and from_id == to_id:
        field_errors["to_account_id"] = "转出账户与转入账户不能相同"
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "转账信息不完整", field_errors=field_errors)

    try:
        amount = Decimal(str(payload.get("amount") or "0"))
        if not amount.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额格式不正确",
                       field_errors={"amount": "金额必须是数字"})
    if amount <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "转账金额必须大于 0",
                       field_errors={"amount": "转账金额必须大于 0"})

    fee = Decimal("0")
    if payload.get("fee") not in (None, ""):
        try:
            fee = Decimal(str(payload["fee"]))
            if not fee.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "手续费格式不正确",
                           field_errors={"fee": "手续费必须是数字"})
        if fee < 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "手续费不能为负数",
                           field_errors={"fee": "手续费必须大于等于 0"})

    occurred_at = datetime.now(timezone.utc)
    if payload.get("occurred_at"):
        try:
            occurred_at = to_local_naive(datetime.fromisoformat(str(payload["occurred_at"]).replace("Z", "+00:00")))
        except ValueError:
            raise AppError(ErrorCode.VALIDATION_ERROR, "日期格式不正确",
                           field_errors={"occurred_at": "日期格式应为 ISO 8601"})

    household_id = user.household_id or ""
    from_acc = await _load_account(db, household_id, from_id, "from_account_id")
    to_acc = await _load_account(db, household_id, to_id, "to_account_id")

    # 跨币种：必须由用户显式给出到账金额与汇率，系统不默认按 1 换算（01 §4）
    same_currency = from_acc.currency == to_acc.currency
    to_amount = amount
    rate: Decimal | None = None
    if same_currency:
        conversion_status = "same_currency"
    else:
        if payload.get("to_amount") in (None, "") or payload.get("exchange_rate") in (None, ""):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "跨币种转账需要你确认到账金额与汇率，系统不会默认按 1 换算",
                field_errors={
                    "to_amount": "请填写对方账户实际到账金额",
                    "exchange_rate": "请填写本次使用的汇率",
                },
            )
        try:
            to_amount = Decimal(str(payload["to_amount"]))
            rate = Decimal(str(payload["exchange_rate"]))
            if not rate.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "跨币种金额或汇率格式不正确")
        if to_amount <= 0 or rate <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "到账金额与汇率必须大于 0")
        conversion_status = "converted"

    group = TransferGroup(
        id=new_id(),
        household_id=household_id,
        group_no=_new_group_no(),
        status="confirmed",
        from_account_id=from_acc.id,
        to_account_id=to_acc.id,
        amount=amount,
        fee=fee,
        currency=from_acc.currency,
        original_amount=amount,
        original_currency=from_acc.currency,
        converted_amount=to_amount if not same_currency else None,
        base_currency=from_acc.currency,
        exchange_rate=rate,
        rate_date=occurred_at if rate else None,
        rate_source="用户确认" if rate else None,
        conversion_status=conversion_status,
        occurred_at=occurred_at,
        confirmed_at=occurred_at,
        member_id=payload.get("member_id"),
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(group)
    await db.flush()

    # 转出事件
    out_event = BusinessEvent(
        id=new_id(), household_id=household_id, event_no=_new_event_no(),
        event_type="transfer", status="confirmed", occurred_at=occurred_at,
        confirmed_at=occurred_at, amount=amount, currency=from_acc.currency,
        original_amount=amount, original_currency=from_acc.currency,
        base_currency=from_acc.currency, conversion_status="same_currency",
        account_id=from_acc.id, member_id=payload.get("member_id"),
        title=f"内部转账 · 转出至{to_acc.name}",
        counterparty=to_acc.name, source="manual",
        note=payload.get("note"), version=1,
        created_by=user.user_id, updated_by=user.user_id,
    )
    # 转入事件
    in_event = BusinessEvent(
        id=new_id(), household_id=household_id, event_no=_new_event_no(),
        event_type="transfer", status="confirmed", occurred_at=occurred_at,
        confirmed_at=occurred_at, amount=to_amount, currency=to_acc.currency,
        original_amount=to_amount, original_currency=to_acc.currency,
        converted_amount=to_amount if not same_currency else None,
        exchange_rate=rate, base_currency=to_acc.currency,
        conversion_status=conversion_status,
        account_id=to_acc.id, member_id=payload.get("member_id"),
        title=f"内部转账 · 转入自{from_acc.name}",
        counterparty=from_acc.name, source="manual",
        note=payload.get("note"), version=1,
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add_all([out_event, in_event])
    await db.flush()

    group.out_event_id = out_event.id
    group.in_event_id = in_event.id

    movements = [
        EventMovement(
            id=new_id(), household_id=household_id, event_id=out_event.id,
            target_type="account", target_id=from_acc.id, direction="out",
            amount=amount, currency=from_acc.currency,
            note="转账转出", created_by=user.user_id, updated_by=user.user_id,
        ),
        EventMovement(
            id=new_id(), household_id=household_id, event_id=in_event.id,
            target_type="account", target_id=to_acc.id, direction="in",
            amount=to_amount, currency=to_acc.currency,
            note="转账转入", created_by=user.user_id, updated_by=user.user_id,
        ),
    ]

    # 手续费单独记为一笔支出（01 §4：手续费计入支出，但不影响转账本金口径）
    fee_event = None
    if fee > 0:
        # 手续费是系统自动记的支出，同样要有分类，避免出现在「未分类」里
        fee_category = await catalog_service.ensure_category_id(
            db, household_id, "expense", "手续费", parent_name="利息与费用",
        )
        fee_event = BusinessEvent(
            id=new_id(), household_id=household_id, event_no=_new_event_no(),
            event_type="expense", status="confirmed", occurred_at=occurred_at,
            confirmed_at=occurred_at, amount=fee, currency=from_acc.currency,
            original_amount=fee, original_currency=from_acc.currency,
            base_currency=from_acc.currency, conversion_status="same_currency",
            account_id=from_acc.id, member_id=payload.get("member_id"),
            category_id=fee_category,
            title=f"转账手续费 · {to_acc.name}", source="manual",
            note="内部转账手续费", version=1,
            created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(fee_event)
        await db.flush()
        group.fee_event_id = fee_event.id
        movements.append(
            EventMovement(
                id=new_id(), household_id=household_id, event_id=fee_event.id,
                target_type="account", target_id=from_acc.id, direction="out",
                amount=fee, currency=from_acc.currency,
                note="转账手续费", created_by=user.user_id, updated_by=user.user_id,
            )
        )

    db.add_all(movements)

    await audit_service.record_audit(
        db,
        action="transfer.create",
        action_label="内部转账",
        object_type="transfer",
        object_id=group.id,
        related_object_id=to_acc.id,
        after={
            "group_no": group.group_no,
            "from_account_id": from_acc.id,
            "to_account_id": to_acc.id,
            "amount": amount,
            "fee": fee,
            "currency": group.currency,
            "conversion_status": conversion_status,
            "status": group.status,
        },
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return group, from_acc, to_acc


async def get_transfer(
    db: AsyncSession, user: CurrentUser, group_id: str
) -> tuple[TransferGroup, Account | None, Account | None]:
    """按转账组 id 取详情（供幂等重放返回原记录）。"""
    group = (await db.execute(
        select(TransferGroup).where(
            TransferGroup.id == group_id,
            TransferGroup.household_id == user.household_id,
        )
    )).scalars().first()
    if group is None:
        raise AppError(ErrorCode.NOT_FOUND, "转账记录不存在", status_code=404)
    accounts = {
        a.id: a for a in (await db.execute(
            select(Account).where(Account.household_id == user.household_id)
        )).scalars().all()
    }
    return group, accounts.get(group.from_account_id), accounts.get(group.to_account_id)


async def list_transfers(
    db: AsyncSession, user: CurrentUser, *, page: int = 1, page_size: int = 20,
    date_from: str | None = None, date_to: str | None = None,
    account_id: str | None = None, keyword: str | None = None,
) -> tuple[list[tuple[TransferGroup, Account | None, Account | None]], int]:
    """转账列表（支持日期区间、账户、单号/备注关键字筛选）。

    转账不走 business_events 的流水筛选，之前这一页完全没有筛选条件，
    只能一页页翻，所以这里单独补上同等的筛选能力。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import func as sql_func
    from sqlalchemy import or_

    from app.core.timeutil import to_local_naive

    conditions = [TransferGroup.household_id == user.household_id]

    def _day(value: str | None, *, end: bool = False) -> datetime | None:
        if not value:
            return None
        try:
            return to_local_naive(value) or datetime.fromisoformat(value[:19])
        except Exception:  # noqa: BLE001
            return None

    start = _day(date_from)
    finish = _day(date_to)
    if start is not None:
        conditions.append(TransferGroup.occurred_at >= start)
    if finish is not None:
        # 结束日包含当天：用「次日 0 点前」比较
        conditions.append(TransferGroup.occurred_at < finish + timedelta(days=1))
    if account_id:
        conditions.append(or_(TransferGroup.from_account_id == account_id,
                              TransferGroup.to_account_id == account_id))
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(TransferGroup.group_no.ilike(like),
                              TransferGroup.note.ilike(like)))

    base = select(TransferGroup).where(*conditions)
    total = int(
        (await db.execute(select(sql_func.count(TransferGroup.id)).where(*conditions))).scalar_one()
    )
    rows = list(
        (await db.execute(
            base.order_by(TransferGroup.occurred_at.desc(), TransferGroup.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    accounts = {
        a.id: a
        for a in (await db.execute(
            select(Account).where(Account.household_id == user.household_id)
        )).scalars().all()
    }
    return [(r, accounts.get(r.from_account_id), accounts.get(r.to_account_id)) for r in rows], total


async def reverse_transfer(
    db: AsyncSession, user: CurrentUser, group_id: str, reason: str,
    ctx: RequestContext | None = None,
) -> TransferGroup:
    """冲销转账：冲销组内全部事件，余额随之回退；历史保留。"""
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "冲销必须填写原因",
                       field_errors={"reason": "请填写冲销原因"})
    group = (
        await db.execute(select(TransferGroup).where(TransferGroup.id == group_id))
    ).scalars().first()
    if group is None or group.household_id != user.household_id:
        raise AppError(ErrorCode.NOT_FOUND, "转账记录不存在", status_code=404)
    if group.status != "confirmed":
        raise AppError(ErrorCode.STATE_INVALID, "只有已确认的转账可以冲销", status_code=409)

    event_ids = [i for i in (group.out_event_id, group.in_event_id, group.fee_event_id) if i]
    events = list(
        (await db.execute(select(BusinessEvent).where(BusinessEvent.id.in_(event_ids))))
        .scalars().all()
    )
    now = datetime.now(timezone.utc)
    for event in events:
        if event.status != "confirmed":
            continue
        event.status = "reversed"
        event.version += 1
        event.updated_by = user.user_id

    before = {"status": group.status}
    group.status = "reversed"
    group.version += 1
    group.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="transfer.reverse",
        action_label="冲销内部转账",
        object_type="transfer",
        object_id=group.id,
        before=before,
        after={"status": group.status, "reversed_events": len(events)},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return group
