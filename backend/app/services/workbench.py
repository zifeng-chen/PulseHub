"""家庭台账工作台（台账总览 A+B 版聚合）。

与脉枢台的分工（重要，不要破坏）：

- 脉枢台（`/dashboard/snapshot`）回答「家里有多少钱」：净资产、账户余额、资产价值、
  投资总额、负债剩余、收支趋势与结构占比——全是**金额**与**趋势**。
- 台账工作台（本模块）回答「这账记得对不对、还差什么、下一步做什么」，
  因此**刻意不返回任何合计金额**：只返回计数、天数、状态三档与缺口描述。

这样两页永不重叠，而且本页的缺口正好解释了脉枢台数字为什么偏低
（例如 4 项资产 90 天没估值 → 资产价值偏低），让「去台账页」变成有理由的动作。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser
from app.core.ids import utc_iso
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.db.models.assets import RealEstate
from app.db.models.business import Account, Asset, BusinessEvent
from app.db.models.finance import InvestmentAccount, InvestmentTrade, InvestmentValuation, Liability
from app.db.models.leases import Lease, LeaseReceivable

# 估值超过这么久没更新就算「陈旧」（资产按季度重估是常见节奏）
STALE_VALUATION_DAYS = 90
# 投资行情超过这么久没刷新就算「未更新」（行情按天变）
STALE_QUOTE_DAYS = 7

_STATE_RANK = {"ok": 0, "warn": 1, "bad": 2}

_AUDIT_ACTION_ZH = {
    "create": "新增", "created": "新增", "update": "编辑", "updated": "编辑",
    "delete": "删除", "deleted": "删除", "confirm": "确认", "confirmed": "确认",
    "cancel": "取消", "cancelled": "取消", "reverse": "冲销", "reversed": "冲销",
    "login": "登录", "logout": "登出", "export": "导出", "import": "导入",
    "restore": "恢复", "settle": "结算", "renew": "续租", "receive": "收款",
    "reconcile": "对账", "apply_quote": "更新行情", "valuate": "估值",
    "paid": "标记已收", "repay": "登记还款", "transfer": "转账",
    "test": "测试", "download": "下载", "upload": "上传",
}
_AUDIT_OBJECT_ZH = {
    "account": "账户", "asset": "资产", "liability": "负债", "investment": "投资",
    "investment_account": "投资账户", "lease": "租赁", "event": "流水",
    "business_event": "流水", "transfer": "转账", "reconciliation": "对账",
    "settings": "设置", "member": "成员", "reminder": "提醒", "user": "账号",
    "credit_card": "信用卡", "credit_card_bill": "信用卡账单", "deposit": "押金",
    "settlement": "结算单", "receivable": "应收",
}
# 「最近改动」只看台账自身的写操作：附件下载、渠道测试这类噪音不进台账页
_AUDIT_TRACKED_OBJECTS = {
    "account", "asset", "liability", "investment", "investment_account",
    "lease", "event", "business_event", "transfer", "reconciliation",
    "credit_card", "credit_card_bill", "deposit", "settlement", "receivable",
}
_AUDIT_DETAIL_KEYS = ("name", "title", "tenant_name", "symbol_name", "event_no", "lease_no")

_QUEUE_HINTS = {
    "repayment": "还款日临近，记得留足余额",
    "credit_card_bill": "账单未还清",
    "rent_due": "登记实收后自动生成下一期",
    "lease_expire": "可续租或发起退租结算",
    "asset_maintenance": "到期维护",
    "ai_suggestion": "建议查看",
}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def _rows(db: AsyncSession, stmt) -> list:
    return list((await db.execute(stmt)).scalars().all())


def _state_of(count: int, severity: str = "warn") -> str:
    return severity if count > 0 else "ok"


def _audit_verb(action: str) -> str:
    """把 investment.account_create / lease.update 这类动作码收敛成动词。"""
    raw = (action or "").lower()
    for candidate in (raw, raw.rsplit(".", 1)[-1], raw.rsplit("_", 1)[-1]):
        if candidate in _AUDIT_ACTION_ZH:
            return _AUDIT_ACTION_ZH[candidate]
    return "变更"


def _audit_detail(summary: str | None) -> str:
    """审计摘要可能是 JSON，尽量只取一个可读名称，不要把整段 JSON 摊给用户。"""
    text = (summary or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return ""
        if isinstance(payload, dict):
            for key in _AUDIT_DETAIL_KEYS:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:24]
        return ""
    return text[:36]


def _pick(dims: list[dict]) -> str:
    rank = max((_STATE_RANK.get(d["state"], 0) for d in dims), default=0)
    for key, value in _STATE_RANK.items():
        if value == rank:
            return key
    return "ok"


async def ledger_workbench(db: AsyncSession, user: CurrentUser) -> dict:
    from app.services import reminders as reminder_service
    from app.services.audit import list_audit_logs
    from app.services.setup import current_household
    from app.services.stats import period_range

    household = await current_household(db)
    tz_name = household.timezone if household else "Asia/Shanghai"
    household_id = user.household_id or ""
    now_local = local_now()
    today = now_local.date()
    month_start, month_end, month_label = period_range("current_month", tz_name)

    # ---------- 账户 ----------
    accounts = await _rows(db, select(Account).where(
        Account.household_id == household_id, Account.deleted_at.is_(None),
    ))
    active_accounts = [a for a in accounts if a.status == "active"]
    unreconciled = [a for a in active_accounts if a.last_reconciled_at is None]

    # ---------- 资产 ----------
    assets = await _rows(db, select(Asset).where(
        Asset.household_id == household_id, Asset.deleted_at.is_(None),
    ))
    live_assets = [a for a in assets if a.status == "active"]
    unvalued = [
        a for a in live_assets
        if _dec(a.current_value) <= 0 or a.last_valuation_date is None
    ]
    stale_cut = now_local - timedelta(days=STALE_VALUATION_DAYS)
    stale_assets = [
        a for a in live_assets
        if a.last_valuation_date is not None
        and (to_local_naive(a.last_valuation_date) or now_local) < stale_cut
    ]
    estates = await _rows(db, select(RealEstate).where(
        RealEstate.household_id == household_id, RealEstate.deleted_at.is_(None),
    ))
    missing_ratio = [e for e in estates if e.ownership_ratio is None]

    # ---------- 负债 ----------
    liabilities = await _rows(db, select(Liability).where(
        Liability.household_id == household_id, Liability.deleted_at.is_(None),
    ))
    live_liabilities = [l for l in liabilities if l.status == "active"]
    missing_payment = [l for l in live_liabilities if not l.monthly_payment]
    missing_schedule = [
        l for l in live_liabilities
        if l.payment_day is None and l.next_payment_date is None and l.first_payment_date is None
    ]

    # ---------- 投资（行情新鲜度） ----------
    invest_accounts = [
        a for a in await _rows(db, select(InvestmentAccount).where(
            InvestmentAccount.household_id == household_id, InvestmentAccount.deleted_at.is_(None),
        )) if a.status == "active"
    ]
    traded_account_ids = set((await db.execute(
        select(InvestmentTrade.account_id).where(
            InvestmentTrade.household_id == household_id,
            InvestmentTrade.deleted_at.is_(None),
            InvestmentTrade.symbol.is_not(None),
        ).distinct()
    )).scalars().all())
    last_quote: dict[str, datetime] = {}
    for acct_id, valued_at in (await db.execute(
        select(InvestmentValuation.account_id, func.max(InvestmentValuation.valued_at)).where(
            InvestmentValuation.household_id == household_id,
            InvestmentValuation.deleted_at.is_(None),
        ).group_by(InvestmentValuation.account_id)
    )).all():
        last_quote[acct_id] = to_local_naive(valued_at)
    quote_cut = now_local - timedelta(days=STALE_QUOTE_DAYS)
    stale_quotes = [
        a for a in invest_accounts
        if a.id in traded_account_ids
        and (last_quote.get(a.id) is None or last_quote[a.id] < quote_cut)
    ]

    # ---------- 租赁 ----------
    leases = await _rows(db, select(Lease).where(
        Lease.household_id == household_id, Lease.deleted_at.is_(None),
    ))
    live_leases = [l for l in leases if l.status == "active"]
    missing_contract = [l for l in live_leases if not l.attachment_id]
    missing_contact = [l for l in live_leases if not l.tenant_contact]
    deposit_pending = [
        l for l in live_leases if _dec(l.deposit_amount) > 0 and not l.deposit_received
    ]
    receivables = await _rows(db, select(LeaseReceivable).where(
        LeaseReceivable.household_id == household_id,
        LeaseReceivable.deleted_at.is_(None),
        LeaseReceivable.status == "pending",
    ))
    overdue_rents = [
        r for r in receivables
        if r.due_date is not None and (to_local_naive(r.due_date) or now_local).date() < today
    ]

    # ---------- 流水 ----------
    event_conditions = [
        BusinessEvent.household_id == household_id,
        BusinessEvent.deleted_at.is_(None),
        BusinessEvent.status == "confirmed",
    ]
    month_conditions = list(event_conditions)
    if month_start is not None:
        month_conditions.append(BusinessEvent.occurred_at >= month_start)
    if month_end is not None:
        month_conditions.append(BusinessEvent.occurred_at < month_end)
    month_events = int((await db.execute(
        select(func.count(BusinessEvent.id)).where(*month_conditions)
    )).scalar_one())
    unclassified = int((await db.execute(
        select(func.count(BusinessEvent.id)).where(*month_conditions, BusinessEvent.category_id.is_(None))
    )).scalar_one())

    # 上月同期（同样的天数窗口），用于「本月记得比上月少」的判断
    prev_events = 0
    if month_start is not None and month_end is not None:
        elapsed = max((now_local - month_start).days + 1, 1)
        prev_end = month_start
        prev_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_events = int((await db.execute(
            select(func.count(BusinessEvent.id)).where(
                *event_conditions,
                BusinessEvent.occurred_at >= prev_start,
                BusinessEvent.occurred_at < prev_start + timedelta(days=elapsed),
            )
        )).scalar_one())

    touched_accounts = set((await db.execute(
        select(BusinessEvent.account_id).where(
            *month_conditions, BusinessEvent.account_id.is_not(None),
        ).distinct()
    )).scalars().all())
    idle_accounts = [
        a for a in active_accounts
        if a.id not in touched_accounts and a.account_type not in ("investment",)
    ]

    # ---------- 健康维度 ----------
    account_notes = []
    if unreconciled:
        account_notes.append(f"{len(unreconciled)} 个未对账")
    dims = [
        {
            "key": "accounts", "label": "账户信息",
            "state": _state_of(len(unreconciled)),
            "count": len(unreconciled),
            "detail": "、".join(account_notes) or "全部已对账",
            "to": "/ledger/accounts",
        },
        {
            "key": "assets", "label": "资产估值",
            "state": "bad" if unvalued else _state_of(len(stale_assets) + len(missing_ratio)),
            "count": len(unvalued) + len(stale_assets) + len(missing_ratio),
            "detail": "、".join(filter(None, [
                f"{len(unvalued)} 项缺估值" if unvalued else "",
                f"{len(stale_assets)} 项超 {STALE_VALUATION_DAYS} 天" if stale_assets else "",
                f"{len(missing_ratio)} 项缺产权比例" if missing_ratio else "",
            ])) or "估值新鲜",
            "to": "/ledger/assets",
        },
        {
            "key": "liabilities", "label": "负债要素",
            "state": _state_of(len(missing_payment) + len(missing_schedule)),
            "count": len(missing_payment) + len(missing_schedule),
            "detail": "、".join(filter(None, [
                f"{len(missing_payment)} 笔缺月供" if missing_payment else "",
                f"{len(missing_schedule)} 笔缺还款日" if missing_schedule else "",
            ])) or "要素齐全",
            "to": "/ledger/liabilities",
        },
        {
            "key": "investments", "label": "投资行情",
            "state": _state_of(len(stale_quotes)),
            "count": len(stale_quotes),
            "detail": f"{len(stale_quotes)} 个账户行情超 {STALE_QUOTE_DAYS} 天" if stale_quotes else "行情最新",
            "to": "/ledger/investments",
        },
        {
            "key": "leases", "label": "租赁档案",
            "state": "bad" if overdue_rents else _state_of(
                len(missing_contract) + len(missing_contact) + len(deposit_pending)
            ),
            "count": (
                len(overdue_rents) + len(missing_contract)
                + len(missing_contact) + len(deposit_pending)
            ),
            "detail": "、".join(filter(None, [
                f"{len(overdue_rents)} 笔租金逾期" if overdue_rents else "",
                f"{len(missing_contract)} 份缺合同" if missing_contract else "",
                f"{len(missing_contact)} 份缺联系方式" if missing_contact else "",
                f"{len(deposit_pending)} 份押金未收" if deposit_pending else "",
            ])) or "档案完整",
            "to": "/ledger/leases",
        },
    ]
    overall = _pick(dims)
    gap_total = (
        len(unreconciled) + len(unvalued) + len(stale_assets) + len(missing_ratio)
        + len(missing_payment) + len(missing_schedule) + len(stale_quotes)
        + len(missing_contract) + len(missing_contact) + len(deposit_pending)
    )

    # ---------- 待补齐清单（数据缺口，只给计数与影响） ----------
    gaps: list[dict] = []
    if unvalued:
        gaps.append({
            "key": "assets:unvalued", "title": f"{len(unvalued)} 项资产缺估值",
            "affects": "资产价值偏低", "count": len(unvalued),
            "to": "/ledger/assets", "severity": "bad",
        })
    if stale_assets:
        gaps.append({
            "key": "assets:stale", "title": f"{len(stale_assets)} 项超 {STALE_VALUATION_DAYS} 天未估值",
            "affects": "资产价值过时", "count": len(stale_assets),
            "to": "/ledger/assets", "severity": "warn",
        })
    if missing_ratio:
        gaps.append({
            "key": "assets:ratio", "title": f"{len(missing_ratio)} 项不动产缺产权比例",
            "affects": "净资产归集不准", "count": len(missing_ratio),
            "to": "/ledger/assets", "severity": "warn",
        })
    if unreconciled:
        gaps.append({
            "key": "accounts:reconcile", "title": f"{len(unreconciled)} 个账户从未对账",
            "affects": "账户余额未经核验", "count": len(unreconciled),
            "to": "/ledger/reconciliation", "severity": "warn",
        })
    if missing_payment or missing_schedule:
        gaps.append({
            "key": "liabilities:fields",
            "title": f"{len(missing_payment) + len(missing_schedule)} 笔负债缺还款要素",
            "affects": "还款提醒会漏", "count": len(missing_payment) + len(missing_schedule),
            "to": "/ledger/liabilities", "severity": "warn",
        })
    if stale_quotes:
        gaps.append({
            "key": "investments:quotes", "title": f"{len(stale_quotes)} 个投资账户行情未更新",
            "affects": "投资总额不准", "count": len(stale_quotes),
            "to": "/ledger/investments", "severity": "warn",
        })
    if missing_contract:
        gaps.append({
            "key": "leases:contract", "title": f"{len(missing_contract)} 份租赁缺合同附件",
            "affects": "收租核对无凭据", "count": len(missing_contract),
            "to": "/ledger/leases", "severity": "warn",
        })
    if missing_contact:
        gaps.append({
            "key": "leases:contact", "title": f"{len(missing_contact)} 份租赁缺租客联系方式",
            "affects": "催收联系不上", "count": len(missing_contact),
            "to": "/ledger/leases", "severity": "warn",
        })
    if deposit_pending:
        gaps.append({
            "key": "leases:deposit", "title": f"{len(deposit_pending)} 份押金未收",
            "affects": "押金台账缺失", "count": len(deposit_pending),
            "to": "/ledger/leases", "severity": "warn",
        })

    # ---------- 待办队列（时间驱动，按到期日排） ----------
    reminder_data = await reminder_service.list_reminders(db, user)
    queue_items: list[dict] = []
    for group in reminder_data.get("groups", []):
        for item in group.get("items", []):
            rtype = item.get("type") or group.get("type")
            queue_items.append({
                "key": item.get("key"),
                "title": item.get("title"),
                "type": rtype,
                "type_label": item.get("type_label") or group.get("type_label"),
                "due_date": item.get("due_date"),
                "days_left": item.get("days_left"),
                "overdue": bool(item.get("overdue")),
                "to": item.get("detail_to"),
                "object_type": item.get("object_type"),
                "object_id": item.get("object_id"),
                "hint": _QUEUE_HINTS.get(rtype or "", "去处理"),
            })

    today_items = [
        q for q in queue_items
        if q["overdue"] or (q["days_left"] is not None and q["days_left"] <= 0)
    ]
    week_items = [
        q for q in queue_items
        if q["days_left"] is not None and 1 <= q["days_left"] <= 7
    ]
    later_items = [
        q for q in queue_items
        if q["days_left"] is None or q["days_left"] > 7
    ]
    today_items.sort(key=lambda q: (q["days_left"] if q["days_left"] is not None else 99))
    week_items.sort(key=lambda q: (q["days_left"] if q["days_left"] is not None else 99))

    # 未归类流水没有截止日，但是「今天顺手就能做完」的事，并入今日
    if unclassified:
        today_items.append({
            "key": "events:unclassified", "title": f"{unclassified} 笔流水未归类",
            "type": "data_gap", "type_label": "数据整理", "due_date": None,
            "days_left": 0, "overdue": False, "to": "/ledger/transactions",
            "object_type": "event", "object_id": None,
            "hint": "影响支出结构占比",
        })

    # ---------- 最近改动（只取台账自身的写操作） ----------
    logs, _total = await list_audit_logs(db, household_id=household_id, page=1, page_size=40)
    recent = []
    for log in logs:
        otype = (log.object_type or "").lower()
        if otype not in _AUDIT_TRACKED_OBJECTS:
            continue
        obj = _AUDIT_OBJECT_ZH.get(otype, "记录")
        detail = _audit_detail(log.after_summary)
        recent.append({
            "id": log.id,
            "at": local_iso(log.created_at),
            "text": f"{obj} · {_audit_verb(log.action)}" + (f" {detail}" if detail else ""),
            "actor": log.actor_name,
            "to": log.source_page if (log.source_page or "").startswith("/") else "",
        })
        if len(recent) >= 6:
            break

    # ---------- 账本目录（移动端降级形态） ----------
    def _latest(*values):
        vals = [to_local_naive(v) for v in values if v is not None]
        return local_iso(max(vals)) if vals else None

    last_event_at = (await db.execute(
        select(func.max(BusinessEvent.occurred_at)).where(
            BusinessEvent.household_id == household_id, BusinessEvent.deleted_at.is_(None),
        )
    )).scalar_one()
    # 目录是纯「档案索引」：记录数 + 最后更新（陈旧度），不重复健康度与缺口信息
    domains = [
        {
            "key": "accounts", "label": "账户", "count": len(active_accounts),
            "last_updated": _latest(*[a.updated_at for a in active_accounts]),
            "to": "/ledger/accounts",
        },
        {
            "key": "transactions", "label": "流水", "count": month_events,
            "count_label": "本月",
            "last_updated": local_iso(last_event_at),
            "to": "/ledger/transactions",
        },
        {
            "key": "assets", "label": "资产", "count": len(live_assets),
            "last_updated": _latest(*[a.last_valuation_date or a.updated_at for a in live_assets]),
            "to": "/ledger/assets",
        },
        {
            "key": "liabilities", "label": "负债", "count": len(live_liabilities),
            "last_updated": _latest(*[l.updated_at for l in live_liabilities]),
            "to": "/ledger/liabilities",
        },
        {
            "key": "investments", "label": "投资", "count": len(invest_accounts),
            "last_updated": _latest(*last_quote.values(), *[a.updated_at for a in invest_accounts]),
            "to": "/ledger/investments",
        },
        {
            "key": "leases", "label": "租赁", "count": len(live_leases),
            "last_updated": _latest(*[l.updated_at for l in live_leases]),
            "to": "/ledger/leases",
        },
        {
            "key": "reminders", "label": "提醒与录入",
            "count": int(reminder_data.get("total") or 0),
            "last_updated": None,
            "to": "/ledger/reminders",
        },
    ]

    # ---------- 记账进度（用来发现「这周是不是漏记了」） ----------
    recording_note = "本月记账节奏正常"
    if prev_events and month_events < prev_events * 0.8:
        recording_note = f"本月已记 {month_events} 笔，较上月同期 {prev_events} 笔偏少"
    elif idle_accounts:
        recording_note = f"本月已记 {month_events} 笔；{len(idle_accounts)} 个账户本月零流水"
    elif month_events:
        recording_note = f"本月已记 {month_events} 笔，{len(active_accounts)} 个账户中 {len(active_accounts) - len(idle_accounts)} 个有流水"

    return {
        "period_label": month_label,
        "health": {
            "state": overall,
            "state_label": (
                "齐全" if overall == "ok"
                else (f"{len(gaps)} 项待处理" if overall == "bad" else f"{len(gaps)} 项待补")
            ),
            "gap_total": gap_total,
            "summary": (
                f"共 {gap_total} 处明细缺口，补齐后脉枢台的数字更可信" if gap_total
                else "台账要素齐全，脉枢台的数字可以直接采信"
            ),
            "dims": dims,
        },
        "gaps": gaps,
        "queue": {
            "today": today_items,
            "week": week_items,
            "later": later_items,
            "counts": {
                "today": len(today_items),
                "week": len(week_items),
                "later": len(later_items),
            },
        },
        "recent": recent,
        "recording": {
            "month_events": month_events,
            "prev_period_events": prev_events,
            "unclassified": unclassified,
            "idle_accounts": [a.name for a in idle_accounts][:5],
            "idle_count": len(idle_accounts),
            "note": recording_note,
        },
        "domains": domains,
        "updated_at": utc_iso(datetime.now(timezone.utc)),
    }
