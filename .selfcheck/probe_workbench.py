"""在 NAS 容器内直连服务层，打印 /dashboard/workbench 的真实返回（不经过登录）。

用途：本地验证台账总览 A+B 版的真实数据形态。
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.core.context import CurrentUser
from app.db.base import SessionLocal
from app.db.models.identity import HouseholdMember, User
from app.services.workbench import ledger_workbench


async def main() -> None:
    async with SessionLocal() as db:
        user_row = (await db.execute(select(User).limit(1))).scalars().first()
        member = (await db.execute(
            select(HouseholdMember).where(HouseholdMember.user_id == user_row.id).limit(1)
        )).scalars().first() if user_row else None
        if user_row is None or member is None:
            print(json.dumps({"error": "no user/member"}, ensure_ascii=False))
            return
        user = CurrentUser(
            user_id=user_row.id,
            username=user_row.username,
            display_name=user_row.display_name or user_row.username,
            is_system_admin=True,
            household_id=member.household_id,
        )
        data = await ledger_workbench(db, user)
        print(json.dumps(data, ensure_ascii=False, indent=1, default=str))


asyncio.run(main())
