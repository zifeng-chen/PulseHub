"""账单 / 还款到期自动提醒。

用户反馈：提醒本该自动发，不该让人手动点「发送」；并且要能设周期
（提前几天、一天几次、一个周期内最多几次）。

设计取舍：
- 仍不引入外部调度器：API 进程内一个轻量 asyncio 循环（每 30 分钟一次）。
- 发送规则存在 `notification_channels.type_switches` 的 `auto` 段，无需改表：
  `{enabled, days_before, times_per_day, max_per_period, hours}`
- 三层去重，避免骚扰：
  1. 只发「距到期 ≤ days_before 天」的项；
  2. 每个对象当天最多 times_per_day 次，且全天总量也受它限制；
  3. 同一对象在一个提醒周期内最多 max_per_period 次。
- 通知渠道未启用、或时间不在 hours 内时静默跳过，不影响主应用。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.context import CurrentUser
from app.db.base import SessionLocal
from app.db.models.config import NotificationChannel
from app.db.models.identity import HouseholdMember, User

logger = logging.getLogger(__name__)

# 与业务时间同一口径：跟随 FAM_LOCAL_TZ（默认 Asia/Shanghai），不再硬编码
from app.core.timeutil import local_tz

CHECK_INTERVAL_SECONDS = 1800


# 自动发送的默认规则：提前 3 天、每天 1 次、一个周期内最多 3 次、上午 9 点
DEFAULT_AUTO = {
    "enabled": True,
    "days_before": 3,
    "times_per_day": 1,
    "max_per_period": 3,
    "hours": [9],
}


def _auto_config(channel) -> dict:
    """从 type_switches 读出自动发送规则，越界值一律收敛到安全区间。"""
    cfg = dict(DEFAULT_AUTO)
    try:
        raw = json.loads(channel.type_switches or "{}") if channel else {}
    except Exception:  # noqa: BLE001 - 脏数据不该让定时任务挂掉
        raw = {}
    auto = raw.get("auto") if isinstance(raw, dict) else None
    if isinstance(auto, dict):
        for key in DEFAULT_AUTO:
            if key in auto:
                cfg[key] = auto[key]

    def _int(value, fallback: int, low: int, high: int) -> int:
        try:
            number = int(value)
        except Exception:  # noqa: BLE001
            return fallback
        return max(low, min(high, number))

    cfg["enabled"] = bool(cfg["enabled"])
    cfg["days_before"] = _int(cfg["days_before"], 3, 0, 30)
    cfg["times_per_day"] = _int(cfg["times_per_day"], 1, 1, 6)
    cfg["max_per_period"] = _int(cfg["max_per_period"], 3, 1, 30)
    hours = []
    for h in (cfg["hours"] if isinstance(cfg["hours"], list) else [9]):
        hours.append(_int(h, 9, 0, 23))
    cfg["hours"] = sorted(set(hours)) or [9]
    return cfg


async def _owner_user(db) -> CurrentUser | None:
    member = (await db.execute(
        select(HouseholdMember).where(HouseholdMember.status == "active")
        .order_by(HouseholdMember.joined_at)
    )).scalars().first()
    if member is None:
        return None
    row = (await db.execute(select(User).where(User.id == member.user_id))).scalars().first()
    if row is None:
        return None
    return CurrentUser(
        user_id=row.id, username=row.username, display_name=row.display_name,
        is_system_admin=True, household_id=member.household_id, role=member.role,
    )


def _parse_local(value: str | None) -> datetime | None:
    """解析 local_iso 字符串（本地墙钟 naive）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19])
    except Exception:  # noqa: BLE001
        return None


def _to_utc_naive(local_naive: datetime, tz) -> datetime:
    """本地墙钟 → UTC naive：notification_records.created_at 存的是 UTC。"""
    return local_naive.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


async def run_once(force: bool = False) -> dict:
    """执行一次检查；force=True 时忽略时间窗口与每日上限（测试/手动触发用）。

    发送范围：所有「距到期 ≤ days_before 天」的提醒项（账单、租金、租约、资产维护、AI 建议）。
    """
    from sqlalchemy import func

    from app.db.models.config import NotificationRecord
    from app.core.ids import new_id
    from app.services import reminders

    now_local = datetime.now(local_tz())
    tz = local_tz()

    async with SessionLocal() as db:
        channel = (await db.execute(select(NotificationChannel))).scalars().first()
        if channel is None or not channel.enabled or not channel.token_cipher:
            return {"skipped": "channel_disabled"}

        cfg = _auto_config(channel)
        if not cfg["enabled"] and not force:
            return {"skipped": "auto_disabled"}
        if not force and now_local.hour not in cfg["hours"]:
            return {"skipped": "outside_hours", "hours": cfg["hours"]}

        user = await _owner_user(db)
        if user is None:
            return {"skipped": "no_household"}

        day_start_utc = _to_utc_naive(
            datetime.combine(now_local.date(), time.min), tz)
        today_sent = int((await db.execute(
            select(func.count(NotificationRecord.id)).where(
                NotificationRecord.household_id == user.household_id,
                NotificationRecord.created_at >= day_start_utc,
            )
        )).scalar_one())

        # 「一天发几次」同时是全天总量上限与单对象上限，避免同一件事反复轰炸
        budget = cfg["times_per_day"] - today_sent
        if budget <= 0 and not force:
            return {"skipped": "daily_limit", "sent_today": today_sent, "limit": cfg["times_per_day"]}

        data = await reminders.list_reminders(db, user)
        items = [it for g in data["groups"] for it in g["items"]]
        candidates = [
            it for it in items
            if it.get("days_left") is not None and it["days_left"] <= cfg["days_before"]
        ]

        sent = skipped = failed = 0
        for it in candidates:
            if budget <= 0 and not force:
                skipped += 1
                continue

            # 周期上限：从「到期日 - days_before 天」算起，该对象最多 max_per_period 次
            due_local = _parse_local(it.get("due_date"))
            start_utc = day_start_utc
            if due_local is not None:
                window_start = due_local - timedelta(days=cfg["days_before"])
                start_utc = min(start_utc, _to_utc_naive(window_start, tz))
            count = int((await db.execute(
                select(func.count(NotificationRecord.id)).where(
                    NotificationRecord.household_id == user.household_id,
                    NotificationRecord.object_type == it["object_type"],
                    NotificationRecord.object_id == it["object_id"],
                    NotificationRecord.created_at >= start_utc,
                )
            )).scalar_one())
            if count >= cfg["max_per_period"] and not force:
                skipped += 1
                continue

            rec = NotificationRecord(
                id=new_id(), household_id=user.household_id, channel_type="pushplus",
                reminder_type=it["type"],
                title=it["title"],
                content=(
                    f"{it['type_label']}：{it['title']}"
                    + (f"（金额 {it['amount']}）" if it.get("amount") else "")
                    + (f"，到期日 {it['due_date'][:10]}" if it.get("due_date") else "")
                    + (f"，还有 {it['days_left']} 天" if it.get("days_left", 0) >= 0 else "")
                ),
                delivery_status="pending",
                object_type=it["object_type"], object_id=it["object_id"],
            )
            db.add(rec)
            await db.flush()
            await reminders._send_one(db, user, rec)  # noqa: SLF001 - 复用同一套发送与状态记录
            if rec.delivery_status == "sent":
                sent += 1
                channel.last_sent_at = datetime.now(timezone.utc)
                channel.last_status = "sent"
            else:
                failed += 1
                channel.last_status = "failed"
                channel.last_error = rec.error
            budget -= 1

        await db.commit()
        result = {"due": len(candidates), "sent": sent, "skipped": skipped,
                  "failed": failed, "config": cfg}
        if sent or failed:
            logger.info("自动提醒：%s", result)
        return result


async def run_recurring_once() -> dict:
    """结算到期的固定支出与固定收入（不依赖通知渠道、不受时间窗口限制）。"""
    from app.services import recurring
    from app.services import recurring_income

    async with SessionLocal() as db:
        user = await _owner_user(db)
        if user is None:
            return {"skipped": "no_household"}
        created_expense = await recurring.settle_due(db, user)
        created_income = await recurring_income.settle_due(db, user)
        await db.commit()
        if created_expense:
            logger.info("固定支出自动记账 %s 笔", created_expense)
        if created_income:
            logger.info("固定收入自动记账 %s 笔", created_income)
        return {"created_expense": created_expense, "created_income": created_income}


async def _loop() -> None:
    while True:
        try:
            await run_recurring_once()
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # noqa: BLE001 - 定时任务不能拖垮主应用
            logger.exception("固定支出结算失败")
        try:
            await run_once()
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # noqa: BLE001 - 定时任务不能拖垮主应用
            logger.exception("还款日提醒检查失败")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def start(app) -> None:
    if not settings.due_notify_enabled or settings.app_env == "testing":
        return
    app.state.due_notify_task = asyncio.create_task(_loop())


async def stop(app) -> None:
    task = getattr(app.state, "due_notify_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
