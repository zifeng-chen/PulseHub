"""提醒中心服务（08 §5、08 §12、19 §5.7）。

- 提醒状态由页面访问 + 确定性查询**实时计算**，不建立后台调度器/Worker/任务队列。
- 通知发送由用户在提醒页**手动确认**，同步发送并立即写入通知记录，失败可重试。
- 任一未配置的 PushPlus 不阻塞主平台（提醒照常展示，仅发送失败）。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser
from app.core.ids import new_id, utc_iso
from app.core.money import fmt_amount
from app.core.timeutil import local_iso, to_local_aware
from app.core.errors import AppError, ErrorCode
from app.db.models.business import Asset
from app.db.models.config import NotificationRecord
from app.db.models.finance import CreditCard, CreditCardBill, Liability
from app.db.models.leases import Lease, LeaseReceivable

# 临近到期窗口（天）
NEAR_DAYS = 30

REMINDER_TYPES = [
    ("repayment", "还款提醒", "/ledger/liabilities"),
    ("credit_card_bill", "信用卡账单", "/ledger/credit-cards"),
    ("rent_due", "租金到期", "/ledger/leases"),
    ("lease_expire", "租约到期", "/ledger/leases"),
    ("asset_maintenance", "资产维护", "/ledger/assets"),
    ("ai_suggestion", "AI 建议", "/insights"),
]


def _next_monthly_due(day: int, now: datetime | None = None) -> datetime:
    """下一次「每月 day 日」，按账本本地时区计算，返回 UTC 零点（与系统时间可比）。"""
    from app.core.timeutil import local_tz

    tz = local_tz()
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    year, month = local_now.year, local_now.month
    for _ in range(14):
        last_day = calendar.monthrange(year, month)[1]
        candidate = date(year, month, min(max(day, 1), last_day))
        if candidate >= local_now.date():
            return datetime(candidate.year, candidate.month, candidate.day, tzinfo=timezone.utc)
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return datetime(local_now.year, local_now.month, min(day, 28), tzinfo=timezone.utc)


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money(value) -> str:
    """金额对外统一输出（默认两位小数）。"""
    return fmt_amount(_dec(value))


def _aware(dt: datetime | None) -> datetime | None:
    """给数据库里的本地墙钟时间补上本地时区，保证与 now 可比。"""
    return to_local_aware(dt)


def _days_left(due: datetime, now: datetime) -> int:
    """按家庭时区计算天数差（避免 UTC 与本地日期跨天导致「今天」被判成明天）。"""
    from app.core.timeutil import local_tz

    tz = local_tz()
    return (due.astimezone(tz).date() - now.astimezone(tz).date()).days


def _item(key, rtype, title, due, amount, detail_to, object_type, object_id) -> dict:
    due_aware = _aware(due)
    days = _days_left(due_aware, datetime.now(timezone.utc)) if due_aware else None
    return {
        "key": key,
        "type": rtype,
        "type_label": next(t for k, t, _ in REMINDER_TYPES if k == rtype),
        "title": title,
        "due_date": local_iso(due_aware) if due_aware else None,
        "days_left": days,
        "overdue": days is not None and days < 0,
        "amount": amount,
        "detail_to": detail_to,
        "object_type": object_type,
        "object_id": object_id,
    }


async def _ai_suggestions(
    db: AsyncSession,
    user: CurrentUser,
    outstanding_map: dict[str, Decimal] | None = None,
    liabilities: list | None = None,
) -> list[dict]:
    """AI 建议（确定性规则生成，**不依赖模型**）：信用卡额度、收支结余、消费异常、待办数据。

    与模型无关——即便本地/第三方 AI 未连接，这些建议也照常可用；
    每条建议都带 severity / reason / suggestion，用户可勾选后手动发送。

    `outstanding_map` / `liabilities` 由调用方（`list_reminders`）传入已算好的结果：
    它本来就要为还款提醒算一遍剩余本金和负债列表，这里再算一遍纯属重复。
    """
    from app.services import insights as insights_service
    from app.services import stats as stats_service
    from app.services.liabilities import outstanding_for
    from app.services.setup import current_household

    household_id = user.household_id or ""
    household = await current_household(db)
    base_currency = household.base_currency if household else "CNY"
    tz_name = household.timezone if household else "Asia/Shanghai"

    items: list[dict] = []

    def add(key, severity, title, reason, suggestion, detail_to, object_type, object_id,
            amount=None):
        items.append({
            "key": key, "type": "ai_suggestion", "type_label": "AI 建议",
            "title": title, "due_date": None, "days_left": None, "overdue": False,
            "amount": amount, "detail_to": detail_to,
            "object_type": object_type, "object_id": object_id,
            "severity": severity, "reason": reason, "suggestion": suggestion,
        })

    # 1) 信用卡额度使用率
    cards = list((await db.execute(
        select(CreditCard).where(CreditCard.household_id == household_id,
                                 CreditCard.deleted_at.is_(None))
    )).scalars().all())
    if cards:
        outs = outstanding_map
        if outs is None:
            outs = await outstanding_for(db, household_id, [c.liability_id for c in cards])
        for c in cards:
            limit = _dec(c.credit_limit)
            used = outs.get(c.liability_id)
            if limit <= 0 or used is None or used <= 0:
                continue
            ratio = used / limit * 100
            if ratio >= 80:
                sev = "critical" if ratio >= 95 else "warning"
                add(f"ai:card:{c.id}", sev,
                    f"信用卡额度使用率 {ratio:.0f}%",
                    f"已用 {_money(used)} / 额度 {_money(limit)} {base_currency}",
                    "额度占用偏高，建议尽快还款以免影响征信与后续用卡。",
                    "/ledger/credit-cards", "credit_card", c.id,
                    amount=_money(limit - used))

    # 2) 本月收支结余
    start, end, _ = stats_service.period_range("current_month", tz_name)
    totals = await stats_service._event_totals(db, household_id, start, end)
    bucket = totals.get(base_currency, {})
    income = _dec(bucket.get("income", 0))
    expense = _dec(bucket.get("expense", 0))
    if expense > income and expense > 0:
        add("ai:balance", "warning", "本月支出已超过收入",
            f"收入 {_money(income)} / 支出 {_money(expense)} {base_currency}",
            "本月为净流出，建议检查非必要支出或调整下月预算。",
            "/insights/expenses", "insight", "expenses",
            amount=_money(expense - income))
    elif income > 0 and expense / income >= Decimal("0.9"):
        add("ai:balance-ratio", "info", "本月支出接近收入（≥90%）",
            f"支出 {_money(expense)} / 收入 {_money(income)} {base_currency}",
            "结余空间较窄，建议预留应急资金。",
            "/insights/expenses", "insight", "expenses")

    # 3) 消费异常（分类环比激增）
    try:
        habits = await insights_service.spending_habits(db, user, "current_month", None, 6)
        for a in (habits.get("habits", {}).get("anomalies") or [])[:3]:
            if a.get("change_pct") is None:
                reason = f"本月 {a['current']}，上月无此分类支出"
            else:
                reason = f"本月 {a['current']}，上月 {a['previous']}（+{a['change_pct']:.0f}%）"
            add(f"ai:anomaly:{a['name']}", "info",
                f"「{a['name']}」支出环比明显上升", reason,
                "确认是否为一次性大额支出；若为常态，建议纳入月度预算。",
                "/insights/spending-habits", "insight", "spending-habits")
    except Exception:  # noqa: BLE001
        pass

    # 4) 待确认草稿
    from app.db.models.business import BusinessEvent

    draft_count = int((await db.execute(
        select(func.count(BusinessEvent.id)).where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.status == "draft",
            BusinessEvent.deleted_at.is_(None),
        )
    )).scalar_one())
    if draft_count > 0:
        add("ai:drafts", "info", f"{draft_count} 条草稿待确认",
            "草稿不进入任何统计，确认后才形成正式业务事实",
            "建议到流水页核对并确认或取消，避免数据不完整。",
            "/ledger/transactions", "insight", "transactions")

    # 5) 缺估值资产
    no_value = int((await db.execute(
        select(func.count(Asset.id)).where(
            Asset.household_id == household_id,
            Asset.deleted_at.is_(None),
            Asset.status == "active",
            (Asset.current_value.is_(None)) | (Asset.current_value == 0),
        )
    )).scalar_one())
    if no_value > 0:
        add("ai:assets-valuation", "info", f"{no_value} 项资产缺少估值",
            "未估值资产不参与净资产统计",
            "建议补充当前价值，使净资产更准确。",
            "/ledger/assets", "insight", "assets")

    # 6) 一次性到期负债（无月供、需一次性归还）：最容易造成现金流断裂的隐性风险
    from app.db.models.finance import Liability
    from app.services.liabilities import base_outstanding

    if liabilities is None:
        liabilities = list((await db.execute(
            select(Liability).where(
                Liability.household_id == household_id,
                Liability.deleted_at.is_(None),
                Liability.status == "active",
            )
        )).scalars().all())
    lump_items = [
        l for l in liabilities
        if l.status == "active"
        and not _dec(l.monthly_payment)
        and (l.repayment_method in ("lump_sum", "interest_first")
             or l.payment_frequency == "one_time"
             or l.liability_type == "personal")
    ]
    if lump_items:
        outs = outstanding_map
        if outs is None:
            outs = await outstanding_for(db, household_id, [l.id for l in lump_items])
        for l in lump_items:
            out = outs.get(l.id) or base_outstanding(l)
            if out <= 0:
                continue
            add(f"ai:lump:{l.id}", "warning",
                f"「{l.name}」需一次性归还 {_money(out)}",
                "该笔负债没有月度还款，到期需一次性付清",
                "建议提前锁定还款资金，或与出借方约定分期以免现金流断裂。",
                "/ledger/liabilities", "liability", l.id)

    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda x: order.get(x["severity"], 3))
    return items


async def list_reminders(db: AsyncSession, user: CurrentUser) -> dict:
    """六类提醒实时计算（08 §12）。"""
    household_id = user.household_id or ""
    now = datetime.now(timezone.utc)
    near_after = now + timedelta(days=NEAR_DAYS)

    groups: list[dict] = []

    # 1) 还款提醒（负债下次还款日临近或已逾期，未结清）
    from app.services.liabilities import compute_next_payment, outstanding_for
    liabilities = list((await db.execute(
        select(Liability).where(Liability.household_id == household_id, Liability.deleted_at.is_(None))
    )).scalars().all())
    # 剩余本金一次算全：这条数据下面还要给「额度使用率」「一次性到期负债」用，
    # 之前三处各调一次 outstanding_for，等于把同一批负债读了三次。
    outstanding_map = await outstanding_for(db, household_id, [l.id for l in liabilities])
    repayment_items = []
    for l in liabilities:
        nxt = _aware(compute_next_payment(l, now))
        if nxt is None:
            continue
        if nxt.date() > near_after.date():
            continue
        out = outstanding_map.get(l.id)
        if out is not None and out <= 0:
            continue
        repayment_items.append(_item(
            f"repayment:{l.id}", "repayment", l.name, nxt,
            fmt_amount(l.monthly_payment or 0) if l.monthly_payment else None,
            "/ledger/liabilities", "liability", l.id,
        ))
    groups.append({"type": "repayment", "type_label": "还款提醒",
                   "count": len(repayment_items), "items": repayment_items})

    # 2) 信用卡账单（due_date 临近或已逾期，未还清）
    bills = list((await db.execute(
        select(CreditCardBill).where(CreditCardBill.household_id == household_id,
                                     CreditCardBill.deleted_at.is_(None))
    )).scalars().all())
    bill_items = []
    for b in bills:
        due = _aware(b.due_date)
        if due is None or due.date() > near_after.date():
            continue
        if float(b.paid_amount) >= float(b.total_amount):
            continue
        bill_items.append(_item(
            f"bill:{b.id}", "credit_card_bill", "信用卡账单",
            due, fmt_amount(b.total_amount - (b.paid_amount or 0)),
            "/ledger/credit-cards", "credit_card_bill", b.id,
        ))
    # 2b) 账户里的信用卡 / 电子钱包：按账户的「还款日」生成（不依赖独立的账单表）
    from app.services import accounts as account_service

    for acc, bal in await account_service.list_accounts(db, user):
        if not acc.due_day or acc.status != "active":
            continue
        owing = -Decimal(str(bal))
        if owing <= 0:
            continue
        due = _next_monthly_due(int(acc.due_day), now)
        if due.date() > near_after.date():
            continue
        bill_items.append(_item(
            f"acctbill:{acc.id}", "credit_card_bill", f"{acc.name} 还款",
            due, fmt_amount(owing), "/ledger/accounts", "account", acc.id,
        ))

    groups.append({"type": "credit_card_bill", "type_label": "信用卡账单",
                   "count": len(bill_items), "items": bill_items})

    # 3) 租金到期（应收 pending 且 due_date 临近或已逾期）
    receivables = list((await db.execute(
        select(LeaseReceivable).where(LeaseReceivable.household_id == household_id,
                                      LeaseReceivable.deleted_at.is_(None),
                                      LeaseReceivable.status == "pending")
    )).scalars().all())
    rent_items = []
    for r in receivables:
        due = _aware(r.due_date)
        if due is None or due.date() > near_after.date():
            continue
        rent_items.append(_item(
            f"rent:{r.id}", "rent_due", "租金应收",
            due, fmt_amount(r.amount), "/ledger/leases", "lease_receivable", r.id,
        ))
    groups.append({"type": "rent_due", "type_label": "租金到期",
                   "count": len(rent_items), "items": rent_items})

    # 4) 租约到期（end_date 临近）
    leases = list((await db.execute(
        select(Lease).where(Lease.household_id == household_id, Lease.deleted_at.is_(None),
                            Lease.status == "active")
    )).scalars().all())
    lease_items = []
    for l in leases:
        end = _aware(l.end_date)
        if end is None or end.date() > near_after.date():
            continue
        lease_items.append(_item(
            f"lease:{l.id}", "lease_expire", f"租约 {l.lease_no or ''} 到期",
            end, None, "/ledger/leases", "lease", l.id,
        ))
    groups.append({"type": "lease_expire", "type_label": "租约到期",
                   "count": len(lease_items), "items": lease_items})

    # 5) 资产维护（maintenance_date 临近或已过期）
    assets = list((await db.execute(
        select(Asset).where(Asset.household_id == household_id, Asset.deleted_at.is_(None))
    )).scalars().all())
    maint_items = []
    for a in assets:
        m = _aware(a.maintenance_date)
        if m is None or m.date() > near_after.date():
            continue
        maint_items.append(_item(
            f"maint:{a.id}", "asset_maintenance", a.name,
            m, None, "/ledger/assets", "asset", a.id,
        ))
    groups.append({"type": "asset_maintenance", "type_label": "资产维护",
                   "count": len(maint_items), "items": maint_items})

    # 6) AI 建议（确定性规则实时生成：额度/结余/消费异常/待办数据）
    ai_items = await _ai_suggestions(db, user, outstanding_map=outstanding_map, liabilities=liabilities)
    groups.append({"type": "ai_suggestion", "type_label": "AI 建议",
                   "count": len(ai_items), "items": ai_items})

    total = sum(g["count"] for g in groups)
    return {
        "groups": groups,
        "total": total,
        "near_days": NEAR_DAYS,
        "data_updated_at": utc_iso(now),
        "note": "到期提醒会按「提醒通知」里的规则自动发送；页面上的发送按钮用于手动补发。",
    }


def _serialize_record(r: NotificationRecord) -> dict:
    from app.core import enums as enum_zh
    return {
        "id": r.id,
        "channel_type": r.channel_type,
        "reminder_type": r.reminder_type,
        "title": r.title,
        "content": r.content[:200],
        "delivery_status": r.delivery_status,
        "delivery_status_label": enum_zh.zh("delivery_status", r.delivery_status),
        "error": r.error,
        "sent_at": utc_iso(r.sent_at) if r.sent_at else None,
        "retry_count": r.retry_count,
        "object_type": r.object_type,
        "object_id": r.object_id,
    }


async def list_records(db: AsyncSession, user: CurrentUser, page: int = 1, page_size: int = 20) -> tuple[list[dict], int]:
    from sqlalchemy import func
    conditions = [NotificationRecord.household_id == user.household_id]
    total = int((await db.execute(select(func.count(NotificationRecord.id)).where(*conditions))).scalar_one())
    rows = list((await db.execute(
        select(NotificationRecord).where(*conditions)
        .order_by(NotificationRecord.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return [_serialize_record(r) for r in rows], total


_REMINDER_LABELS = {
    "repayment": "还款提醒",
    "credit_card_bill": "信用卡账单",
    "rent_due": "租金到期",
    "lease_expire": "租约到期",
    "asset_maintenance": "资产维护",
    "ai_suggestion": "AI 建议",
}


def _brand_title(reminder_type: str | None, fallback: str | None) -> str:
    """通知标题统一为「财资脉枢 · 提醒类型」，避免各渠道标题风格不一。"""
    label = _REMINDER_LABELS.get(reminder_type or "") or fallback or "提醒"
    return f"财资脉枢 · {label}"


async def _send_one(db: AsyncSession, user: CurrentUser, record: NotificationRecord) -> None:
    """同步发送单条通知，更新记录状态。"""
    from app.adapters import build_adapters
    adapter = build_adapters(db, user.household_id)["notification"]
    try:
        await adapter.send(_brand_title(record.reminder_type, record.title), record.content)
        record.delivery_status = "sent"
        record.sent_at = datetime.now(timezone.utc)
        record.error = None
    except AppError as exc:
        record.delivery_status = "failed"
        record.error = exc.message if hasattr(exc, "message") else str(exc)
    record.retry_count = (record.retry_count or 0) + 1


async def send_reminders(db: AsyncSession, user: CurrentUser, keys: list[str] | None = None) -> dict:
    """手动确认发送提醒（08 §12：同步发送，写通知记录）。keys 为空则发送全部。"""
    reminders = await list_reminders(db, user)
    all_items = [it for g in reminders["groups"] for it in g["items"]]
    if keys:
        targets = [it for it in all_items if it["key"] in keys]
    else:
        targets = all_items

    if not targets:
        raise AppError(ErrorCode.VALIDATION_ERROR, "没有可发送的提醒", status_code=422)

    created: list[NotificationRecord] = []
    for it in targets:
        rec = NotificationRecord(
            id=new_id(),
            household_id=user.household_id,
            channel_type="pushplus",
            reminder_type=it["type"],
            title=it["title"],
            content=f"{it['type_label']}：{it['title']}"
                    + (f"（金额 {it['amount']}）" if it.get("amount") else "")
                    + (f"，到期日 {it['due_date'][:10]}" if it.get("due_date") else ""),
            delivery_status="pending",
            object_type=it["object_type"],
            object_id=it["object_id"],
        )
        db.add(rec)
        created.append(rec)
    await db.flush()

    for rec in created:
        await _send_one(db, user, rec)

    sent = sum(1 for r in created if r.delivery_status == "sent")
    failed = sum(1 for r in created if r.delivery_status == "failed")
    return {"total": len(created), "sent": sent, "failed": failed}


async def retry_record(db: AsyncSession, user: CurrentUser, record_id: str) -> dict:
    """重试失败的通知记录。"""
    record = (await db.execute(
        select(NotificationRecord).where(
            NotificationRecord.id == record_id,
            NotificationRecord.household_id == user.household_id,
        )
    )).scalars().first()
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "通知记录不存在", status_code=404)
    if record.delivery_status == "sent":
        raise AppError(ErrorCode.STATE_INVALID, "该通知已发送成功，无需重试", status_code=409)
    await _send_one(db, user, record)
    return _serialize_record(record)


async def due_account_bills(db: AsyncSession, user: CurrentUser) -> list[dict]:
    """需还款账户（信用卡 / 电子钱包）中「今天到期」的账单。"""
    from app.core.timeutil import local_tz
    from app.services import accounts as account_service

    tz = local_tz()
    today = datetime.now(tz).date()
    items: list[dict] = []
    for acc, bal in await account_service.list_accounts(db, user):
        if not acc.due_day or acc.status != "active":
            continue
        owing = -Decimal(str(bal))
        if owing <= 0:
            continue
        due = _next_monthly_due(int(acc.due_day), datetime.now(timezone.utc))
        if due.date() != today:
            continue
        items.append({
            "account_id": acc.id,
            "name": acc.name,
            "amount": fmt_amount(owing),
            "due_date": due.date().isoformat(),
        })
    return items


async def send_due_bills_today(db: AsyncSession, user: CurrentUser) -> dict:
    """还款日当天发送一次提醒（同一天同一账户只发一次）。"""
    from app.core.timeutil import local_tz

    tz = local_tz()
    # 去重窗口按「本地自然日」划界，但 created_at 是 UTC 系统时间：
    # 必须把本地零点换算成对应的 UTC 瞬间，否则在本地 0:00–8:00 会重复发送。
    start_local = datetime.combine(datetime.now(tz).date(), datetime.min.time(), tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)

    due_items = await due_account_bills(db, user)
    sent = 0
    skipped = 0
    failed = 0
    for item in due_items:
        exists = (await db.execute(
            select(NotificationRecord.id).where(
                NotificationRecord.household_id == user.household_id,
                NotificationRecord.reminder_type == "credit_card_bill",
                NotificationRecord.object_type == "account",
                NotificationRecord.object_id == item["account_id"],
                NotificationRecord.created_at >= start_utc,
            ).limit(1)
        )).scalars().first()
        if exists:
            skipped += 1
            continue
        rec = NotificationRecord(
            id=new_id(), household_id=user.household_id, channel_type="pushplus",
            reminder_type="credit_card_bill",
            title=f"{item['name']} 今天还款",
            content=f"信用卡账单：{item['name']} 今天（{item['due_date']}）是还款日，"
                    f"待还金额 {item['amount']}，请及时还款以免产生利息。",
            delivery_status="pending", object_type="account", object_id=item["account_id"],
            created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(rec)
        await db.flush()
        await _send_one(db, user, rec)
        if rec.delivery_status == "sent":
            sent += 1
        else:
            failed += 1
    await db.commit()
    return {"due": len(due_items), "sent": sent, "failed": failed, "skipped": skipped}
