"""信用卡积分余额快照（来源：招商「每日信用管家」邮件）。

招行邮件每天都会写「积分余额 X」，但那是**客户名下所有卡的合计**，不分单卡；
邮件也不给「兑换了什么」的明细。所以这里只做两件事：

1. 把每天邮件里的积分余额存成快照，抵扣时提示「当前积分」；
2. 相邻两天积分骤降（默认 ≥500 分）时给出提示，提醒可能是积分兑换了抵扣金。

积分不参与任何账务计算，也不与人民币换算——各行、各活动的兑换比例都不一样，
硬换算只会给出错误数字。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.timeutil import local_iso
from app.db.models.finance import CardRewardSnapshot

POINTS_RE = re.compile(r"积分余额\s*([\d,]+)")
AVAILABLE_RE = re.compile(r"可用额度\s*[￥¥]?\s*([\d,]+\.\d{2})")

# 积分骤降阈值：低于这个值多半是正常消费累积/零星到期，不必打扰
DROP_THRESHOLD = 500


def parse_points(body: str) -> int | None:
    match = POINTS_RE.search(body or "")
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_available_limit(body: str) -> Decimal | None:
    """邮件里的「可用额度」是**多卡合计**，只作参考，不能写回单卡额度。"""
    match = AVAILABLE_RE.search(body or "")
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except Exception:  # noqa: BLE001
        return None


def serialize(row: CardRewardSnapshot) -> dict:
    return {
        "id": row.id,
        "snapshot_date": local_iso(row.snapshot_date) if row.snapshot_date else None,
        "points": row.points,
        "available_limit": f"{Decimal(str(row.available_limit or 0)):.2f}",
        "source": row.source,
    }


async def save_snapshot(
    db: AsyncSession, household_id: str, snapshot_date: datetime,
    points: int, available_limit: Decimal | None = None,
    source: str = "email", source_uid: str | None = None,
) -> CardRewardSnapshot:
    """同一天只保留一条：后拉的覆盖先拉的（当天积分还会继续变）。"""
    day_start = snapshot_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    row = (await db.execute(
        select(CardRewardSnapshot).where(
            CardRewardSnapshot.household_id == household_id,
            CardRewardSnapshot.provider == "cmb",
            CardRewardSnapshot.snapshot_date >= day_start,
            CardRewardSnapshot.snapshot_date < day_end,
            CardRewardSnapshot.deleted_at.is_(None),
        )
    )).scalars().first()

    if row is None:
        row = CardRewardSnapshot(
            id=new_id(), household_id=household_id, provider="cmb",
            snapshot_date=day_start, points=points,
            available_limit=available_limit, source=source, source_uid=source_uid,
        )
        db.add(row)
    else:
        row.points = points
        row.available_limit = available_limit
        row.source_uid = source_uid or row.source_uid
    await db.flush()
    return row


async def latest(db: AsyncSession, household_id: str) -> CardRewardSnapshot | None:
    return (await db.execute(
        select(CardRewardSnapshot).where(
            CardRewardSnapshot.household_id == household_id,
            CardRewardSnapshot.deleted_at.is_(None),
        ).order_by(CardRewardSnapshot.snapshot_date.desc()).limit(1)
    )).scalars().first()


async def recent(db: AsyncSession, household_id: str, limit: int = 30) -> list[CardRewardSnapshot]:
    return list((await db.execute(
        select(CardRewardSnapshot).where(
            CardRewardSnapshot.household_id == household_id,
            CardRewardSnapshot.deleted_at.is_(None),
        ).order_by(CardRewardSnapshot.snapshot_date.desc()).limit(limit)
    )).scalars().all())


def detect_drops(rows: list[CardRewardSnapshot], threshold: int = DROP_THRESHOLD) -> list[dict]:
    """按时间正序比较相邻快照，找出积分骤降的日子。

    只做提示，不自动记账：兑换可能换的是抵扣金、里程或商品，系统无从判断。
    """
    ordered = sorted(rows, key=lambda r: r.snapshot_date)
    out: list[dict] = []
    for before, after in zip(ordered, ordered[1:]):
        drop = int(before.points) - int(after.points)
        if drop >= threshold:
            out.append({
                "date": local_iso(after.snapshot_date),
                "drop": drop,
                "before": int(before.points),
                "after": int(after.points),
                "hint": f"{local_iso(after.snapshot_date)[:10]} 积分减少 {drop}，若是兑换了抵扣金，"
                        "可在信用卡「抵扣」里补记一笔",
            })
    return out
