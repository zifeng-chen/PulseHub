"""在 NAS 容器内直连服务层，排查「账户余额合计为负」的构成。

用途：确认 -47,806.31 是否由信用卡负余额口径引起，以及信用卡是否同时被
重复计入负债模块。
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from sqlalchemy import select, func

from app.core.context import CurrentUser
from app.db.base import SessionLocal
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.finance import Liability
from app.db.models.identity import Household, HouseholdMember, User
from app.services.accounts import balances_for


async def main() -> None:
    out: dict = {}
    async with SessionLocal() as db:
        household = (await db.execute(select(Household).limit(1))).scalars().first()
        if household is None:
            print(json.dumps({"error": "no household"}, ensure_ascii=False))
            return
        hh_id = household.id
        out["household"] = household.name

        # 账户列表与余额
        accounts = list((await db.execute(
            select(Account).where(Account.household_id == hh_id, Account.deleted_at.is_(None))
        )).scalars().all())
        balances = await balances_for(db, hh_id, [a.id for a in accounts])

        acc_rows = []
        total = Decimal("0")
        for a in accounts:
            bal = balances.get(a.id, Decimal("0"))
            total += bal
            acc_rows.append({
                "name": a.name,
                "type": a.account_type,
                "currency": a.currency,
                "opening_balance": str(a.opening_balance),
                "balance": str(bal),
                "credit_limit": str(a.credit_limit) if a.credit_limit is not None else None,
                "include_in_net_worth": a.include_in_net_worth,
            })
        out["accounts_total"] = str(total)
        out["accounts"] = acc_rows

        # 负债模块（信用卡应不在其中；信用卡欠款由账户余额表达）
        liabilities = list((await db.execute(
            select(Liability).where(Liability.household_id == hh_id, Liability.deleted_at.is_(None))
        )).scalars().all())
        out["liabilities"] = [
            {"name": l.name, "type": l.liability_type,
             "opening_outstanding": str(l.opening_outstanding) if l.opening_outstanding is not None else None,
             "initial_amount": str(l.initial_amount),
             "include_in_net_worth": l.include_in_net_worth}
            for l in liabilities
        ]

        # 是否有信用卡类型负债（若存在即口径冲突）
        cc_liab = [l for l in liabilities if l.liability_type == "credit_card"]
        out["credit_card_liabilities_count"] = len(cc_liab)

        # 账户里信用卡/消费信贷账户的余额明细（负值即欠款）
        out["credit_accounts"] = [
            {"name": a.name, "type": a.account_type, "balance": str(balances.get(a.id, Decimal("0")))}
            for a in accounts
            if a.account_type in ("credit_card", "e_wallet") or a.credit_limit is not None
        ]

    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


asyncio.run(main())
