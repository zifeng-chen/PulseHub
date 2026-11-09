"""补录公积金缴存（2025-06 ~ 2026-10，每月 2,400）。

缴存是「钱进公积金账户」，但不属于个人可支配收入，因此用独立事件类型
provident_fund_deposit（不在收入/支出口径内）。补完把账户期初重算，
使当前余额落在目标值上。

    docker exec -i -e PF_DEPOSIT=2400 -e PF_TARGET=65000 PulseHub python - < deploy/backfill_pf_deposit.py

幂等：已有缴存事件时直接退出。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.core.ids import new_id
from app.core.timeutil import local_now
from app.db.base import SessionLocal
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.identity import User

DEPOSIT = Decimal(os.environ.get("PF_DEPOSIT", "2400") or "2400")
TARGET = Decimal(os.environ.get("PF_TARGET", "65000") or "65000")
DAY = 15
FIRST = (2025, 6)
PERIODS = 17


async def main() -> None:
    async with SessionLocal() as db:
        acc = (
            await db.execute(
                select(Account).where(
                    Account.account_type == "provident_fund", Account.deleted_at.is_(None)
                )
            )
        ).scalars().first()
        if acc is None:
            print("没有公积金账户，终止")
            return

        exists = (
            await db.execute(
                select(func.count())
                .select_from(BusinessEvent)
                .where(
                    BusinessEvent.event_type == "provident_fund_deposit",
                    BusinessEvent.account_id == acc.id,
                )
            )
        ).scalar() or 0
        if exists:
            print(f"已有 {exists} 笔缴存记录，终止（避免重复）")
            return

        owner = (await db.execute(select(User))).scalars().first()
        if owner is None:
            print("找不到用户，终止")
            return

        y, m = FIRST
        for _ in range(PERIODS):
            occurred_text = f"{y:04d}-{m:02d}-{DAY:02d}T09:00:00"
            occurred = datetime.fromisoformat(occurred_text)
            event = BusinessEvent(
                id=new_id(), household_id=acc.household_id,
                event_no=f"EV{local_now().strftime('%Y%m%d')}{new_id()[:6].upper()}",
                event_type="provident_fund_deposit", status="confirmed",
                occurred_at=occurred, confirmed_at=occurred,
                amount=DEPOSIT, currency=acc.currency,
                original_amount=DEPOSIT, original_currency=acc.currency,
                base_currency=acc.currency, conversion_status="same_currency",
                account_id=acc.id, title="公积金缴存", source="manual",
                note="按每月缴存额补录", version=1,
                created_by=owner.id, updated_by=owner.id,
            )
            db.add(event)
            await db.flush()
            db.add(
                EventMovement(
                    id=new_id(), household_id=acc.household_id, event_id=event.id,
                    target_type="account", target_id=acc.id, direction="in",
                    amount=DEPOSIT, currency=acc.currency, note="公积金缴存",
                    created_by=owner.id, updated_by=owner.id,
                )
            )
            print(f"  + {y}-{m:02d} 缴存 {DEPOSIT}")
            m += 1
            if m > 12:
                y, m = y + 1, 1

        await db.flush()

        # 重算期初：让当前余额等于目标值
        net = Decimal("0")
        rows = (
            await db.execute(
                select(EventMovement.direction, func.coalesce(func.sum(EventMovement.amount), 0))
                .where(EventMovement.target_type == "account", EventMovement.target_id == acc.id)
                .group_by(EventMovement.direction)
            )
        ).all()
        for direction, total in rows:
            net += Decimal(str(total)) if direction == "in" else -Decimal(str(total))

        old = Decimal(str(acc.opening_balance or 0))
        acc.opening_balance = TARGET - net
        acc.version += 1
        await db.commit()
        print(f"期初 {old} → {acc.opening_balance}；累计变动 {net}；当前余额 {TARGET}")


asyncio.run(main())
