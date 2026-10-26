"""统一业务事件与资金变动基座（23 §4、11 §2）。

规则：
- 草稿不进入余额、净资产、收支、脉诊或 AI 权威分析。
- 确认必须在同一事务中生成资金变动与审计记录。
- 已确认事件不能直接删除，只能冲销/更正。
- 更新必须带数据版本；版本冲突返回 409。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.money import fmt_amount
from app.db.models.business import BusinessEvent, EventMovement
from app.services import audit as audit_service

# Numeric(20,4) 的安全上限（与 accounts.MAX_AMOUNT 保持一致）；超出会在 MySQL 严格模式下报错
MAX_AMOUNT = 1e15
# BusinessEvent.title 列宽见 db/models/business.py
MAX_TITLE_LENGTH = 255

# 事件类型 -> 允许的资金变动目标类型
_EVENT_TARGET_HINT = {
    "income": "account", "expense": "account", "transfer": "account",
    "asset_acquire": "asset", "asset_dispose": "asset",
    "liability_draw": "liability", "liability_repay": "liability",
    "investment_buy": "investment", "investment_sell": "investment",
    "rent_receivable": "lease", "rent_received": "account",
    "deposit_receive": "account", "deposit_return": "account",
}

# 是否进入统计（与 enums.EVENT_TYPE 保持一致口径）
_COUNTING_TYPES = {"income", "expense", "liability_repay", "rent_received"}

# 事件类型 → 资金流向（列表/组件统一显示用）。
# 「收支方向」和「事件类型」是两个维度：类型是业务语义（负债还款、资产购置…），
# 方向只看钱进还是钱出。界面统一按方向显示，避免出现「收入 / 支出 / 还款」这种混杂口径。
_FLOW_BY_EVENT_TYPE = {
    # 真正计入收支的
    "income": ("income", "收入"),
    "rent_received": ("income", "收入"),
    "expense": ("expense", "支出"),
    "refund": ("expense", "支出"),           # 退款冲减支出，方向仍归支出侧
    "liability_repay": ("expense", "支出"),   # 本金不重复计支出，但钱的流向是支出
    # 钱进来了但不计收入：本金回笼、借款到账、押金收取、资产/持仓变现
    "receivable_repay": ("inflow", "资金转入"),
    "liability_draw": ("inflow", "资金转入"),
    "deposit_receive": ("inflow", "资金转入"),
    "asset_dispose": ("inflow", "资金转入"),
    "investment_sell": ("inflow", "资金转入"),
    # 钱出去了但不计支出：形成资产、买入持仓、退还押金
    "asset_acquire": ("outflow", "资金转出"),
    "investment_buy": ("outflow", "资金转出"),
    "deposit_return": ("outflow", "资金转出"),
    "transfer": ("transfer", "内部转账"),
    "rent_receivable": ("neutral", "不计收支"),  # 只是应收确认，不动账户资金
}


def flow_direction(event_type: str | None) -> tuple[str, str]:
    """返回 (方向代码, 方向中文)。未知类型统一按「不计收支」处理，避免显示英文标识。"""
    return _FLOW_BY_EVENT_TYPE.get(event_type or "", ("neutral", "不计收支"))

# 收入类事件类型（用于分类域校验：收入事件必须用收入分类）
_INCOME_EVENT_TYPES = {"income", "rent_received"}

# M1 核心账务 + M2 资产 + M3 负债与投资 + M4 房产租赁自行创建的事件类型。
# 其余类型必须先有对应模块的关联对象，否则会产生无法追溯的孤儿数据（18 §6）。
CREATABLE_EVENT_TYPES = {
    "income", "expense", "refund", "asset_acquire", "asset_dispose",
    "liability_draw", "liability_repay", "investment_buy", "investment_sell",
    "rent_receivable", "rent_received", "deposit_receive", "deposit_return",
    "receivable_repay",
}
M1_EVENT_TYPES = {"income", "expense"}  # 兼容旧引用

DEFERRED_EVENT_MODULES = {
    "transfer": "内部转账专用接口（/ledger/transfers）",
}


def movement_specs(event: BusinessEvent) -> list[dict]:
    """按事件类型推导资金变动。

    - 收入/支出：账户变动；支出若只关联信用卡（无账户）则记为负债本金增加（刷卡消费）；
    - 资产购置/处置：账户 + 资产双变动；
    - 借款到账：账户转入 + 负债本金增加；负债还款：账户转出 + 负债本金减少；
    - 投资买入/卖出：投资账户资金减少/增加。
    负债方向语义：in = 本金增加（借款、刷卡消费），out = 本金减少（还款）。
    投资方向语义：in = 投资账户资金增加（卖出回款），out = 资金减少（买入）。
    """
    t = event.event_type
    if t == "income":
        return [{"target_type": "account", "target_id": event.account_id, "direction": "in"}]
    if t == "expense":
        if event.liability_id and not event.account_id:
            return [{"target_type": "liability", "target_id": event.liability_id, "direction": "in"}]
        return [{"target_type": "account", "target_id": event.account_id, "direction": "out"}]

    specs: list[dict] = []
    if t == "asset_acquire":
        if event.account_id:
            specs.append({"target_type": "account", "target_id": event.account_id, "direction": "out"})
        if event.asset_id:
            specs.append({"target_type": "asset", "target_id": event.asset_id, "direction": "in"})
    elif t == "asset_dispose":
        if event.asset_id:
            specs.append({"target_type": "asset", "target_id": event.asset_id, "direction": "out"})
        if event.account_id and float(event.amount or 0) > 0:
            specs.append({"target_type": "account", "target_id": event.account_id, "direction": "in"})
    elif t == "liability_draw":
        if event.account_id:
            specs.append({"target_type": "account", "target_id": event.account_id, "direction": "in"})
        if event.liability_id:
            specs.append({"target_type": "liability", "target_id": event.liability_id, "direction": "in"})
    elif t == "liability_repay":
        if event.account_id:
            specs.append({"target_type": "account", "target_id": event.account_id, "direction": "out"})
        if event.liability_id:
            specs.append({"target_type": "liability", "target_id": event.liability_id, "direction": "out"})
    elif t == "investment_buy":
        if event.investment_account_id:
            specs.append({"target_type": "investment", "target_id": event.investment_account_id,
                          "direction": "out"})
    elif t == "investment_sell":
        if event.investment_account_id:
            specs.append({"target_type": "investment", "target_id": event.investment_account_id,
                          "direction": "in"})
    elif t == "receivable_repay":
        # 借出款回款：资金回到账户，应收资产同步减少（不计收入）
        if event.account_id:
            specs.append({"target_type": "account", "target_id": event.account_id, "direction": "in"})
        if event.asset_id:
            specs.append({"target_type": "asset", "target_id": event.asset_id, "direction": "out"})
    elif t == "refund":
        # 退款是资金「回流」：信用卡退款减少欠款，储蓄卡退款增加余额
        specs.append({"target_type": "account", "target_id": event.account_id, "direction": "in"})
    elif t == "rent_receivable":
        # 租金应收不改变账户余额（06 §9）：不生成资金变动，应收记录由 lease_receivables 承载
        return []
    elif t == "rent_received":
        specs.append({"target_type": "account", "target_id": event.account_id, "direction": "in"})
    elif t == "deposit_receive":
        specs.append({"target_type": "account", "target_id": event.account_id, "direction": "in"})
    elif t == "deposit_return":
        specs.append({"target_type": "account", "target_id": event.account_id, "direction": "out"})
    else:
        direction = "in" if t in ("rent_received", "deposit_receive") else "out"
        target_type = _EVENT_TARGET_HINT.get(t, "account")
        target_id = {
            "account": event.account_id,
            "asset": event.asset_id,
            "liability": event.liability_id,
            "investment": event.investment_account_id,
            "property": None,
        }.get(target_type)
        specs.append({"target_type": target_type, "target_id": target_id, "direction": direction})
    return specs


def _new_event_no() -> str:
    stamp = local_now().strftime("%Y%m%d")
    return f"EV{stamp}{new_id()[:6].upper()}"


def serialize_event(e: BusinessEvent, movements: list[EventMovement] | None = None) -> dict:
    from app.core import enums as enum_zh

    direction, direction_label = flow_direction(e.event_type)
    return {
        "id": e.id,
        "short_id": e.event_no,
        "event_no": e.event_no,
        "event_type": e.event_type,
        "event_type_label": enum_zh.zh("event_type", e.event_type),
        # 统一显示口径：方向（收入/支出/内部转账/不计收支）+ 二级分类名。
        # 页面上的「类型」列一律用 direction_label，不要在各地自己拼「还款」这类值。
        "flow_direction": direction,
        "flow_direction_label": direction_label,
        "category_name": None,  # 由 attach_category_names 批量回填
        "status": e.status,
        "status_label": enum_zh.zh("event_status", e.status),
        "occurred_at": local_iso(e.occurred_at) if e.occurred_at else None,
        "confirmed_at": utc_iso(e.confirmed_at) if e.confirmed_at else None,
        "amount": fmt_amount(e.amount or 0),
        "currency": e.currency,
        "base_currency": e.base_currency or e.currency,
        "exchange_rate": f"{float(e.exchange_rate):.8f}" if e.exchange_rate else None,
        "conversion_status": e.conversion_status,
        "category_id": e.category_id,
        "account_id": e.account_id,
        "member_id": e.member_id,
        "asset_id": e.asset_id,
        "liability_id": e.liability_id,
        "investment_account_id": e.investment_account_id,
        "lease_id": e.lease_id,
        "refund_of": e.refund_of,
        "title": e.title,
        "counterparty": e.counterparty,
        "source": e.source,
        "source_label": enum_zh.zh("source_method", e.source),
        "attachment_id": e.attachment_id,
        "note": e.note,
        "realized_gain": fmt_amount(e.realized_gain) if e.realized_gain is not None else None,
        "counts_in_stats": e.status == "confirmed" and e.event_type in _COUNTING_TYPES,
        "version": e.version,
        "created_at": utc_iso(e.created_at),
        "updated_at": utc_iso(e.updated_at),
        "movements": [
            {
                "id": m.id,
                "target_type": m.target_type,
                "target_id": m.target_id,
                "direction": m.direction,
                "direction_label": "转入" if m.direction == "in" else "转出",
                "amount": fmt_amount(m.amount or 0),
                "currency": m.currency,
            }
            for m in (movements or [])
        ],
    }


async def _validate_relations(db: AsyncSession, user: CurrentUser, payload: dict) -> dict:
    """校验事件关联对象确实属于当前家庭，且分类域与事件类型匹配。"""
    from sqlalchemy import select

    from app.db.models.business import Account, Asset
    from app.db.models.config import Category
    from app.db.models.finance import InvestmentAccount, Liability

    resolved: dict = {}
    field_errors: dict[str, str] = {}

    account_id = payload.get("account_id")
    if account_id:
        account = (await db.execute(select(Account).where(Account.id == account_id))).scalars().first()
        if account is None or account.deleted_at is not None:
            field_errors["account_id"] = "账户不存在"
        elif account.household_id != user.household_id:
            raise AppError(ErrorCode.FORBIDDEN, status_code=403)
        elif account.status != "active":
            field_errors["account_id"] = "该账户已停用，不能用于记账"
        else:
            resolved["account"] = account

    asset_id = payload.get("asset_id")
    if asset_id:
        asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalars().first()
        if asset is None or asset.deleted_at is not None:
            field_errors["asset_id"] = "资产不存在"
        elif asset.household_id != user.household_id:
            raise AppError(ErrorCode.FORBIDDEN, status_code=403)
        else:
            resolved["asset"] = asset

    liability_id = payload.get("liability_id")
    if liability_id:
        liability = (await db.execute(select(Liability).where(Liability.id == liability_id))).scalars().first()
        if liability is None or liability.deleted_at is not None:
            field_errors["liability_id"] = "负债不存在"
        elif liability.household_id != user.household_id:
            raise AppError(ErrorCode.FORBIDDEN, status_code=403)
        else:
            resolved["liability"] = liability

    investment_id = payload.get("investment_account_id")
    if investment_id:
        inv = (await db.execute(
            select(InvestmentAccount).where(InvestmentAccount.id == investment_id)
        )).scalars().first()
        if inv is None or inv.deleted_at is not None:
            field_errors["investment_account_id"] = "投资账户不存在"
        elif inv.household_id != user.household_id:
            raise AppError(ErrorCode.FORBIDDEN, status_code=403)
        else:
            resolved["investment"] = inv

    lease_id = payload.get("lease_id")
    if lease_id:
        from app.db.models.leases import Lease

        lease = (await db.execute(select(Lease).where(Lease.id == lease_id))).scalars().first()
        if lease is None or lease.deleted_at is not None:
            field_errors["lease_id"] = "租约不存在"
        elif lease.household_id != user.household_id:
            raise AppError(ErrorCode.FORBIDDEN, status_code=403)
        else:
            resolved["lease"] = lease

    category_id = payload.get("category_id")
    if category_id:
        category = (await db.execute(select(Category).where(Category.id == category_id))).scalars().first()
        # 收入类事件（income、租金实收）用收入分类；其余（支出/利息/手续费等）用支出分类
        expected_domain = (
            "income"
            if payload.get("event_type") in _INCOME_EVENT_TYPES
            else "expense"
        )
        if category is None or category.deleted_at is not None:
            field_errors["category_id"] = "分类不存在"
        elif category.household_id != user.household_id:
            raise AppError(ErrorCode.FORBIDDEN, status_code=403)
        elif category.domain != expected_domain:
            field_errors["category_id"] = f"该分类属于「{category.domain}」域，与当前事件类型不匹配"
        elif category.status != "active":
            field_errors["category_id"] = "该分类已停用"
        else:
            resolved["category"] = category

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "关联对象校验失败", field_errors=field_errors)
    return resolved


async def _validate_refund(
    db: AsyncSession, user: CurrentUser, payload: dict, amount: float
) -> str:
    """退款校验：必须关联已确认支出、退回原账户、且不超过可退余额。

    可退余额 = 原支出金额 − 已有退款（含草稿，排除已冲销），避免重复退款。
    """
    refund_of = str(payload.get("refund_of") or "").strip()
    if not refund_of:
        raise AppError(ErrorCode.VALIDATION_ERROR, "退款必须关联原始支出",
                       field_errors={"refund_of": "请选择要退款的支出"})

    origin = (
        await db.execute(
            select(BusinessEvent).where(
                BusinessEvent.id == refund_of,
                BusinessEvent.household_id == user.household_id,
                BusinessEvent.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if origin is None:
        raise AppError(ErrorCode.NOT_FOUND, "原始支出不存在", status_code=404)
    if origin.event_type != "expense":
        raise AppError(ErrorCode.VALIDATION_ERROR, "只有支出可以退款",
                       field_errors={"refund_of": "请选择支出类型的事件"})
    if origin.status != "confirmed":
        raise AppError(ErrorCode.STATE_INVALID, "只能对已确认的支出退款", status_code=409)
    if (payload.get("account_id") or None) != origin.account_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "退款需退回原账户",
                       field_errors={"account_id": "退款账户必须与原支出一致"})

    refunded = (
        await db.execute(
            select(func.coalesce(func.sum(BusinessEvent.amount), 0)).where(
                BusinessEvent.refund_of == refund_of,
                BusinessEvent.event_type == "refund",
                BusinessEvent.status != "reversed",
                BusinessEvent.deleted_at.is_(None),
            )
        )
    ).scalar() or 0

    remaining = float(origin.amount or 0) - float(refunded)
    if amount > remaining + 0.0001:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"退款金额超过可退余额（剩余 {remaining:.2f}）",
            field_errors={"amount": f"最多可退 {remaining:.2f}"},
        )
    return refund_of


async def create_draft(
    db: AsyncSession,
    user: CurrentUser,
    payload: dict,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    from app.core import enums as enum_zh

    event_type = payload.get("event_type")
    if event_type not in enum_zh.EVENT_TYPE:
        raise AppError(ErrorCode.VALIDATION_ERROR, "事件类型不合法",
                       field_errors={"event_type": "请选择合法的事件类型"})

    if event_type not in CREATABLE_EVENT_TYPES:
        deferred = DEFERRED_EVENT_MODULES.get(event_type, "后续模块")
        raise AppError(
            ErrorCode.STATE_INVALID,
            f"「{enum_zh.zh('event_type', event_type)}」类型将在{deferred}交付后开放",
            field_errors={"event_type": f"当前仅支持收入、支出与资产购置/处置，该类型属于{deferred}"},
            status_code=409,
        )

    # 资产事件必须关联到具体资产，否则会产生无法追溯的孤儿事件
    if event_type in ("asset_acquire", "asset_dispose") and not payload.get("asset_id"):
        raise AppError(ErrorCode.VALIDATION_ERROR, "资产事件必须关联具体资产",
                       field_errors={"asset_id": "请选择关联资产"})
    if event_type == "asset_acquire" and payload.get("account_id") is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "资产购置必须指定资金来源账户",
                       field_errors={"account_id": "请选择资金来源账户"})
    if event_type in ("liability_draw", "liability_repay") and not payload.get("liability_id"):
        raise AppError(ErrorCode.VALIDATION_ERROR, "负债事件必须关联具体负债",
                       field_errors={"liability_id": "请选择关联负债"})
    if event_type in ("investment_buy", "investment_sell") and not payload.get("investment_account_id"):
        raise AppError(ErrorCode.VALIDATION_ERROR, "投资事件必须关联具体投资账户",
                       field_errors={"investment_account_id": "请选择关联投资账户"})
    if event_type in ("rent_receivable", "rent_received", "deposit_receive", "deposit_return") \
            and not payload.get("lease_id"):
        raise AppError(ErrorCode.VALIDATION_ERROR, "租赁事件必须关联具体租约",
                       field_errors={"lease_id": "请选择关联租约"})

    title = (payload.get("title") or "").strip()
    if not title:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请填写标题", field_errors={"title": "请填写标题"})
    if len(title) > MAX_TITLE_LENGTH:
        raise AppError(ErrorCode.VALIDATION_ERROR, "标题过长",
                       field_errors={"title": f"标题最多 {MAX_TITLE_LENGTH} 个字符"})

    amount = payload.get("amount") or 0
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额格式不正确", field_errors={"amount": "金额必须是数字"})
    if amount_value != amount_value or amount_value in (float("inf"), float("-inf")):
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额格式不正确", field_errors={"amount": "金额必须是有效数字"})
    if amount_value < 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额不能为负数", field_errors={"amount": "金额必须大于等于 0"})
    if amount_value > MAX_AMOUNT:
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额超出允许范围",
                       field_errors={"amount": "金额超出允许范围"})

    relations = await _validate_relations(db, user, payload)

    # 退款：校验原支出、账户与可退余额，并记录来源
    refund_of: str | None = None
    if event_type == "refund":
        refund_of = await _validate_refund(db, user, payload, amount_value)
    account = relations.get("account")
    liability = relations.get("liability")
    investment = relations.get("investment")

    # 归属对象的币种优先级最高：资金变动必须与该对象币种一致，否则余额会被混入不同币种金额。
    if account is not None:
        currency = account.currency
    elif liability is not None:
        currency = liability.currency
    elif investment is not None:
        currency = investment.currency
    else:
        currency = (payload.get("currency") or "CNY").upper()

    occurred_at = payload.get("occurred_at")
    if isinstance(occurred_at, str) and occurred_at:
        try:
            occurred_at_dt = to_local_naive(datetime.fromisoformat(occurred_at.replace("Z", "+00:00")))
        except ValueError:
            raise AppError(ErrorCode.VALIDATION_ERROR, "发生时间格式不正确",
                           field_errors={"occurred_at": "时间格式应为 ISO 8601"})
    else:
        occurred_at_dt = local_now()

    event = BusinessEvent(
        id=new_id(),
        household_id=user.household_id,
        event_no=_new_event_no(),
        event_type=event_type,
        status="draft",
        occurred_at=occurred_at_dt,
        amount=amount_value,
        currency=currency,
        original_amount=amount_value,
        original_currency=currency,
        base_currency=currency,
        conversion_status="same_currency",
        category_id=payload.get("category_id"),
        account_id=payload.get("account_id"),
        member_id=payload.get("member_id"),
        asset_id=payload.get("asset_id"),
        liability_id=payload.get("liability_id"),
        investment_account_id=payload.get("investment_account_id"),
        lease_id=payload.get("lease_id"),
        refund_of=refund_of,
        title=title,
        counterparty=payload.get("counterparty"),
        source=payload.get("source") or "manual",
        attachment_id=payload.get("attachment_id"),
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(event)
    await db.flush()

    await audit_service.record_audit(
        db,
        action="event.create_draft",
        action_label="创建业务事件草稿",
        object_type="business_event",
        object_id=event.id,
        after=event_to_dict(event),
        user=user,
        ctx=ctx,
    )
    return event


def event_to_dict(e: BusinessEvent) -> dict:
    return {
        "event_no": e.event_no,
        "event_type": e.event_type,
        "status": e.status,
        "title": e.title,
        "amount": e.amount,
        "currency": e.currency,
        "occurred_at": local_iso(e.occurred_at) if e.occurred_at else None,
        "category_id": e.category_id,
        "account_id": e.account_id,
        "note": e.note,
    }


async def get_event(db: AsyncSession, user: CurrentUser, event_id: str) -> BusinessEvent:
    event = (await db.execute(select(BusinessEvent).where(BusinessEvent.id == event_id))).scalars().first()
    if event is None or event.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "业务事件不存在", status_code=404)
    if event.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return event


async def confirm_event(
    db: AsyncSession,
    user: CurrentUser,
    event_id: str,
    expected_version: int | None,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    """确认事件：同一事务生成资金变动 + 审计。"""
    event = await get_event(db, user, event_id)

    # 版本冲突优先判断：客户端提交的是陈旧数据 → 409 数据已变化，请刷新
    if expected_version is not None and event.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)

    if event.status != "draft":
        raise AppError(ErrorCode.STATE_INVALID, "只有草稿状态的事件可以确认", status_code=409)

    before = event_to_dict(event)
    event.status = "confirmed"
    event.confirmed_at = datetime.now(timezone.utc)
    event.version += 1
    event.updated_by = user.user_id

    # 生成资金变动（M2：资产购置/处置生成账户 + 资产两条变动）
    for spec in movement_specs(event):
        db.add(
            EventMovement(
                id=new_id(),
                household_id=user.household_id,
                event_id=event.id,
                target_type=spec["target_type"],
                target_id=spec["target_id"],
                direction=spec["direction"],
                amount=event.amount or 0,
                currency=event.currency,
                created_by=user.user_id,
                updated_by=user.user_id,
            )
        )

    after = event_to_dict(event)
    await audit_service.record_audit(
        db,
        action="event.confirm",
        action_label="确认业务事件",
        object_type="business_event",
        object_id=event.id,
        before=before,
        after=after,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return event


async def reverse_event(
    db: AsyncSession,
    user: CurrentUser,
    event_id: str,
    reason: str,
    expected_version: int | None,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "冲销必须填写原因",
                       field_errors={"reason": "请填写冲销原因"})
    event = await get_event(db, user, event_id)
    if expected_version is not None and event.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)
    if event.status != "confirmed":
        raise AppError(ErrorCode.STATE_INVALID, "只有已确认事件可以冲销", status_code=409)

    before = event_to_dict(event)
    event.status = "reversed"
    event.version += 1
    event.updated_by = user.user_id

    # 资产处置事件被冲销：资产恢复处置前状态（状态来自处置时的审计记录）
    if event.event_type == "asset_dispose" and event.asset_id:
        from app.services import assets as asset_service

        await asset_service.restore_status_after_reversal(db, event.asset_id, user)

    # 其余派生态按事件类型回滚：冲销必须真正生效，不能只改事件状态
    from decimal import Decimal

    from sqlalchemy import or_

    from app.db.models.finance import CreditCardBill, InvestmentTrade, LiabilityRepayment
    from app.db.models.leases import LeaseReceivable

    def _num(value) -> Decimal:
        return Decimal(str(value or 0))

    # 投资交易：置为 reversed；持仓与投资账户余额都只统计 confirmed，自动回滚
    trades = list((await db.execute(
        select(InvestmentTrade).where(
            InvestmentTrade.event_id == event.id,
            InvestmentTrade.deleted_at.is_(None),
            InvestmentTrade.status == "confirmed",
        )
    )).scalars().all())
    for trade in trades:
        trade.status = "reversed"
        trade.updated_by = user.user_id

    # 租赁收租：对应应收回到未收，收租率与逾期按新状态重算
    lease_receivables = list((await db.execute(
        select(LeaseReceivable).where(
            LeaseReceivable.event_id == event.id,
            LeaseReceivable.deleted_at.is_(None),
        )
    )).scalars().all())
    for item in lease_receivables:
        item.status = "pending"
        item.paid_at = None
        item.paid_amount = Decimal("0")
        item.event_id = None
        item.updated_by = user.user_id

    # 负债还款：回滚关联信用卡账单的已还金额（多还的溢缴款不在账单里，最多减到 0）
    repayments = list((await db.execute(
        select(LiabilityRepayment).where(
            LiabilityRepayment.deleted_at.is_(None),
            or_(
                LiabilityRepayment.principal_event_id == event.id,
                LiabilityRepayment.interest_event_id == event.id,
            ),
        )
    )).scalars().all())
    for repayment in repayments:
        if not repayment.bill_id:
            continue
        bill = (await db.execute(
            select(CreditCardBill).where(CreditCardBill.id == repayment.bill_id)
        )).scalars().first()
        if bill is None:
            continue
        bill.paid_amount = max(Decimal("0"), _num(bill.paid_amount) - _num(repayment.amount))
        bill.version += 1
        bill.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="event.reverse",
        action_label="冲销业务事件",
        object_type="business_event",
        object_id=event.id,
        before=before,
        after=event_to_dict(event),
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return event


async def cancel_event(
    db: AsyncSession,
    user: CurrentUser,
    event_id: str,
    expected_version: int | None,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    """取消草稿（11 §2：draft → cancelled，终态，保留历史，不进统计）。"""
    event = await get_event(db, user, event_id)
    if expected_version is not None and event.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)
    if event.status != "draft":
        raise AppError(ErrorCode.STATE_INVALID, "只有草稿状态的事件可以取消", status_code=409)

    before = event_to_dict(event)
    event.status = "cancelled"
    event.version += 1
    event.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="event.cancel",
        action_label="取消业务事件草稿",
        object_type="business_event",
        object_id=event.id,
        before=before,
        after=event_to_dict(event),
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return event


async def update_draft(
    db: AsyncSession,
    user: CurrentUser,
    event_id: str,
    payload: dict,
    expected_version: int | None,
    ctx: RequestContext | None = None,
) -> BusinessEvent:
    """编辑草稿（23 §6「编辑草稿」动作）。仅 draft 可编辑；事件类型不可变更。"""
    event = await get_event(db, user, event_id)
    if expected_version is not None and event.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)
    if event.status != "draft":
        raise AppError(ErrorCode.STATE_INVALID, "只有草稿状态的事件可以编辑", status_code=409)

    before = event_to_dict(event)
    field_errors: dict[str, str] = {}

    if "event_type" in payload and payload.get("event_type") not in (None, "") \
            and payload["event_type"] != event.event_type:
        field_errors["event_type"] = "草稿事件类型不可修改，请取消后重新创建"

    # 关联对象先校验（可能直接抛 422，未通过则不写任何字段）
    relation_keys = ("account_id", "asset_id", "liability_id",
                     "investment_account_id", "lease_id", "category_id")
    merged_relations = None
    if any(k in payload for k in relation_keys):
        merged = {
            "event_type": event.event_type,
            "account_id": payload.get("account_id", event.account_id),
            "asset_id": payload.get("asset_id", event.asset_id),
            "liability_id": payload.get("liability_id", event.liability_id),
            "investment_account_id": payload.get("investment_account_id", event.investment_account_id),
            "lease_id": payload.get("lease_id", event.lease_id),
            "category_id": payload.get("category_id", event.category_id),
        }
        try:
            merged_relations = await _validate_relations(db, user, merged)
        except AppError as exc:
            for k, v in (exc.field_errors or {}).items():
                field_errors[k] = v

    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            field_errors["title"] = "请填写标题"
        elif len(title) > MAX_TITLE_LENGTH:
            field_errors["title"] = f"标题最多 {MAX_TITLE_LENGTH} 个字符"

    if "amount" in payload and payload["amount"] not in (None, ""):
        try:
            amount_value = float(payload["amount"])
        except (TypeError, ValueError):
            field_errors["amount"] = "金额格式不正确"
        else:
            if amount_value < 0:
                field_errors["amount"] = "金额不能为负数"
            elif amount_value > MAX_AMOUNT:
                field_errors["amount"] = "金额超出允许范围"

    if "occurred_at" in payload:
        occurred_at = payload.get("occurred_at")
        if isinstance(occurred_at, str) and occurred_at:
            try:
                to_local_naive(datetime.fromisoformat(occurred_at.replace("Z", "+00:00")))
            except ValueError:
                field_errors["occurred_at"] = "时间格式应为 ISO 8601"

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "草稿编辑校验失败", field_errors=field_errors)

    # 全部校验通过后统一写入
    if "title" in payload:
        event.title = (payload.get("title") or "").strip()
    if "amount" in payload and payload["amount"] not in (None, ""):
        event.amount = float(payload["amount"])
        event.original_amount = event.amount
    if "occurred_at" in payload:
        occurred_at = payload.get("occurred_at")
        event.occurred_at = (
            to_local_naive(datetime.fromisoformat(occurred_at.replace("Z", "+00:00")))
            if isinstance(occurred_at, str) and occurred_at
            else local_now()
        )
    if merged_relations is not None:
        event.account_id = payload.get("account_id", event.account_id)
        event.asset_id = payload.get("asset_id", event.asset_id)
        event.liability_id = payload.get("liability_id", event.liability_id)
        event.investment_account_id = payload.get("investment_account_id", event.investment_account_id)
        event.lease_id = payload.get("lease_id", event.lease_id)
        event.category_id = payload.get("category_id", event.category_id)
        for obj in (merged_relations.get("account"), merged_relations.get("liability"),
                    merged_relations.get("investment")):
            if obj is not None:
                event.currency = obj.currency
                break
    for plain in ("counterparty", "note", "member_id"):
        if plain in payload:
            setattr(event, plain, payload[plain])

    event.version += 1
    event.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="event.update_draft",
        action_label="编辑业务事件草稿",
        object_type="business_event",
        object_id=event.id,
        before=before,
        after=event_to_dict(event),
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return event


async def list_events(
    db: AsyncSession,
    user: CurrentUser,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    event_type: str | None = None,
    event_types: list[str] | None = None,
    account_id: str | None = None,
    asset_id: str | None = None,
    category_id: str | None = None,
    member_id: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
) -> tuple[list[BusinessEvent], int]:
    """流水列表（06 §4：筛选期间、收入/支出/转账、账户、分类、成员、金额区间、来源、确认状态）。"""
    conditions = [BusinessEvent.household_id == user.household_id, BusinessEvent.deleted_at.is_(None)]
    if status:
        conditions.append(BusinessEvent.status == status)
    if event_type:
        conditions.append(BusinessEvent.event_type == event_type)
    if event_types:
        conditions.append(BusinessEvent.event_type.in_(event_types))
    if account_id:
        conditions.append(BusinessEvent.account_id == account_id)
    if asset_id:
        conditions.append(BusinessEvent.asset_id == asset_id)
    if category_id:
        conditions.append(BusinessEvent.category_id == category_id)
    if member_id:
        conditions.append(BusinessEvent.member_id == member_id)
    if source:
        conditions.append(BusinessEvent.source == source)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(BusinessEvent.title.ilike(like) | BusinessEvent.counterparty.ilike(like))
    if date_from is not None:
        conditions.append(BusinessEvent.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(BusinessEvent.occurred_at < date_to)
    if amount_min is not None:
        conditions.append(BusinessEvent.amount >= amount_min)
    if amount_max is not None:
        conditions.append(BusinessEvent.amount <= amount_max)

    stmt = select(BusinessEvent).where(*conditions)
    count_stmt = select(func.count(BusinessEvent.id)).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = list(
        (
            await db.execute(
                stmt.order_by(BusinessEvent.occurred_at.desc(), BusinessEvent.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    return rows, total


async def movements_for(db: AsyncSession, event_id: str) -> list[EventMovement]:
    return list(
        (await db.execute(select(EventMovement).where(EventMovement.event_id == event_id)))
        .scalars().all()
    )


async def attach_category_names(db: AsyncSession, items: list[dict]) -> None:
    """给事件补上分类名，供列表统一显示「方向 + 分类」。

    只回填事件实际选中的那个分类名（二级优先）：例如「房贷还款」而不是
    「还款 / 房贷还款」——方向列已经表达了收支，分类列再拼父级是冗余。
    没有分类的事件（内部转账、资产购置等）保持 None，前端显示占位符。
    """
    from app.db.models.config import Category

    ids = {item.get("category_id") for item in items if item.get("category_id")}
    if not ids:
        return
    rows = (await db.execute(
        select(Category.id, Category.name).where(Category.id.in_(ids))
    )).all()
    name_by_id = {cid: name for cid, name in rows}

    for item in items:
        cid = item.get("category_id")
        if cid and cid in name_by_id:
            item["category_name"] = name_by_id[cid]


async def attach_repayments(db: AsyncSession, items: list[dict]) -> None:
    """给还款相关事件补上「还款单」信息。

    登记还款会拆成两条事件（本金 + 利息与费用）。流水页只应看到一笔「还款」，
    所以这里把同一笔还款的两条事件都标上同一个 repayment，前端据此合并展示，
    点开详情再拆成本金 / 利息 / 费用。
    """
    from app.db.models.finance import Liability, LiabilityRepayment

    ids = [item["id"] for item in items if item.get("event_type") in ("liability_repay", "expense")]
    if not ids:
        return
    rows = list((await db.execute(
        select(LiabilityRepayment).where(
            LiabilityRepayment.deleted_at.is_(None),
            (LiabilityRepayment.principal_event_id.in_(ids))
            | (LiabilityRepayment.interest_event_id.in_(ids)),
        )
    )).scalars().all())
    if not rows:
        return
    names = {
        row[0]: row[1]
        for row in (await db.execute(
            select(Liability.id, Liability.name).where(
                Liability.id.in_([r.liability_id for r in rows]))
        )).all()
    }
    by_event: dict[str, tuple[LiabilityRepayment, str]] = {}
    for repayment in rows:
        if repayment.principal_event_id:
            by_event[repayment.principal_event_id] = (repayment, "principal")
        if repayment.interest_event_id:
            by_event[repayment.interest_event_id] = (repayment, "interest")
    for item in items:
        entry = by_event.get(item["id"])
        if entry is None:
            continue
        repayment, leg = entry
        item["repayment"] = {
            "id": repayment.id,
            "amount": fmt_amount(repayment.amount),
            "principal": fmt_amount(repayment.principal),
            "interest": fmt_amount(repayment.interest),
            "fee": fmt_amount(repayment.fee),
            "liability_id": repayment.liability_id,
            "liability_name": names.get(repayment.liability_id, ""),
            "leg": leg,
            "occurred_at": local_iso(repayment.occurred_at) if repayment.occurred_at else None,
        }
