"""公积金账户的缴存入账与到期自动记账。

缴存是「钱进公积金账户」，但属于专项住房资金、不是个人可支配收入，
因此用独立事件类型 provident_fund_deposit（counts=False），
与工资收入区分开，避免月结余被重复抬高。
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser
from app.core.ids import new_id
from app.core.timeutil import local_now
from app.db.models.business import Account, BusinessEvent, EventMovement

logger = logging.getLogger(__name__)

DEPOSIT_EVENT_TYPE = "provident_fund_deposit"


async def create_deposit(
    db: AsyncSession,
    account: Account,
    amount: Decimal,
    occurred_at: datetime,
    user_id: str | None,
    note: str | None = None,
) -> BusinessEvent:
    """记一笔缴存：账户余额增加，不计入收支统计。"""
    event = BusinessEvent(
        id=new_id(),
        household_id=account.household_id,
        event_no=f"EV{local_now().strftime('%Y%m%d')}{new_id()[:6].upper()}",
        event_type=DEPOSIT_EVENT_TYPE,
        status="confirmed",
        occurred_at=occurred_at,
        confirmed_at=occurred_at,
        amount=amount,
        currency=account.currency,
        original_amount=amount,
        original_currency=account.currency,
        base_currency=account.currency,
        conversion_status="same_currency",
        account_id=account.id,
        title="公积金缴存",
        source="manual" if user_id else "auto",
        note=note,
        version=1,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(event)
    await db.flush()
    db.add(
        EventMovement(
            id=new_id(),
            household_id=account.household_id,
            event_id=event.id,
            target_type="account",
            target_id=account.id,
            direction="in",
            amount=amount,
            currency=account.currency,
            note="公积金缴存",
            created_by=user_id,
            updated_by=user_id,
        )
    )
    await db.flush()
    return event


async def settle_due_deposits(db: AsyncSession, user: CurrentUser) -> int:
    """到缴存日自动记一笔缴存。

    幂等：本月已有缴存事件就跳过，不依赖进程重启次数。
    过了当月缴存日才补记，且只补当月——停机多日不会一次性补出好几笔。
    """
    today = local_now()
    accounts = list(
        (
            await db.execute(
                select(Account).where(
                    Account.household_id == user.household_id,
                    Account.account_type == "provident_fund",
                    Account.deleted_at.is_(None),
                    Account.status == "active",
                    Account.auto_deposit_amount.is_not(None),
                )
            )
        ).scalars().all()
    )

    created = 0
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for account in accounts:
        amount = Decimal(str(account.auto_deposit_amount or 0))
        if amount <= 0:
            continue
        day = int(account.auto_deposit_day or 15)
        if today.day < day:
            continue
        exists = (
            await db.execute(
                select(func.count())
                .select_from(BusinessEvent)
                .where(
                    BusinessEvent.account_id == account.id,
                    BusinessEvent.event_type == DEPOSIT_EVENT_TYPE,
                    BusinessEvent.status == "confirmed",
                    BusinessEvent.occurred_at >= month_start,
                )
            )
        ).scalar() or 0
        if exists:
            continue
        occurred = today.replace(day=day, hour=9, minute=0, second=0, microsecond=0)
        await create_deposit(db, account, amount, occurred, user.user_id, note="到期自动记账")
        created += 1
        logger.info("公积金自动缴存 %s：%s", account.name, amount)
    return created
