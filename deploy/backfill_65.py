"""一次性补录 65 号公积金贷款：17 期历史 + 2026-10 提前还款 165,000。

必须在容器内执行（要用 app.* 与数据库连接）：

    docker exec -i -e PF_OPENING=123456.78 PulseHub python - < deploy/backfill_65.py

环境变量：
    PF_OPENING      公积金账户在 2025-06 之前的期初余额（元）。账户不存在时用它建账。
    PF_PREPAY_FROM  提前还款的资金来源：provident（默认，公积金账户）/ bank（房贷还款卡）
    DRY_RUN         1 = 只打印将要写入的内容，不落库

幂等：65 号已有还款记录时直接退出，不会重复补录。
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select

from app.core.context import CurrentUser
from app.core.ids import new_id
from app.db.base import SessionLocal
from app.db.models.business import Account
from app.db.models.finance import Liability, LiabilityRepayment, Mortgage
from app.db.models.identity import User
from app.services import liabilities as L

PRINCIPAL = Decimal("500000")
RATE = Decimal("0.026") / 12
PAYMENT = Decimal("2096.45")
CENT = Decimal("0.01")

OPENING = Decimal(os.environ.get("PF_OPENING", "0") or "0")
PREPAY_FROM = (os.environ.get("PF_PREPAY_FROM") or "provident").strip().lower()
DRY_RUN = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")

FIRST_YEAR, FIRST_MONTH = 2025, 6
PERIODS = 17                      # 2025-06 ~ 2026-10
PREPAY = Decimal("165000")
REMAINING_PERIODS = 184           # 提前还款 + 月供不变 → 184 期


def _months() -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = FIRST_YEAR, FIRST_MONTH
    for _ in range(PERIODS):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def build_schedule() -> list[dict[str, Any]]:
    """按等额本息逐月摊销，利息按分位四舍五入（与银行账单口径一致）。"""
    bal = PRINCIPAL
    rows: list[dict[str, Any]] = []
    for y, m in _months():
        interest = (bal * RATE).quantize(CENT, rounding=ROUND_HALF_UP)
        principal = PAYMENT - interest
        bal -= principal
        rows.append({
            "year": y, "month": m, "principal": principal, "interest": interest,
            "amount": PAYMENT, "balance": bal,
        })
    return rows


async def main() -> None:
    rows = build_schedule()
    print(f"摊销表：{len(rows)} 期，期末余额 {rows[-1]['balance']}")
    print(f"提前还款 {PREPAY} 后余额 {rows[-1]['balance'] - PREPAY}")
    if DRY_RUN:
        for r in rows[:3]:
            print(f"  {r['year']}-{r['month']:02d} 本金 {r['principal']} 利息 {r['interest']}")
        print("DRY_RUN：未写库")
        return

    async with SessionLocal() as db:
        liability = (
            await db.execute(
                select(Liability).where(Liability.name == "65号", Liability.deleted_at.is_(None))
            )
        ).scalars().first()
        if liability is None:
            print("找不到 65 号，终止")
            return

        existing = (
            await db.execute(
                select(LiabilityRepayment).where(
                    LiabilityRepayment.liability_id == liability.id,
                    LiabilityRepayment.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        if existing:
            print(f"65 号已有 {len(existing)} 条还款记录，终止（避免重复补录）")
            return

        owner = (
            await db.execute(select(User).where(User.id == liability.created_by))
        ).scalars().first()
        if owner is None:
            owner = (await db.execute(select(User))).scalars().first()
        user = CurrentUser(
            user_id=owner.id, username=owner.username, display_name=owner.display_name,
            is_system_admin=True, household_id=liability.household_id, role="system_admin",
        )

        async def find_account(account_type: str, name: str | None = None) -> Account | None:
            stmt = select(Account).where(
                Account.household_id == liability.household_id,
                Account.account_type == account_type,
                Account.deleted_at.is_(None),
            )
            if name:
                stmt = stmt.where(Account.name == name)
            return (await db.execute(stmt)).scalars().first()

        pf = await find_account("provident_fund")
        if pf is None:
            pf = Account(
                id=new_id(), household_id=liability.household_id, name="公积金账户",
                account_type="provident_fund", currency="CNY",
                opening_balance=OPENING, include_in_net_worth=True, status="active",
                version=1, created_by=user.user_id, updated_by=user.user_id,
            )
            db.add(pf)
            await db.flush()
            print(f"已创建公积金账户，期初 {OPENING}")
        else:
            print(f"复用公积金账户 {pf.name}")

        prepay_account = pf
        if PREPAY_FROM == "bank":
            bank = await find_account("bank", "房贷还款卡")
            if bank is None:
                print("找不到「房贷还款卡」，终止")
                return
            prepay_account = bank

        # 1) 修正建档信息：start_date 现在错写成 2026-05-09
        liability.start_date = liability.start_date.replace(year=2025, month=5, day=9)
        liability.first_payment_date = liability.first_payment_date or liability.start_date.replace(
            year=2025, month=6, day=9
        )
        if liability.first_payment_date.year != 2025:
            liability.first_payment_date = liability.first_payment_date.replace(
                year=2025, month=6, day=9
            )
        liability.term_months = 336
        liability.payment_day = 9
        liability.monthly_payment = PAYMENT
        liability.repayment_method = "equal_installment"
        liability.opening_outstanding = None      # 剩余本金改由事件推算
        liability.next_payment_date = None        # 补录过程中由 add_repayment 顺延
        liability.auto_repay = True
        liability.linked_account_id = pf.id
        liability.status = "active"

        mortgage = (
            await db.execute(select(Mortgage).where(Mortgage.liability_id == liability.id))
        ).scalars().first()
        if mortgage is not None:
            mortgage.loan_method = "provident_fund"

        await db.flush()

        # 2) 补录 17 期
        for r in rows:
            occurred = f"{r['year']:04d}-{r['month']:02d}-09T00:00:00"
            await L.add_repayment(db, user, liability.id, {
                "amount": str(r["amount"]),
                "principal": str(r["principal"]),
                "interest": str(r["interest"]),
                "account_id": pf.id,
                "occurred_at": occurred,
                "reason": "按合同等额本息摊销表补录历史还款",
            })
            print(f"  + {occurred[:7]} 本金 {r['principal']} 利息 {r['interest']}")

        # 3) 提前还款（只冲本金，不顺延月供）
        await L.add_repayment(db, user, liability.id, {
            "amount": str(PREPAY),
            "principal": str(PREPAY),
            "interest": "0",
            "account_id": prepay_account.id,
            "occurred_at": "2026-10-09T00:00:00",
            "prepay": True,
            "reason": "提前还款：月供不变、缩短年限",
            "note": "提前还款 165000，月供不变缩短年限",
        })
        print(f"  + 2026-10 提前还款 {PREPAY}（{prepay_account.name}）")

        # 4) 收口：剩余 184 期，下期 2026-11-09
        liability.remaining_periods = REMAINING_PERIODS
        liability.next_payment_date = liability.first_payment_date.replace(
            year=2026, month=11, day=9
        )
        liability.version += 1
        await db.commit()
        print(f"完成：剩余期数 {REMAINING_PERIODS}，下次还款日 2026-11-09")


asyncio.run(main())
