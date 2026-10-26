"""家庭台账 API（06、18 §4 M1 核心账务、23 §4/§7）。

M1 已交付：分类、账户、统一业务事件、资金变动、收入、支出、内部转账、基础统计。
未交付模块的列表接口返回空列表 +「模块未启用」说明，不创建假数据（18 §7）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Body, File, Form, Header, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUserDep, Ctx, DbSession
from app.core.timeutil import local_now, to_local_naive
from app.core.errors import AppError, ErrorCode, list_payload, success
from decimal import Decimal

from app.core.money import fmt_amount
from app.db.models.business import Asset, BusinessEvent, EventMovement
from app.services import accounts as account_service
from app.services import catalog as catalog_service
from app.services import events as event_service
from app.services import transfers as transfer_service
from app.services.permissions import require_action

router = APIRouter(prefix="/ledger", tags=["家庭台账"])

MODULE_PENDING = "该模块将在对应交付阶段启用，当前不可写入。"
NEXT_MODULE = {
    "assets": "资产模块",
    "reconciliation": "对账模块",
    "liabilities": "负债模块",
    "investments": "投资模块",
    "leases": "租赁模块",
    "capture": "智能录入模块",
    "reminders": "提醒与通知增强模块",
}


def _pending(module: str) -> dict:
    return {
        "items": [], "total": 0, "page": 1, "page_size": 20, "has_next": False,
        "module_enabled": False,
        "pending_module": NEXT_MODULE.get(module, "后续模块"),
        "note": MODULE_PENDING,
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return to_local_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        raise AppError(ErrorCode.VALIDATION_ERROR, "日期格式应为 ISO 8601",
                       field_errors={"date": "日期格式应为 ISO 8601"})


def _date_to_inclusive(value: str | None) -> datetime | None:
    """列表的「截止日期」：纯日期（YYYY-MM-DD）表示含当天，补到 23:59:59.999999。

    列表查询用的是 occurred_at < date_to，直接传当日 00:00 会把当天事件整日排除。
    """
    dt = _parse_dt(value)
    if dt is None:
        return None
    if isinstance(value, str) and len(value.strip()) == 10:
        dt = dt + timedelta(days=1) - timedelta(microseconds=1)
    return dt


# ---------------- 分类 ----------------

@router.get("/categories", summary="分类列表")
async def list_categories(db: DbSession, user: CurrentUserDep,
                          domain: str | None = Query(None)) -> dict:
    rows = await catalog_service.list_categories(db, user.household_id or "", domain)
    items = [catalog_service.serialize_category(c) for c in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


# ---------------- 账户（M1） ----------------

@router.get("/accounts", summary="账户列表（含余额）")
async def list_accounts(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    account_type: str | None = Query(None),
    status: str | None = Query(None),
    category_id: str | None = Query(None),
) -> dict:
    rows = await account_service.list_accounts(
        db, user, keyword=keyword, account_type=account_type, status=status,
        category_id=category_id,
    )
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    items = [
        account_service.serialize_account(
            acc, bal,
            category_label=cat_map.get(acc.category_id) if acc.category_id else None,
        )
        for acc, bal in rows
    ]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/accounts", summary="新增账户")
async def create_account(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                         payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    account = await account_service.create_account(db, user, payload, ctx)
    await db.commit()
    balance = await account_service.balance_of(db, account)
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    return success(
        account_service.serialize_account(
            account, balance,
            category_label=cat_map.get(account.category_id) if account.category_id else None,
        ),
        message="账户已创建",
    )


@router.get("/accounts/{account_id}", summary="账户详情（含余额与流水）")
async def get_account(db: DbSession, user: CurrentUserDep, account_id: str) -> dict:
    account = await account_service.get_account(db, user, account_id)
    balance = await account_service.balance_of(db, account)
    flow = await account_service.account_flow(db, user, account_id)
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    data = account_service.serialize_account(
        account, balance,
        category_label=cat_map.get(account.category_id) if account.category_id else None,
    )
    data["flow"] = [
        {
            "event_id": event.id,
            "event_no": event.event_no,
            "event_type": event.event_type,
            "title": event.title,
            "amount": fmt_amount(movement.amount or 0),
            "direction": movement.direction,
            "direction_label": "转入" if movement.direction == "in" else "转出",
            "currency": movement.currency,
            "status": event.status,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        }
        for event, movement in flow
    ]
    return success(data)


@router.put("/accounts/{account_id}", summary="编辑账户")
async def update_account(db: DbSession, ctx: Ctx, user: CurrentUserDep, account_id: str,
                         payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    account = await account_service.update_account(
        db, user, account_id, payload, payload.get("version"), ctx
    )
    await db.commit()
    balance = await account_service.balance_of(db, account)
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    return success(
        account_service.serialize_account(
            account, balance,
            category_label=cat_map.get(account.category_id) if account.category_id else None,
        ),
        message="账户已更新",
    )


@router.delete("/accounts/{account_id}", summary="停用账户（需二次确认）")
async def archive_account(db: DbSession, ctx: Ctx, user: CurrentUserDep, account_id: str,
                          reason: str = Query(""), confirm_token: str = Query(None)) -> dict:
    require_action(user, "archive")
    from app.services import confirm as confirm_service

    confirm_service.consume_token(confirm_token, user, "account.archive", "account", account_id)
    account = await account_service.archive_account(db, user, account_id, reason, ctx)
    await db.commit()
    return success({"id": account.id, "status": account.status}, message="账户已停用")


# ---------------- 统一业务事件（M1） ----------------


async def _serialize_events_with_category(
    db, events, movements_map: dict | None = None,
) -> list[dict]:
    """事件序列化 + 批量补分类名：列表/详情的「类型」列统一按方向显示。"""
    from app.services import events as event_service

    items = [
        event_service.serialize_event(e, (movements_map or {}).get(e.id)) for e in events
    ]
    await event_service.attach_category_names(db, items)
    return items


@router.get("/events", summary="业务事件列表（全量筛选）")
async def list_events(
    db: DbSession, user: CurrentUserDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    event_type: str | None = Query(None),
    direction: str | None = Query(None, description="income / expense / transfer / asset 分组筛选"),
    account_id: str | None = Query(None),
    asset_id: str | None = Query(None),
    category_id: str | None = Query(None),
    member_id: str | None = Query(None),
    source: str | None = Query(None),
    keyword: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    amount_min: float | None = Query(None),
    amount_max: float | None = Query(None),
) -> dict:
    event_types: list[str] | None = None
    if direction == "income":
        event_types = ["income"]
    elif direction == "expense":
        event_types = ["expense"]
    elif direction == "transfer":
        event_types = ["transfer"]
    elif direction == "asset":
        event_types = ["asset_acquire", "asset_dispose"]
    elif direction in ("inflow", "outflow"):
        # 资金转入 / 转出：钱动了但不计入收支（回款、借款到账、买卖资产等）
        from app.services.events import _FLOW_BY_EVENT_TYPE
        event_types = [k for k, v in _FLOW_BY_EVENT_TYPE.items() if v[0] == direction]
    elif direction == "neutral":
        from app.services.events import _FLOW_BY_EVENT_TYPE
        event_types = [k for k, v in _FLOW_BY_EVENT_TYPE.items() if v[0] == "neutral"]

    rows, total = await event_service.list_events(
        db, user, page=page, page_size=page_size, status=status, event_type=event_type,
        event_types=event_types, account_id=account_id, asset_id=asset_id,
        category_id=category_id,

        member_id=member_id, source=source, keyword=keyword,
        date_from=_parse_dt(date_from), date_to=_date_to_inclusive(date_to),
        amount_min=amount_min, amount_max=amount_max,
    )
    items = [event_service.serialize_event(e) for e in rows]
    await event_service.attach_category_names(db, items)
    # 同一笔还款的本金 / 利息两条事件带上同一个还款单，前端合并成一行展示
    await event_service.attach_repayments(db, items)
    return list_payload(items, total, page, page_size)


@router.post("/events", summary="创建业务事件草稿")
async def create_event(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                       payload: dict = Body(...),
                       idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict:
    require_action(user, "create_draft")
    from app.services import idempotency as idem_service

    # 防重复提交：同一幂等键重复到达时直接返回上次结果，不再创建第二条
    key = idem_service.key_from(payload, idempotency_key)
    record, replayed = await idem_service.guard(
        db, user.household_id, "event.create", key, payload)
    if replayed:
        if not (record and record.object_id):
            raise idem_service.ReplayInProgress()
        existing = await event_service.get_event(db, user, record.object_id)
        return success(event_service.serialize_event(existing),
                       message="该请求已处理过，已返回原记录（未重复创建）")

    event = await event_service.create_draft(db, user, payload, ctx)
    idem_service.complete_idempotent(record, event.id)
    await db.commit()
    return success(event_service.serialize_event(event),
                   message="草稿已保存，确认后才进入统计")


@router.get("/recurring-expenses", summary="周期固定支出列表")
async def list_recurring(db: DbSession, user: CurrentUserDep,
                         status: str | None = Query(None)) -> dict:
    from app.services import recurring as recurring_service
    from app.services import catalog as catalog_service

    rows = await recurring_service.list_recurring(db, user, status=status)
    names = await catalog_service.category_label_map(db, user.household_id)
    items = [recurring_service.serialize(r, category_label=names.get(r.category_id or "")) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/recurring-expenses", summary="新增周期固定支出")
async def create_recurring(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                           payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import recurring as recurring_service

    item = await recurring_service.create_recurring(db, user, payload, ctx)
    await db.commit()
    return success(recurring_service.serialize(item), message="固定支出已登记，到期将自动记账")


@router.put("/recurring-expenses/{item_id}", summary="编辑周期固定支出")
async def update_recurring(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str,
                           payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring as recurring_service

    item = await recurring_service.update_recurring(db, user, item_id, payload, ctx)
    await db.commit()
    return success(recurring_service.serialize(item), message="固定支出已更新")


@router.post("/recurring-expenses/{item_id}/pause", summary="暂停固定支出")
async def pause_recurring(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring as recurring_service

    item = await recurring_service.pause_recurring(db, user, item_id, ctx)
    await db.commit()
    return success(recurring_service.serialize(item), message="已暂停")


@router.post("/recurring-expenses/{item_id}/resume", summary="恢复固定支出")
async def resume_recurring(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring as recurring_service

    item = await recurring_service.resume_recurring(db, user, item_id, ctx)
    await db.commit()
    return success(recurring_service.serialize(item), message="已恢复")


@router.delete("/recurring-expenses/{item_id}", summary="删除固定支出")
async def delete_recurring(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring as recurring_service

    await recurring_service.delete_recurring(db, user, item_id, ctx)
    await db.commit()
    return success(None, message="已删除")


# ---------------- 周期固定收入（工作维度 · 工资/养老金等） ----------------

@router.get("/recurring-incomes", summary="周期固定收入列表")
async def list_recurring_incomes(db: DbSession, user: CurrentUserDep,
                                 status: str | None = Query(None)) -> dict:
    from app.services import recurring_income as income_service
    from app.services import catalog as catalog_service

    rows = await income_service.list_recurring_incomes(db, user, status=status)
    names = await catalog_service.category_label_map(db, user.household_id)
    items = [income_service.serialize(r, category_label=names.get(r.category_id or "")) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/recurring-incomes", summary="新增周期固定收入")
async def create_recurring_income(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                                  payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import recurring_income as income_service

    item = await income_service.create_recurring_income(db, user, payload, ctx)
    await db.commit()
    return success(income_service.serialize(item), message="固定收入已登记，到期将自动记账")


@router.put("/recurring-incomes/{item_id}", summary="编辑周期固定收入")
async def update_recurring_income(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str,
                                  payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring_income as income_service

    item = await income_service.update_recurring_income(db, user, item_id, payload, ctx)
    await db.commit()
    return success(income_service.serialize(item), message="固定收入已更新")


@router.post("/recurring-incomes/{item_id}/pause", summary="暂停固定收入")
async def pause_recurring_income(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring_income as income_service

    item = await income_service.pause_recurring_income(db, user, item_id, ctx)
    await db.commit()
    return success(income_service.serialize(item), message="已暂停")


@router.post("/recurring-incomes/{item_id}/resume", summary="恢复固定收入")
async def resume_recurring_income(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring_income as income_service

    item = await income_service.resume_recurring_income(db, user, item_id, ctx)
    await db.commit()
    return success(income_service.serialize(item), message="已恢复")


@router.delete("/recurring-incomes/{item_id}", summary="删除固定收入")
async def delete_recurring_income(db: DbSession, ctx: Ctx, user: CurrentUserDep, item_id: str) -> dict:
    require_action(user, "edit_draft")
    from app.services import recurring_income as income_service

    await income_service.delete_recurring_income(db, user, item_id, ctx)
    await db.commit()
    return success(None, message="已删除")


# ---------------- 工作档案（成员在职状态 + 社保公积金基数） ----------------

@router.get("/employment", summary="工作档案列表")
async def list_employment(db: DbSession, user: CurrentUserDep) -> dict:
    from app.services import employment as employment_service

    rows = await employment_service.list_profiles(db, user)
    items = [employment_service.serialize(r) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/employment", summary="新增工作档案")
async def create_employment(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                            payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import employment as employment_service

    item = await employment_service.create_profile(db, user, payload, ctx)
    await db.commit()
    return success(employment_service.serialize(item), message="工作档案已创建")


@router.put("/employment/{profile_id}", summary="编辑工作档案")
async def update_employment(db: DbSession, ctx: Ctx, user: CurrentUserDep, profile_id: str,
                            payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import employment as employment_service

    item = await employment_service.update_profile(db, user, profile_id, payload, ctx)
    await db.commit()
    return success(employment_service.serialize(item), message="工作档案已更新")


@router.delete("/employment/{profile_id}", summary="删除工作档案")
async def delete_employment(db: DbSession, ctx: Ctx, user: CurrentUserDep, profile_id: str) -> dict:
    require_action(user, "edit_draft")
    from app.services import employment as employment_service

    await employment_service.delete_profile(db, user, profile_id, ctx)
    await db.commit()
    return success(None, message="已删除")


@router.get("/events/{event_id}", summary="业务事件详情")
async def get_event(db: DbSession, user: CurrentUserDep, event_id: str) -> dict:
    event = await event_service.get_event(db, user, event_id)
    movements = await event_service.movements_for(db, event_id)
    return success(event_service.serialize_event(event, movements))


@router.post("/events/{event_id}/confirm", summary="确认业务事件")
async def confirm_event(db: DbSession, ctx: Ctx, user: CurrentUserDep, event_id: str,
                        payload: dict = Body(default={})) -> dict:
    require_action(user, "confirm")
    event = await event_service.confirm_event(db, user, event_id, payload.get("version"), ctx)
    await db.commit()
    movements = await event_service.movements_for(db, event.id)
    return success(event_service.serialize_event(event, movements),
                   message="事件已确认，已生成资金变动与审计记录")


@router.post("/events/{event_id}/reverse", summary="冲销业务事件")
async def reverse_event(db: DbSession, ctx: Ctx, user: CurrentUserDep, event_id: str,
                        payload: dict = Body(...)) -> dict:
    require_action(user, "reverse")
    event = await event_service.reverse_event(
        db, user, event_id, payload.get("reason", ""), payload.get("version"), ctx
    )
    await db.commit()
    return success(event_service.serialize_event(event), message="事件已冲销，历史记录已保留")


@router.post("/events/{event_id}/cancel", summary="取消业务事件草稿")
async def cancel_event(db: DbSession, ctx: Ctx, user: CurrentUserDep, event_id: str,
                       payload: dict = Body(default={})) -> dict:
    require_action(user, "edit_draft")
    event = await event_service.cancel_event(db, user, event_id, payload.get("version"), ctx)
    await db.commit()
    return success(event_service.serialize_event(event), message="草稿已取消，不计入任何统计")


@router.put("/events/{event_id}", summary="编辑业务事件草稿")
async def update_event_draft(db: DbSession, ctx: Ctx, user: CurrentUserDep, event_id: str,
                             payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    event = await event_service.update_draft(
        db, user, event_id, payload, payload.get("version"), ctx
    )
    await db.commit()
    return success(event_service.serialize_event(event), message="草稿已更新")


# ---------------- 内部转账（M1） ----------------

@router.get("/transfers", summary="转账列表")
async def list_transfers(
    db: DbSession, user: CurrentUserDep,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    date_from: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="结束日期 YYYY-MM-DD（含当天）"),
    account_id: str | None = Query(None, description="只看与该账户相关的转账"),
    keyword: str | None = Query(None, description="单号或备注关键字"),
) -> dict:
    rows, total = await transfer_service.list_transfers(
        db, user, page=page, page_size=page_size,
        date_from=date_from, date_to=date_to, account_id=account_id, keyword=keyword,
    )
    items = [transfer_service.serialize_transfer(g, f, t) for g, f, t in rows]
    return list_payload(items, total, page, page_size)


@router.post("/transfers", summary="内部转账")
async def create_transfer(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                          payload: dict = Body(...),
                          idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict:
    require_action(user, "confirm")
    from app.services import idempotency as idem_service

    key = idem_service.key_from(payload, idempotency_key)
    record, replayed = await idem_service.guard(
        db, user.household_id, "transfer.create", key, payload)
    if replayed:
        if not (record and record.object_id):
            raise idem_service.ReplayInProgress()
        row = await transfer_service.get_transfer(db, user, record.object_id)
        return success(transfer_service.serialize_transfer(*row),
                       message="该转账已处理过，已返回原记录（未重复创建）")

    group, from_acc, to_acc = await transfer_service.create_transfer(db, user, payload, ctx)
    idem_service.complete_idempotent(record, group.id)
    await db.commit()
    return success(transfer_service.serialize_transfer(group, from_acc, to_acc),
                   message="转账已确认，已生成转账组，仅改变账户余额")


@router.post("/transfers/{group_id}/reverse", summary="冲销内部转账")
async def reverse_transfer(db: DbSession, ctx: Ctx, user: CurrentUserDep, group_id: str,
                           payload: dict = Body(...)) -> dict:
    require_action(user, "reverse")
    group = await transfer_service.reverse_transfer(
        db, user, group_id, payload.get("reason", ""), ctx
    )
    await db.commit()
    return success(transfer_service.serialize_transfer(group), message="转账已冲销，历史记录已保留")


# ---------------- 基础统计（M1） ----------------

@router.get("/summary", summary="台账基础统计")
async def ledger_summary(db: DbSession, user: CurrentUserDep,
                         period: str = Query("current_month")) -> dict:
    from app.services import stats as stats_service

    snapshot = await stats_service.dashboard_snapshot(db, user, period)
    return success({
        "period": snapshot["period"],
        "period_label": snapshot["period_label"],
        "period_range": snapshot["period_range"],
        "base_currency": snapshot["base_currency"],
        "caliber": snapshot["caliber"],
        "counts": snapshot["counts"],
        "accounts": snapshot["accounts"],
        "assets": snapshot["assets"],
        "liabilities": snapshot["liabilities"],
        "investments": snapshot["investments"],
        "net_worth": snapshot["net_worth"],
        "metrics": snapshot["metrics"],
        "charts": snapshot["charts"],
        "data_updated_at": snapshot["data_updated_at"],
    })


# ---------------- 资产（M2） ----------------

AI_ASSET_CAPABILITIES = {
    "allowed": [
        "分析资产占比、价值变化与集中度",
        "提示长期未更新估值的资产",
        "说明资产与关联负债的覆盖关系",
    ],
    "forbidden": [
        "自动修改资产当前价值或新增估值",
        "自动处置、归档或删除资产",
        "在缺少来源与日期时编造参考价格",
    ],
}

MODULE_NOTES = {
    "liabilities": "房贷/车贷的余额、利率与还款由负债模块统一管理，可点击进入负债详情查看与登记还款",
    "leases": "租金、押金与逾期由租赁模块统一管理，可点击进入租赁详情查看与登记",
    "investments": "投资账户与持仓由投资模块统一管理",
}

TYPED_TYPES = {
    "real-estates": "real_estate",
    "parking-spaces": "parking",
    "vehicles": "vehicle",
    "precious-metals": "precious_metal",
}


def _mask_reveal(user: CurrentUserDep, reveal: bool) -> bool:
    """敏感字段查看权限：证件号/VIN/车牌/户号默认掩码，仅审计查看权限可看完整值。"""
    if not reveal:
        return False
    if "audit_view" not in user.action_set:
        raise AppError(ErrorCode.FORBIDDEN, "查看完整敏感字段需要审计查看权限", status_code=403)
    return True


async def _asset_items_payload(
    db: DbSession | AsyncSession, user: CurrentUserDep, assets: list, *,
    total: int, page: int, page_size: int, reveal: bool, summary: dict | None = None,
) -> dict:
    from app.services import asset_extensions as ext_service
    from app.services import assets as asset_service

    extensions = await ext_service.extensions_for(db, assets)
    cat_map = await asset_service.category_label_map(db, user.household_id)
    items = [
        asset_service.serialize_asset(
            a, extension=extensions.get(a.id), reveal=reveal,
            category_label=cat_map.get(a.category_id) if a.category_id else None,
        )
        for a in assets
    ]
    payload = {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": page * page_size < total,
        "caliber": asset_service.CALIBER,
        "module_notes": MODULE_NOTES,
    }
    if summary is not None:
        payload["summary"] = summary
    return payload


@router.get("/assets/summary", summary="资产顶部统计（06 §2）")
async def assets_summary(db: DbSession, user: CurrentUserDep) -> dict:
    from app.services import assets as asset_service

    household = await _household_base_currency(db)
    return success(await asset_service.asset_summary(db, user.household_id or "", household))


@router.get("/assets", summary="资产列表（含顶部统计）")
async def list_assets(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    asset_type: str | None = Query(None),
    category_id: str | None = Query(None),
    member_id: str | None = Query(None),
    status: str | None = Query(None),
    liquidity: str | None = Query(None),
    valuation_from: str | None = Query(None),
    valuation_to: str | None = Query(None),
    pending_valuation: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    reveal: bool = Query(False),
) -> dict:
    from app.services import assets as asset_service

    rows, total = await asset_service.list_assets(
        db, user, keyword=keyword, asset_type=asset_type, category_id=category_id,
        member_id=member_id,
        status=status, liquidity=liquidity,
        valuation_from=_parse_dt(valuation_from), valuation_to=_parse_dt(valuation_to),
        pending_valuation=pending_valuation,
        page=page, page_size=page_size,
    )
    summary = await asset_service.asset_summary(db, user.household_id or "", await _household_base_currency(db))
    payload = await _asset_items_payload(
        db, user, rows, total=total, page=page, page_size=page_size,
        reveal=_mask_reveal(user, reveal), summary=summary,
    )
    return success(payload)


@router.post("/assets", summary="新增资产（含可选购置事件）")
async def create_asset(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                       payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    # mode=confirm 会立即生成资金变动、改变账户余额，必须同时具备确认权限
    if (payload.get("acquisition") or {}).get("mode") == "confirm":
        require_action(user, "confirm")
    from app.services import assets as asset_service
    from app.services import asset_extensions as ext_service

    asset, event = await asset_service.create_asset(db, user, payload, ctx)
    await db.commit()
    extension = await ext_service.extension_for(db, asset)
    cat_map = await asset_service.category_label_map(db, user.household_id)
    data = asset_service.serialize_asset(
        asset, extension=extension,
        category_label=cat_map.get(asset.category_id) if asset.category_id else None,
    )
    if event is not None:
        data["acquisition_event"] = {
            "id": event.id, "event_no": event.event_no, "status": event.status,
            "status_label": "已确认" if event.status == "confirmed" else "草稿",
        }
    return success(data, message="资产已创建" + ("，购置事件已确认" if event and event.status == "confirmed" else "，购置事件为草稿"))


@router.post("/assets/ocr-extract", summary="房产证识别（AI 协助录入资产）")
async def asset_ocr_extract(
    db: DbSession, ctx: Ctx, user: CurrentUserDep,
    file: UploadFile = File(...),
) -> dict:
    """上传房产证照片 → OCR 识别 → 提取地址/面积/证号等字段（需用户核对后保存）。"""
    require_action(user, "create_draft")
    from app.services import asset_ocr as asset_ocr_service
    from app.services import audit as audit_service

    result = await asset_ocr_service.extract_property_fields(db, user, file)
    await audit_service.record_audit(
        db, action="asset.ocr_extract", action_label="房产证识别",
        object_type="asset", object_id="draft",
        after={"filled": result["filled"]}, result="success",
        external_provider="baidu_ocr", user=user, ctx=ctx,
    )
    await db.commit()
    return success(result, message=f"识别完成，提取到 {result['filled']} 个字段，请核对后保存")


async def _household_base_currency(db: AsyncSession) -> str:
    from app.services.setup import current_household

    household = await current_household(db)
    return household.base_currency if household else "CNY"


@router.get("/assets/real-estates", summary="不动产列表")
async def list_real_estates(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    member_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    reveal: bool = Query(False),
) -> dict:
    from app.services import assets as asset_service

    rows, total = await asset_service.list_assets(
        db, user, keyword=keyword, asset_type="real_estate", member_id=member_id,
        status=status, page=page, page_size=page_size,
    )
    summary = await asset_service.asset_summary(db, user.household_id or "", await _household_base_currency(db))
    payload = await _asset_items_payload(
        db, user, rows, total=total, page=page, page_size=page_size,
        reveal=_mask_reveal(user, reveal), summary=summary,
    )
    return success(payload)


@router.get("/assets/parking-spaces", summary="车位列表")
async def list_parking_spaces(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    member_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    reveal: bool = Query(False),
) -> dict:
    from app.services import assets as asset_service

    rows, total = await asset_service.list_assets(
        db, user, keyword=keyword, asset_type="parking", member_id=member_id,
        status=status, page=page, page_size=page_size,
    )
    summary = await asset_service.asset_summary(db, user.household_id or "", await _household_base_currency(db))
    payload = await _asset_items_payload(
        db, user, rows, total=total, page=page, page_size=page_size,
        reveal=_mask_reveal(user, reveal), summary=summary,
    )
    return success(payload)


@router.get("/assets/vehicles", summary="车辆列表")
async def list_vehicles(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    member_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    reveal: bool = Query(False),
) -> dict:
    from app.services import assets as asset_service

    rows, total = await asset_service.list_assets(
        db, user, keyword=keyword, asset_type="vehicle", member_id=member_id,
        status=status, page=page, page_size=page_size,
    )
    summary = await asset_service.asset_summary(db, user.household_id or "", await _household_base_currency(db))
    payload = await _asset_items_payload(
        db, user, rows, total=total, page=page, page_size=page_size,
        reveal=_mask_reveal(user, reveal), summary=summary,
    )
    return success(payload)


@router.get("/assets/precious-metals", summary="贵金属列表")
async def list_precious_metals(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    member_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    reveal: bool = Query(False),
) -> dict:
    from app.services import assets as asset_service

    rows, total = await asset_service.list_assets(
        db, user, keyword=keyword, asset_type="precious_metal", member_id=member_id,
        status=status, page=page, page_size=page_size,
    )
    summary = await asset_service.asset_summary(db, user.household_id or "", await _household_base_currency(db))
    payload = await _asset_items_payload(
        db, user, rows, total=total, page=page, page_size=page_size,
        reveal=_mask_reveal(user, reveal), summary=summary,
    )
    return success(payload)


# --- 水电煤户号（15 §3.4）---

@router.get("/assets/real-estates/{asset_id}/utility-accounts", summary="水电煤户号列表")
async def list_utility_accounts(db: DbSession, user: CurrentUserDep, asset_id: str,
                                reveal: bool = Query(False)) -> dict:
    from app.services import asset_extensions as ext_service

    view_full = _mask_reveal(user, reveal)
    rows = await ext_service.list_utility_accounts(db, user, asset_id)
    items = [ext_service.serialize_utility(r, reveal=view_full) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/assets/real-estates/{asset_id}/utility-accounts", summary="新增水电煤户号")
async def create_utility_account(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                                 asset_id: str, payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import asset_extensions as ext_service

    row = await ext_service.create_utility_account(db, user, asset_id, payload, ctx)
    await db.commit()
    return success(ext_service.serialize_utility(row), message="户号已保存")


@router.put("/assets/utility-accounts/{utility_id}", summary="编辑水电煤户号")
async def update_utility_account(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                                 utility_id: str, payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import asset_extensions as ext_service

    row = await ext_service.update_utility_account(db, user, utility_id, payload, ctx)
    await db.commit()
    return success(ext_service.serialize_utility(row), message="户号已更新")


@router.delete("/assets/utility-accounts/{utility_id}", summary="删除水电煤户号（软删除）")
async def delete_utility_account(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                                 utility_id: str, reason: str = Query("")) -> dict:
    require_action(user, "delete")
    from app.services import asset_extensions as ext_service

    row = await ext_service.delete_utility_account(db, user, utility_id, reason, ctx)
    await db.commit()
    return success({"id": row.id}, message="户号已删除，历史保留")


# --- 车辆保险（15 §5.3）---

@router.get("/assets/vehicles/{asset_id}/insurances", summary="车辆保险列表")
async def list_insurances(db: DbSession, user: CurrentUserDep, asset_id: str) -> dict:
    from app.services import asset_extensions as ext_service

    rows = await ext_service.list_insurances(db, user, asset_id)
    items = [ext_service.serialize_insurance(r) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/assets/vehicles/{asset_id}/insurances", summary="新增车辆保险")
async def create_insurance(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                           asset_id: str, payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import asset_extensions as ext_service

    row = await ext_service.create_insurance(db, user, asset_id, payload, ctx)
    await db.commit()
    return success(ext_service.serialize_insurance(row), message="保险记录已保存")


@router.put("/assets/vehicle-insurances/{insurance_id}", summary="编辑车辆保险")
async def update_insurance(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                           insurance_id: str, payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import asset_extensions as ext_service

    row = await ext_service.update_insurance(db, user, insurance_id, payload, ctx)
    await db.commit()
    return success(ext_service.serialize_insurance(row), message="保险记录已更新")


@router.delete("/assets/vehicle-insurances/{insurance_id}", summary="删除车辆保险（软删除）")
async def delete_insurance(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                           insurance_id: str, reason: str = Query("")) -> dict:
    require_action(user, "delete")
    from app.services import asset_extensions as ext_service

    row = await ext_service.delete_insurance(db, user, insurance_id, reason, ctx)
    await db.commit()
    return success({"id": row.id}, message="保险记录已删除，历史保留")


# --- 资产详情与动作 ---

@router.get("/assets/{asset_id}", summary="资产详情（含估值、资金事件、关联）")
async def get_asset(db: DbSession, user: CurrentUserDep, asset_id: str,
                    reveal: bool = Query(False)) -> dict:
    from app.db.models.business import Attachment, EventMovement
    from app.services import asset_extensions as ext_service
    from app.services import assets as asset_service
    from app.services import events as event_service

    view_full = _mask_reveal(user, reveal)
    asset = await asset_service.get_asset(db, user, asset_id)
    extension = await ext_service.extension_for(db, asset)
    valuations = await asset_service.list_valuations(db, user, asset_id, limit=20)

    events, _total = await event_service.list_events(db, user, asset_id=asset_id, page=1, page_size=20)
    movements_map: dict[str, list] = {}
    if events:
        movement_rows = list(
            (await db.execute(
                select(EventMovement).where(EventMovement.event_id.in_([e.id for e in events]))
            )).scalars().all()
        )
        for m in movement_rows:
            movements_map.setdefault(m.event_id, []).append(m)
    attachments = list(
        (await db.execute(
            select(Attachment).where(
                Attachment.object_type == "asset", Attachment.object_id == asset_id,
                Attachment.deleted_at.is_(None),
            ).order_by(Attachment.created_at.desc()).limit(20)
        )).scalars().all()
    )

    related: dict = {
        "liabilities": {
            "items": [],
            "module_enabled": bool(asset.linked_liability_id),
            "linked_liability_id": asset.linked_liability_id,
            "note": MODULE_NOTES["liabilities"],
        },
        "leases": {"items": [], "module_enabled": False, "note": MODULE_NOTES["leases"]},
    }

    # 房贷 / 车贷摘要：由负债模块统一提供（15 §3.2、15 §5.2）
    from app.db.models.finance import Liability as _Liability
    from app.services import liabilities as liability_service

    linked_liabilities = list((await db.execute(
        select(_Liability).where(
            _Liability.household_id == user.household_id,
            _Liability.linked_asset_id == asset_id,
            _Liability.deleted_at.is_(None),
        )
    )).scalars().all())
    if linked_liabilities:
        outstanding = await liability_service.outstanding_for(
            db, user.household_id or "", [l.id for l in linked_liabilities]
        )
        related["liabilities"] = {
            "items": [
                liability_service.serialize_liability(l, outstanding.get(l.id))
                for l in linked_liabilities
            ],
            "module_enabled": True,
            "linked_liability_id": asset.linked_liability_id or linked_liabilities[0].id,
            "note": MODULE_NOTES["liabilities"],
        }

    if asset.asset_type == "real_estate":
        utility_rows = await ext_service.list_utility_accounts(db, user, asset_id)
        related["utility_accounts"] = [ext_service.serialize_utility(r, reveal=view_full) for r in utility_rows]

        # 该房产的租约：不动产详情直接能看到租赁状态，而不是「模块未启用」
        # 水电煤户号按房产自动带进每条租约，不需要在租赁里重复录入
        from app.services import leases as lease_service

        lease_rows = await lease_service.list_leases(db, user, real_estate_id=asset_id)
        utilities = [ext_service.serialize_utility(r) for r in utility_rows]
        lease_items = []
        for lease in lease_rows:
            item = await lease_service.lease_detail(db, user, lease.id)
            item["utilities"] = utilities
            lease_items.append(item)
        related["leases"] = {"items": lease_items, "module_enabled": True}
    if asset.asset_type == "vehicle":
        insurance_rows = await ext_service.list_insurances(db, user, asset_id)
        related["insurances"] = [ext_service.serialize_insurance(r) for r in insurance_rows]
    if asset.asset_type == "other":
        from app.db.models.business import Account as _Account

        from app.services import receivables as receivable_service

        rec = await receivable_service.get_for_asset(db, user, asset_id)
        if rec is not None:
            repaid = await receivable_service.repaid_total(db, rec.id)
            repay_rows = await receivable_service.list_repayments(db, rec.id)
            acc_ids = {r.account_id for r in repay_rows if r.account_id}
            acc_names: dict[str, str] = {}
            if acc_ids:
                acc_names = {
                    row[0]: row[1]
                    for row in (await db.execute(
                        select(_Account.id, _Account.name).where(_Account.id.in_(acc_ids))
                    )).all()
                }
            periods = await receivable_service.list_periods(db, rec.id)
            related["receivable"] = receivable_service.serialize_receivable(
                rec, repaid=repaid,
                repayments=[(r, acc_names.get(r.account_id or "", "")) for r in repay_rows],
                reveal=view_full,
                periods=periods,
            )

    data = asset_service.serialize_asset(
        asset, extension=extension, reveal=view_full,
        category_label=(await asset_service.category_label_map(db, user.household_id)).get(asset.category_id)
        if asset.category_id else None,
        extra={
            "valuations": [asset_service.serialize_valuation(v) for v in valuations],
            "events": await _serialize_events_with_category(db, events, movements_map),
            "attachments": [
                {"id": a.id, "file_name": a.file_name, "mime_type": a.mime_type,
                 "size_bytes": a.size_bytes, "status": a.status}
                for a in attachments
            ],
            "related": related,
            "ai": AI_ASSET_CAPABILITIES,
        },
    )
    return success(data)


@router.put("/assets/{asset_id}", summary="编辑资产（含类型扩展字段）")
async def update_asset(db: DbSession, ctx: Ctx, user: CurrentUserDep, asset_id: str,
                       payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import asset_extensions as ext_service
    from app.services import assets as asset_service

    asset = await asset_service.update_asset(
        db, user, asset_id, payload, payload.get("version"), ctx
    )
    await db.commit()
    extension = await ext_service.extension_for(db, asset)
    cat_map = await asset_service.category_label_map(db, user.household_id)
    return success(
        asset_service.serialize_asset(
            asset, extension=extension,
            category_label=cat_map.get(asset.category_id) if asset.category_id else None,
        ),
        message="资产已更新",
    )


@router.get("/assets/{asset_id}/valuations", summary="估值历史")
async def list_valuations(db: DbSession, user: CurrentUserDep, asset_id: str) -> dict:
    from app.services import assets as asset_service

    rows = await asset_service.list_valuations(db, user, asset_id)
    items = [asset_service.serialize_valuation(v) for v in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.put("/assets/receivables/{asset_id}/periods", summary="设置借出款分期计划")
async def set_receivable_periods(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                                 asset_id: str, payload: dict = Body(...)) -> dict:
    """按月分段生成分期表，例如：首期 6000 × 1、3200 × 22、5000 × 13、3100 × 1。

    金额每期不同的分期，只有逐期登记才能对账；这里按「连续 N 期、每期金额相同」
    的分段描述一次生成，合计必须等于借款本金。
    """
    require_action(user, "edit_draft")
    from app.services import receivables as receivable_service

    rec = await receivable_service.get_for_asset(db, user, asset_id)
    if rec is None:
        raise AppError(ErrorCode.NOT_FOUND, "借出款记录不存在", status_code=404)

    start = payload.get("start_month") or ""
    try:
        start_year, start_month = (int(x) for x in str(start).split("-")[:2])
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "请提供起始月份（YYYY-MM）",
                       field_errors={"start_month": "格式应为 YYYY-MM"})

    segments = payload.get("segments") or []
    if not isinstance(segments, list) or not segments:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请至少填写一段分期",
                       field_errors={"segments": "请至少填写一段分期"})

    rows = receivable_service.build_periods(segments, start_year, start_month)
    await receivable_service.replace_periods(db, user, rec, rows)
    await db.commit()
    return success({"count": len(rows), "total": f"{sum(r['amount'] for r in rows):.2f}"},
                   message=f"已生成 {len(rows)} 期分期计划")


@router.post("/assets/receivables/{asset_id}/repayments", summary="登记借出款回款")
async def add_receivable_repayment(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                                   asset_id: str, payload: dict = Body(...)) -> dict:
    """借出款回款：账户余额增加、应收本金减少，不计入收入；可多次部分回款。"""
    require_action(user, "confirm")
    from app.services import receivables as receivable_service

    _row, summary = await receivable_service.add_repayment(db, user, asset_id, payload, ctx)
    await db.commit()
    return success(summary, message="回款已登记：资金回笼，应收本金减少（不计收入）")


@router.post("/assets/{asset_id}/valuations", summary="新增估值")
async def add_valuation(db: DbSession, ctx: Ctx, user: CurrentUserDep, asset_id: str,
                        payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import assets as asset_service

    valuation = await asset_service.add_valuation(db, user, asset_id, payload, ctx)
    await db.commit()
    return success(asset_service.serialize_valuation(valuation), message="估值已登记，当前价值已更新")


@router.post("/assets/{asset_id}/beike-valuate", summary="贝壳估价（壳e估）")
async def beike_valuate(db: DbSession, ctx: Ctx, user: CurrentUserDep, asset_id: str,
                        payload: dict = Body(...)) -> dict:
    """按房产地址与建筑面积调用贝壳 assessPrice，返回单价/总价；不自动改资产价值。"""
    require_action(user, "edit_draft")
    from app.services import assets as asset_service
    from app.services import asset_extensions as ext_service
    from app.services import audit as audit_service
    from app.services import beike as beike_service

    asset = await asset_service.get_asset(db, user, asset_id)
    if asset.asset_type != "real_estate":
        raise AppError(ErrorCode.VALIDATION_ERROR, "只有不动产可以调用贝壳估价", status_code=422)

    ext = await ext_service.extension_for(db, asset) or {}
    cfg = await beike_service.get_config(db, user.household_id)
    city = (payload.get("city") or (cfg.default_city if cfg else "") or "").strip()
    city_code = (payload.get("city_code") or (cfg.default_city_code if cfg else "") or "").strip()
    address = (payload.get("address") or str(ext.get("address") or "") or asset.name).strip()
    area = str(payload.get("area") or ext.get("building_area") or "").strip()

    errors: dict[str, str] = {}
    if not city:
        errors["city"] = "请填写城市（如：北京市）"
    if not city_code:
        errors["city_code"] = "请填写行政区划代码（如：110000）"
    if not address:
        errors["address"] = "请填写详细地址"
    if not area:
        errors["area"] = "请填写建筑面积（㎡）"
    if errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "估价参数不完整", field_errors=errors, status_code=422)

    result = await beike_service.assess_price(
        db, user.household_id, city=city, city_code=city_code, address=address,
        area=area, district=payload.get("district"), resblock=payload.get("resblock"),
        building=payload.get("building"), unit=payload.get("unit"), floor=payload.get("floor"),
    )
    await audit_service.record_audit(
        db, action="asset.beike_valuate", action_label="贝壳估价",
        object_type="asset", object_id=asset_id,
        after={"city": city, "area": area, "total": result.get("total_price")},
        result="success", external_provider="beike", user=user, ctx=ctx,
    )
    await db.commit()
    return success(result, message="贝壳估价完成（请核对后决定是否采用）")


@router.post("/assets/{asset_id}/dispose", summary="处置资产（需二次确认）")
async def dispose_asset(db: DbSession, ctx: Ctx, user: CurrentUserDep, asset_id: str,
                        payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import assets as asset_service
    from app.services import confirm as confirm_service

    confirm_service.consume_token(
        payload.get("confirm_token"), user, "asset.dispose", "asset", asset_id
    )
    asset, event = await asset_service.dispose_asset(db, user, asset_id, payload, ctx)
    await db.commit()
    return success(
        {
            "id": asset.id,
            "status": asset.status,
            "status_label": "已出售" if asset.status == "disposed" else "已报废",
            "event_id": event.id,
            "event_no": event.event_no,
            "realized_gain": (
                fmt_amount(event.realized_gain) if event.realized_gain is not None else None
            ),
        },
        message="资产已处置，已生成资产处置事件",
    )


@router.post("/assets/{asset_id}/archive", summary="归档资产（需二次确认）")
async def archive_asset(db: DbSession, ctx: Ctx, user: CurrentUserDep, asset_id: str,
                        payload: dict = Body(...)) -> dict:
    require_action(user, "archive")
    from app.services import assets as asset_service
    from app.services import confirm as confirm_service

    confirm_service.consume_token(
        payload.get("confirm_token"), user, "asset.archive", "asset", asset_id
    )
    asset = await asset_service.archive_asset(db, user, asset_id, payload.get("reason", ""), ctx)
    await db.commit()
    return success({"id": asset.id, "status": asset.status}, message="资产已归档，历史记录保留")


# ---------------- 负债与信用卡（M3） ----------------

AI_LIABILITY_CAPABILITIES = {
    "allowed": [
        "说明偿付压力、月供占比与到期日历",
        "解释本金、利息与费用构成",
        "提示即将到期与逾期负债",
    ],
    "forbidden": [
        "自动发起还款或调整还款金额",
        "自动修改剩余本金、利率或还款计划",
        "在没有数据时编造还款能力结论",
    ],
}

AI_INVESTMENT_CAPABILITIES = {
    "allowed": [
        "分析持仓占比、集中度与成本",
        "说明已实现与未实现损益及分红情况",
        "提示缺少估值或估值过期的持仓",
    ],
    "forbidden": [
        "自动下单或调整持仓",
        "自动获取行情或编造价格",
        "把手工估值当作实时行情",
    ],
}


@router.get("/liabilities", summary="负债列表与汇总（06 §6）")
async def list_liabilities(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    liability_type: str | None = Query(None),
    category_id: str | None = Query(None),
    status: str | None = Query(None),
    member_id: str | None = Query(None),
) -> dict:
    from app.services import liabilities as liability_service

    rows = await liability_service.list_liabilities(
        db, user, keyword=keyword, liability_type=liability_type,
        category_id=category_id, status=status, member_id=member_id,
    )
    summary = await liability_service.liability_summary(db, user.household_id or "", await _household_base_currency(db))
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    items = [
        liability_service.serialize_liability(
            liability, outstanding,
            category_label=cat_map.get(liability.category_id) if liability.category_id else None,
        )
        for liability, outstanding in rows
    ]
    return success({
        "items": items, "total": len(items), "page": 1, "page_size": len(items) or 1,
        "has_next": False, "summary": summary,
        "module_notes": {"credit_cards": "信用卡为负债内部页，可在「信用卡」页面单独管理额度与账单"},
        "ai": AI_LIABILITY_CAPABILITIES,
    })


@router.post("/liabilities", summary="新增负债（房贷/车贷/消费贷/经营贷/亲友借款/信用卡）")
async def create_liability(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                           payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import liabilities as liability_service

    liability = await liability_service.create_liability(db, user, payload, ctx)
    await db.commit()
    outstanding = (await liability_service.outstanding_for(
        db, user.household_id or "", [liability.id])).get(liability.id)
    extension = await liability_service.liability_extension(db, liability)
    card = await liability_service.card_info(db, liability, outstanding or 0)
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    return success(
        liability_service.serialize_liability(
            liability, outstanding, extension=extension, card=card,
            category_label=cat_map.get(liability.category_id) if liability.category_id else None,
        ),
        message="负债已创建",
    )


@router.get("/liabilities/{liability_id}", summary="负债详情（概览/还款计划/还款记录/利息费用）")
async def get_liability(db: DbSession, user: CurrentUserDep, liability_id: str) -> dict:
    from app.services import liabilities as liability_service

    liability = await liability_service.get_liability(db, user, liability_id)
    outstanding = (await liability_service.outstanding_for(
        db, user.household_id or "", [liability.id])).get(liability.id)
    repayment_rows = await liability_service.list_repayments(db, user, liability_id)
    plan = await liability_service.repayment_plan(db, user, liability_id)

    # 利息费用：还款记录中的利息与费用部分 + 关联的支出事件
    interest_total = sum((r.interest + r.fee) for r in repayment_rows)
    # 只统计已确认事件；有还款单关联时只看这些还款产生的利息，避免把信用卡消费混进来
    interest_ids = [r.interest_event_id for r in repayment_rows if r.interest_event_id]
    interest_conditions = [
        BusinessEvent.liability_id == liability_id,
        BusinessEvent.event_type == "expense",
        BusinessEvent.status == "confirmed",
        BusinessEvent.deleted_at.is_(None),
    ]
    if interest_ids:
        interest_conditions.append(BusinessEvent.id.in_(interest_ids))
    interest_events = list((await db.execute(
        select(BusinessEvent).where(*interest_conditions)
        .order_by(BusinessEvent.occurred_at.desc()).limit(50)
    )).scalars().all())

    category_names = await catalog_service.category_label_map(db, user.household_id)

    linked_asset = None
    if liability.linked_asset_id:
        from app.db.models.business import Asset as _Asset

        asset = (await db.execute(
            select(_Asset).where(_Asset.id == liability.linked_asset_id)
        )).scalars().first()
        if asset is not None:
            linked_asset = {
                "id": asset.id, "name": asset.name, "asset_type": asset.asset_type,
                "asset_type_label": "不动产" if asset.asset_type == "real_estate" else (
                    "车辆" if asset.asset_type == "vehicle" else asset.asset_type),
                "current_value": fmt_amount(asset.current_value or 0),
                "currency": asset.currency,
            }

    data = liability_service.serialize_liability(
        liability, outstanding,
        category_label=category_names.get(liability.category_id) if liability.category_id else None,
        extra={
            "repayments": [liability_service.serialize_repayment(r) for r in repayment_rows],
            "repayment_plan": plan["schedule"],
            "repayment_plan_meta": {
                "method_label": plan["method_label"],
                "frequency_label": plan["frequency_label"],
                "periods": plan["periods"],
                "total_periods": plan["total_periods"],
                "paid_periods": plan["paid_periods"],
                "per_period": plan["per_period"],
                "caliber": plan["caliber"],
            },
            "interest_summary": {
                "total_interest": fmt_amount(interest_total),
                "caliber": "利息与费用计入支出；还款本金不计入支出",
            },
            "interest_events": [
                {
                    "id": e.id, "event_no": e.event_no, "title": e.title,
                    "category_id": e.category_id,
                    "category_label": category_names.get(e.category_id or "", "未分类"),
                    "amount": fmt_amount(e.amount or 0), "currency": e.currency,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in interest_events
            ],
            "linked_asset": linked_asset,
            "ai": AI_LIABILITY_CAPABILITIES,
        },
    )
    return success(data)


@router.put("/liabilities/{liability_id}", summary="编辑负债")
async def update_liability(db: DbSession, ctx: Ctx, user: CurrentUserDep, liability_id: str,
                           payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import liabilities as liability_service

    liability = await liability_service.update_liability(
        db, user, liability_id, payload, payload.get("version"), ctx
    )
    await db.commit()
    outstanding = (await liability_service.outstanding_for(
        db, user.household_id or "", [liability.id])).get(liability.id)
    extension = await liability_service.liability_extension(db, liability)
    card = await liability_service.card_info(db, liability, outstanding or 0)
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    return success(
        liability_service.serialize_liability(
            liability, outstanding, extension=extension, card=card,
            category_label=cat_map.get(liability.category_id) if liability.category_id else None,
        ),
        message="负债已更新",
    )


@router.delete("/liabilities/{liability_id}", summary="删除负债（需二次确认，软删除）")
async def delete_liability(db: DbSession, ctx: Ctx, user: CurrentUserDep, liability_id: str,
                           reason: str = Query(""), confirm_token: str = Query(None)) -> dict:
    require_action(user, "delete")
    from app.services import confirm as confirm_service
    from app.services import liabilities as liability_service

    confirm_service.consume_token(confirm_token, user, "liability.delete", "liability", liability_id)
    liability = await liability_service.delete_liability(db, user, liability_id, reason, ctx)
    await db.commit()
    return success({"id": liability.id}, message="负债已删除，历史保留")


@router.get("/liabilities/{liability_id}/prepay-simulation",
            summary="提前还款测算（缩短年限 / 减少月供两方案省息对比）")
async def prepay_simulation(
    db: DbSession, user: CurrentUserDep, liability_id: str,
    amount: str = Query(..., description="计划一次性提前还款的金额"),
) -> dict:
    from decimal import Decimal, InvalidOperation

    from app.services import liabilities as liability_service

    try:
        value = Decimal(str(amount).replace(",", ""))
    except (InvalidOperation, ValueError):
        raise AppError(ErrorCode.VALIDATION_ERROR, "金额格式不正确",
                       field_errors={"amount": "请填写正确的金额"})
    data = await liability_service.prepay_simulation(db, user, liability_id, value)
    return success(data, message="测算完成")


@router.post("/liabilities/{liability_id}/repayments", summary="登记还款（本金/利息/费用拆分）")
async def add_repayment(db: DbSession, ctx: Ctx, user: CurrentUserDep, liability_id: str,
                        payload: dict = Body(...),
                        idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict:
    require_action(user, "confirm")
    from app.services import idempotency as idem_service
    from app.services import liabilities as liability_service

    key = idem_service.key_from(payload, idempotency_key)
    record, replayed = await idem_service.guard(
        db, user.household_id, f"repayment.create", key, payload)
    if replayed:
        if not (record and record.object_id):
            raise idem_service.ReplayInProgress()
        existing = await liability_service.get_repayment(db, user, record.object_id)
        return success(liability_service.serialize_repayment(existing),
                       message="该还款已处理过，已返回原记录（未重复登记）")

    repayment = await liability_service.add_repayment(db, user, liability_id, payload, ctx)
    idem_service.complete_idempotent(record, repayment.id)
    await db.commit()
    return success(
        liability_service.serialize_repayment(repayment),
        message="还款已登记：本金不重复计入支出，利息与费用计入支出",
    )


@router.get("/liabilities/{liability_id}/repayments", summary="还款记录")
async def list_repayments(db: DbSession, user: CurrentUserDep, liability_id: str) -> dict:
    from app.services import liabilities as liability_service

    rows = await liability_service.list_repayments(db, user, liability_id)
    items = [liability_service.serialize_repayment(r) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.get("/liabilities/{liability_id}/repayment-plan", summary="还款计划测算")
async def repayment_plan(db: DbSession, user: CurrentUserDep, liability_id: str) -> dict:
    from app.services import liabilities as liability_service

    return success(await liability_service.repayment_plan(db, user, liability_id))


@router.post("/liabilities/{liability_id}/draws", summary="借款到账（不算收入）")
async def add_draw(db: DbSession, ctx: Ctx, user: CurrentUserDep, liability_id: str,
                   payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import events as event_service
    from app.services import liabilities as liability_service

    event = await liability_service.add_draw(db, user, liability_id, payload, ctx)
    await db.commit()
    movements = await event_service.movements_for(db, event.id)
    return success(event_service.serialize_event(event, movements),
                   message="借款已到账：账户增加、负债本金增加，不计入收入")


# ---------------- 信用卡（06 §7，负债内部页） ----------------

@router.get("/credit-cards", summary="信用卡列表（额度/已用/可用/本期账单）")
async def list_credit_cards(db: DbSession, user: CurrentUserDep,
                            keyword: str | None = Query(None),
                            reveal: bool = Query(False)) -> dict:
    from app.services import liabilities as liability_service

    items = await liability_service.list_cards(
        db, user, reveal=_mask_reveal(user, reveal), keyword=keyword
    )
    # 不同币种的额度不能相加：按币种分组后用 Decimal 累计，顶层只报基础币种
    totals: dict[str, dict[str, Decimal]] = {}
    for item in items:
        card = item.get("card", {})
        ccy = card.get("currency") or "CNY"
        bucket = totals.setdefault(ccy, {"limit": Decimal("0"), "used": Decimal("0")})
        bucket["limit"] += Decimal(str(card.get("credit_limit") or 0))
        bucket["used"] += Decimal(str(card.get("used_amount") or 0))
    base = await _household_base_currency(db)
    base_bucket = totals.get(base, {"limit": Decimal("0"), "used": Decimal("0")})
    return success({
        "items": items, "total": len(items), "page": 1, "page_size": len(items) or 1,
        "has_next": False,
        "summary": {
            "count": len(items),
            "currency": base,
            "total_limit": fmt_amount(base_bucket["limit"]),
            "total_used": fmt_amount(base_bucket["used"]),
            "total_available": fmt_amount(base_bucket["limit"] - base_bucket["used"]),
            "mixed_currency": len(totals) > 1,
            "by_currency": [
                {
                    "currency": ccy,
                    "limit": fmt_amount(b["limit"]),
                    "used": fmt_amount(b["used"]),
                    "available": fmt_amount(b["limit"] - b["used"]),
                }
                for ccy, b in totals.items()
            ],
            "caliber": liability_service.CALIBER["credit_card"],
        },
        "ai": AI_LIABILITY_CAPABILITIES,
    })


@router.get("/credit-cards/{card_id}", summary="信用卡详情（账单/消费明细/还款/费用）")
async def get_credit_card(db: DbSession, user: CurrentUserDep, card_id: str,
                          reveal: bool = Query(False)) -> dict:
    from app.services import liabilities as liability_service

    card = await liability_service.get_card(db, user, card_id)
    liability = await liability_service.get_liability(db, user, card.liability_id)
    outstanding = (await liability_service.outstanding_for(
        db, user.household_id or "", [liability.id])).get(liability.id)
    info = await liability_service.card_info(db, liability, outstanding,
                                             reveal=_mask_reveal(user, reveal))
    activity = await liability_service.card_activity(db, user, card_id)
    repayments = await liability_service.list_repayments(db, user, liability.id)
    data = liability_service.serialize_liability(
        liability, outstanding, card=info or {},
        extra={
            "activity": [liability_service.serialize_card_event(e, m) for e, m in activity],
            "repayments": [liability_service.serialize_repayment(r) for r in repayments],
            "ai": AI_LIABILITY_CAPABILITIES,
        },
    )
    return success(data)


@router.post("/credit-cards/{card_id}/bills", summary="生成信用卡账单")
async def create_credit_card_bill(db: DbSession, ctx: Ctx, user: CurrentUserDep, card_id: str,
                                  payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import liabilities as liability_service

    bill = await liability_service.create_bill(db, user, card_id, payload, ctx)
    await db.commit()
    return success(liability_service.serialize_bill(bill), message="账单已生成")


@router.post("/credit-cards/{card_id}/expenses", summary="记一笔信用卡消费")
async def add_credit_card_expense(db: DbSession, ctx: Ctx, user: CurrentUserDep, card_id: str,
                                  payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import events as event_service
    from app.services import liabilities as liability_service

    event = await liability_service.add_card_expense(db, user, card_id, payload, ctx)
    await db.commit()
    movements = await event_service.movements_for(db, event.id)
    return success(event_service.serialize_event(event, movements),
                   message="消费已记录：计入支出，同时增加信用卡已用额度")


# ---------------- 投资（M3，06 §8） ----------------

@router.get("/investments", summary="投资账户列表（余额/市值/成本/损益）")
async def list_investments(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    account_type: str | None = Query(None),
    category_id: str | None = Query(None),
    member_id: str | None = Query(None),
) -> dict:
    from app.services import investments as investment_service

    rows = await investment_service.list_accounts(
        db, user, keyword=keyword, account_type=account_type,
        category_id=category_id, member_id=member_id,
    )
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    items = [
        investment_service.serialize_account(
            acc, portfolio,
            category_label=cat_map.get(acc.category_id) if acc.category_id else None,
        )
        for acc, portfolio in rows
    ]
    summary = await investment_service.portfolio_summary(
        db, user.household_id or "", await _household_base_currency(db)
    )
    return success({
        "items": items, "total": len(items), "page": 1, "page_size": len(items) or 1,
        "has_next": False, "summary": summary, "ai": AI_INVESTMENT_CAPABILITIES,
    })


@router.post("/investments", summary="新增投资账户")
async def create_investment(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                            payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import investments as investment_service

    account = await investment_service.create_account(db, user, payload, ctx)
    await db.commit()
    portfolio = await investment_service.portfolio_of(db, account)
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    return success(
        investment_service.serialize_account(
            account, portfolio,
            category_label=cat_map.get(account.category_id) if account.category_id else None,
        ),
        message="投资账户已创建",
    )


@router.get("/investments/{account_id}", summary="投资账户详情（持仓/交易/估值/收益）")
async def get_investment(db: DbSession, user: CurrentUserDep, account_id: str) -> dict:
    from app.services import investments as investment_service

    account = await investment_service.get_account(db, user, account_id)
    portfolio = await investment_service.portfolio_of(db, account)
    trades = await investment_service.list_trades(db, user, account_id)
    valuations = await investment_service.list_valuations(db, user, account_id, limit=50)
    data = investment_service.serialize_account(
        account, portfolio,
        category_label=(await catalog_service.category_label_map(db, user.household_id)).get(account.category_id)
        if account.category_id else None,
        extra={
            "trades": [investment_service.serialize_trade(t) for t in trades],
            "valuations": [investment_service.serialize_valuation(v) for v in valuations],
            "ai": AI_INVESTMENT_CAPABILITIES,
        },
    )
    return success(data)


@router.put("/investments/{account_id}", summary="编辑投资账户")
async def update_investment(db: DbSession, ctx: Ctx, user: CurrentUserDep, account_id: str,
                            payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import investments as investment_service

    account = await investment_service.update_account(
        db, user, account_id, payload, payload.get("version"), ctx
    )
    await db.commit()
    portfolio = await investment_service.portfolio_of(db, account)
    cat_map = await catalog_service.category_label_map(db, user.household_id)
    return success(
        investment_service.serialize_account(
            account, portfolio,
            category_label=cat_map.get(account.category_id) if account.category_id else None,
        ),
        message="投资账户已更新",
    )


@router.delete("/investments/{account_id}", summary="删除投资账户（需二次确认，软删除）")
async def delete_investment(db: DbSession, ctx: Ctx, user: CurrentUserDep, account_id: str,
                            reason: str = Query(""), confirm_token: str = Query(None)) -> dict:
    require_action(user, "delete")
    from app.services import confirm as confirm_service
    from app.services import investments as investment_service

    confirm_service.consume_token(confirm_token, user, "investment.delete", "investment_account", account_id)
    account = await investment_service.delete_account(db, user, account_id, reason, ctx)
    await db.commit()
    return success({"id": account.id}, message="投资账户已删除，历史保留")


@router.post("/investments/{account_id}/trades", summary="登记投资交易（买入/卖出/分红/费用）")
async def add_investment_trade(db: DbSession, ctx: Ctx, user: CurrentUserDep, account_id: str,
                               payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import investments as investment_service

    trade, _event = await investment_service.add_trade(db, user, account_id, payload, ctx)
    await db.commit()
    return success(investment_service.serialize_trade(trade), message="交易已登记")


@router.get("/investments/{account_id}/trades", summary="交易列表")
async def list_investment_trades(db: DbSession, user: CurrentUserDep, account_id: str) -> dict:
    from app.services import investments as investment_service

    rows = await investment_service.list_trades(db, user, account_id)
    items = [investment_service.serialize_trade(t) for t in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.get("/investments/{account_id}/positions", summary="持仓（含下钻明细）")
async def list_investment_positions(db: DbSession, user: CurrentUserDep, account_id: str) -> dict:
    from app.services import investments as investment_service

    account = await investment_service.get_account(db, user, account_id)
    portfolio = await investment_service.portfolio_of(db, account)
    items = portfolio["positions"]
    return success({
        "items": items, "total": len(items), "page": 1, "page_size": len(items) or 1,
        "has_next": False,
        "summary": {
            "market_value": investment_service.money(portfolio["market_value"]),
            "cost": investment_service.money(portfolio["cost"]),
            "unrealized_pnl": investment_service.money(portfolio["unrealized_pnl"]),
            "realized_pnl": investment_service.money(portfolio["realized_pnl"]),
            "all_priced": portfolio["all_priced"],
            "caliber": investment_service.CALIBER,
        },
    })


@router.get("/investments/{account_id}/quotes", summary="按行情预演持仓收益率（只读，不写库）")
async def investment_quote_preview(db: DbSession, user: CurrentUserDep, account_id: str) -> dict:
    from app.services import investments as investment_service

    return success(await investment_service.quote_preview(db, user, account_id))


@router.post("/investments/{account_id}/quotes/apply", summary="把行情价格写成估值（需确认）")
async def investment_quote_apply(db: DbSession, ctx: Ctx, user: CurrentUserDep, account_id: str,
                                 payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import investments as investment_service

    data = await investment_service.apply_quotes(db, user, account_id, payload or {}, ctx)
    await db.commit()
    return success(data, message=f"已按行情更新 {data['updated']} 个持仓的估值")


@router.post("/investments/{account_id}/valuations", summary="录入投资估值")
async def add_investment_valuation(db: DbSession, ctx: Ctx, user: CurrentUserDep, account_id: str,
                                   payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import investments as investment_service

    valuation = await investment_service.add_valuation(db, user, account_id, payload, ctx)
    await db.commit()
    return success(investment_service.serialize_valuation(valuation), message="估值已录入")


@router.get("/investments/{account_id}/valuations", summary="投资估值历史")
async def list_investment_valuations(db: DbSession, user: CurrentUserDep, account_id: str,
                                     symbol: str | None = Query(None)) -> dict:
    from app.services import investments as investment_service

    rows = await investment_service.list_valuations(db, user, account_id, symbol=symbol)
    items = [investment_service.serialize_valuation(v) for v in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


# ---------------- 房产租赁（M4） ----------------

@router.get("/leases", summary="房产租赁列表（房产 + 租约）")
async def list_leases(
    db: DbSession, user: CurrentUserDep,
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    real_estate_id: str | None = Query(None),
    category_id: str | None = Query(None),
    view: str = Query("leases", description="leases / properties"),
) -> dict:
    from app.services import leases as lease_service

    if view == "properties":
        items = await lease_service.list_properties(db, user, keyword=keyword, status=status)
        return list_payload(items, len(items), 1, len(items) or 1)
    rows = await lease_service.list_leases(
        db, user, keyword=keyword, status=status, real_estate_id=real_estate_id,
        category_id=category_id)
    items = await lease_service._leases_payload(db, rows)
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/leases", summary="新建租约")
async def create_lease(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                       payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import leases as lease_service

    lease = await lease_service.create_lease(db, user, payload, ctx)
    await db.commit()
    return success(await lease_service.lease_payload(db, lease), message="租约已创建")


@router.get("/leases/contract-template/preview", summary="预览空白房屋租赁合同模板")
async def lease_contract_template_preview(user: CurrentUserDep) -> dict:
    """先看再下：预览的是即将下载的同一份 docx 内容。"""
    from app.services import lease_contract as contract_service

    paras = contract_service.extract_paragraphs(contract_service.build_lease_contract())
    return success({
        "title": "房屋租赁合同（空白模板）",
        "paragraphs": paras,
        "filename": "房屋租赁合同-空白模板.docx",
    })


@router.get("/leases/contract-template", summary="下载空白房屋租赁合同模板")
async def lease_contract_template(user: CurrentUserDep) -> Response:
    """空白模板：打印后手写，或先下载再按实际情况填写后打印签署。"""
    from urllib.parse import quote

    from app.services import lease_contract as contract_service

    content = contract_service.build_lease_contract()
    return _docx_response(content, "房屋租赁合同-空白模板.docx")


@router.get("/leases/{lease_id}/contract/preview", summary="预览租约的房屋租赁合同（先看后下）")
async def lease_contract_preview(db: DbSession, user: CurrentUserDep, lease_id: str) -> dict:
    from app.services import asset_extensions as ext_service
    from app.services import lease_contract as contract_service
    from app.services import leases as lease_service

    lease = await lease_service.get_lease(db, user, lease_id)
    asset = (await db.execute(
        select(Asset).where(Asset.id == lease.real_estate_id)
    )).scalars().first()
    extension = await ext_service.extension_for(db, asset) if asset is not None else None
    content = contract_service.build_lease_contract(
        lease=lease, asset=asset, extension=extension or {},
    )
    return success({
        "title": f"房屋租赁合同（{lease.tenant_name}）",
        "paragraphs": contract_service.extract_paragraphs(content),
        "filename": contract_service.contract_filename(lease),
    })


@router.get("/leases/{lease_id}/contract", summary="下载租约的房屋租赁合同（已预填租约信息）")
async def lease_contract(db: DbSession, user: CurrentUserDep, lease_id: str) -> Response:
    from app.services import asset_extensions as ext_service
    from app.services import lease_contract as contract_service
    from app.services import leases as lease_service

    lease = await lease_service.get_lease(db, user, lease_id)
    asset = (await db.execute(
        select(Asset).where(Asset.id == lease.real_estate_id)
    )).scalars().first()
    extension = await ext_service.extension_for(db, asset) if asset is not None else None

    content = contract_service.build_lease_contract(
        lease=lease, asset=asset, extension=extension or {},
    )
    return _docx_response(content, contract_service.contract_filename(lease))


def _docx_response(content: bytes, filename: str) -> Response:
    from urllib.parse import quote

    ascii_name = "lease-contract.docx"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={"Content-Disposition": disposition},
    )


@router.get("/leases/{lease_id}", summary="租约详情")
async def lease_detail(db: DbSession, user: CurrentUserDep, lease_id: str,
                       reveal: bool = Query(False)) -> dict:
    from app.services import leases as lease_service

    # reveal=true 才返回明文联系方式；是否放行由权限决定
    return success(await lease_service.lease_detail(
        db, user, lease_id, reveal=_mask_reveal(user, reveal),
    ))


@router.put("/leases/{lease_id}", summary="编辑租约")
async def update_lease(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                       payload: dict = Body(...)) -> dict:
    require_action(user, "edit_draft")
    from app.services import leases as lease_service

    lease = await lease_service.update_lease(db, user, lease_id, payload, ctx)
    await db.commit()
    return success(await lease_service.lease_payload(db, lease), message="租约已更新")


@router.post("/leases/{lease_id}/renew", summary="续租（生成新租约，旧租约转已到期）")
async def renew_lease(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                      payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import leases as lease_service

    lease = await lease_service.renew_lease(db, user, lease_id, payload, ctx)
    await db.commit()
    return success(await lease_service.lease_payload(db, lease), message="已生成续租合同")


@router.post("/leases/{lease_id}/settle", summary="退租结算（水电煤 + 破损赔偿，退还押金）")
async def settle_lease(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                       payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import leases as lease_service

    data = await lease_service.settle_lease(db, user, lease_id, payload, ctx)
    await db.commit()
    return success(data, message="退租结算完成")


@router.get("/leases/{lease_id}/settlements", summary="退租结算记录")
async def list_settlements(db: DbSession, user: CurrentUserDep, lease_id: str) -> dict:
    from app.services import leases as lease_service

    lease = await lease_service.get_lease(db, user, lease_id)
    rows = await lease_service.list_settlements(db, lease.id)
    items = [lease_service.serialize_settlement(s, ps) for s, ps in rows]
    return list_payload(items, len(items), 1, len(items))


@router.post("/leases/{lease_id}/terminate", summary="终止租约（二次确认）")
async def terminate_lease(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                          payload: dict = Body(...)) -> dict:
    require_action(user, "archive")
    from app.services import leases as lease_service

    lease = await lease_service.terminate_lease(db, user, lease_id, payload, ctx)
    await db.commit()
    return success(await lease_service.lease_payload(db, lease), message="租约已终止")


@router.get("/leases/{lease_id}/receivables", summary="租金应收列表")
async def list_receivables(db: DbSession, user: CurrentUserDep, lease_id: str) -> dict:
    from sqlalchemy import select as _select

    from app.db.models.leases import LeaseReceivable
    from app.services import leases as lease_service

    await lease_service._get_lease(db, user, lease_id)
    rows = list((await db.execute(
        _select(LeaseReceivable)
        .where(LeaseReceivable.lease_id == lease_id, LeaseReceivable.deleted_at.is_(None))
        .order_by(LeaseReceivable.due_date.asc())
    )).scalars().all())
    items = [lease_service.serialize_receivable(r) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/leases/{lease_id}/receivables", summary="登记租金应收")
async def create_receivable(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                            payload: dict = Body(...)) -> dict:
    require_action(user, "create_draft")
    from app.services import leases as lease_service

    receivable = await lease_service.create_receivable(db, user, lease_id, payload, ctx)
    await db.commit()
    return success(lease_service.serialize_receivable(receivable), message="租金应收已登记（不改变账户余额）")


@router.get("/leases/{lease_id}/receipt-preview", summary="实收前的金额与下次收租日计算")
async def receipt_preview(db: DbSession, user: CurrentUserDep, lease_id: str,
                          occurred_at: str | None = Query(None)) -> dict:
    from app.services import leases as lease_service

    return success(await lease_service.receipt_preview(db, user, lease_id, occurred_at))


@router.post("/leases/{lease_id}/receipts", summary="登记租金实收")
async def create_receipt(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                         payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import leases as lease_service

    event = await lease_service.create_receipt(db, user, lease_id, payload, ctx)
    await db.commit()
    return success({"event_id": event.id, "event_no": event.event_no},
                   message="租金实收已确认并计入收入")


@router.get("/leases/{lease_id}/deposits", summary="押金列表")
async def list_deposits(db: DbSession, user: CurrentUserDep, lease_id: str) -> dict:
    from sqlalchemy import select as _select

    from app.db.models.leases import LeaseDeposit
    from app.services import leases as lease_service

    await lease_service._get_lease(db, user, lease_id)
    rows = list((await db.execute(
        _select(LeaseDeposit)
        .where(LeaseDeposit.lease_id == lease_id, LeaseDeposit.deleted_at.is_(None))
        .order_by(LeaseDeposit.created_at.asc())
    )).scalars().all())
    items = [lease_service.serialize_deposit(d) for d in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/leases/{lease_id}/deposits/receive", summary="收取押金")
async def receive_deposit(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                          payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import leases as lease_service

    deposit = await lease_service.receive_deposit(db, user, lease_id, payload, ctx)
    await db.commit()
    return success(lease_service.serialize_deposit(deposit), message="押金已收取（不计入收入）")


@router.post("/leases/{lease_id}/deposits/{deposit_id}/return", summary="退还押金")
async def return_deposit(db: DbSession, ctx: Ctx, user: CurrentUserDep, lease_id: str,
                         deposit_id: str, payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import leases as lease_service

    deposit = await lease_service.return_deposit(db, user, lease_id, deposit_id, payload, ctx)
    await db.commit()
    return success(lease_service.serialize_deposit(deposit), message="押金已退还（不计入支出）")


# ---------------- 账户对账（M4） ----------------

@router.get("/reconciliation", summary="账户对账预览")
async def reconciliation_preview(
    db: DbSession, user: CurrentUserDep,
    account_id: str = Query(...),
    as_of: str | None = Query(None),
) -> dict:
    from app.services import reconciliation as recon_service

    return success(await recon_service.preview(db, user, account_id, as_of))


@router.post("/reconciliation", summary="提交对账（生成调整事件）")
async def reconciliation_submit(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                                payload: dict = Body(...)) -> dict:
    require_action(user, "confirm")
    from app.services import reconciliation as recon_service

    result = await recon_service.reconcile(db, user, payload.get("account_id"), payload, ctx)
    await db.commit()
    return success(result, message="对账已完成" + ("，已生成调整事件" if result["adjusted"] else ""))


# ---------------- 智能录入 / OCR（M5） ----------------

@router.post("/capture/attachments", summary="上传材料附件")
async def upload_attachment(
    db: DbSession, ctx: Ctx, user: CurrentUserDep,
    file: UploadFile = File(...),
    material_type: str | None = Form(None),
    object_type: str | None = Form(None),
    object_id: str | None = Form(None),
    note: str | None = Form(None),
) -> dict:
    from app.services import attachments as attachment_service

    require_action(user, "create_draft")
    row = await attachment_service.save_upload(
        db, user, file, material_type=material_type, object_type=object_type,
        object_id=object_id, note=note, ctx=ctx)
    await db.commit()
    return success(attachment_service.serialize_attachment(row), message="附件已上传")


@router.get("/metal-prices", summary="贵金属行情（今日金价 + 回收价，估值用）")
async def metal_prices(refresh: bool = Query(False, description="强制刷新，忽略缓存")) -> dict:
    from app.services import metal_price as price_service

    return success(await price_service.fetch_prices(force=refresh))


@router.get("/metal-brands", summary="贵金属品牌与购买渠道字典")
async def metal_brands() -> dict:
    from app.services import metal_brands as brand_service

    return success({
        "brands": brand_service.BRANDS,
        "brand_options": brand_service.brand_options(),
        "pricing_modes": brand_service.PRICING_MODES,
        "purities": brand_service.PURITIES,
    })


@router.get("/capture/attachments", summary="材料附件列表")
async def list_attachments(
    db: DbSession, user: CurrentUserDep,
    object_type: str | None = Query(None),
    object_id: str | None = Query(None),
    keyword: str | None = Query(None),
    only_pending: bool = Query(False, description="只返回尚未被确认引用的附件（智能录入待处理）"),
) -> dict:
    from app.services import attachments as attachment_service

    rows = await attachment_service.list_attachments(
        db, user, object_type=object_type, object_id=object_id, keyword=keyword)
    items = await attachment_service.attach_reference_info(
        db, user.household_id, [attachment_service.serialize_attachment(a) for a in rows])
    if only_pending:
        items = [i for i in items if not (i["referenced"] and i["reference_status"] == "confirmed")]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.get("/capture/attachments/{attachment_id}/download", summary="下载附件")
async def download_attachment(db: DbSession, user: CurrentUserDep, attachment_id: str):
    from fastapi.responses import FileResponse

    from app.services import attachments as attachment_service

    row = await attachment_service.resolve_download(db, user, attachment_id)
    await db.commit()
    return FileResponse(row.stored_path, filename=row.file_name, media_type=row.mime_type or "application/octet-stream")


@router.delete("/capture/attachments/{attachment_id}", summary="删除附件（二次确认）")
async def delete_attachment(db: DbSession, ctx: Ctx, user: CurrentUserDep, attachment_id: str,
                            payload: dict = Body(...)) -> dict:
    require_action(user, "delete")
    from app.services import attachments as attachment_service

    row = await attachment_service.delete_attachment(db, user, attachment_id, payload, ctx)
    await db.commit()
    return success(attachment_service.serialize_attachment(row), message="附件已删除（历史保留）")


@router.post("/capture/ocr", summary="OCR 识别材料")
async def ocr_recognize(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                        payload: dict = Body(...)) -> dict:
    from app.services import ocr as ocr_service

    require_action(user, "create_draft")
    try:
        log = await ocr_service.recognize(db, user, payload.get("attachment_id"),
                                          material_type=payload.get("material_type"), ctx=ctx)
        await db.commit()
        return success(ocr_service.serialize_log(log), message="OCR 识别完成，请审核结构化结果")
    except AppError as exc:
        # 未配置（可操作配置入口）直接上抛；识别失败记录可重试日志后上抛
        if exc.code != ErrorCode.PROVIDER_NOT_CONFIGURED:
            await ocr_service.record_failure(
                db, user, payload.get("attachment_id"), exc.message,
                material_type=payload.get("material_type"))
            await db.commit()
        raise


@router.get("/capture/ocr-logs", summary="OCR 调用记录")
async def ocr_logs(db: DbSession, user: CurrentUserDep,
                   attachment_id: str | None = Query(None),
                   status: str | None = Query(None),
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)) -> dict:
    from app.services import ocr as ocr_service

    rows, total = await ocr_service.list_logs(
        db, user, attachment_id=attachment_id, status=status, page=page, page_size=page_size)
    items = [ocr_service.serialize_log(l) for l in rows]
    return list_payload(items, total, page, page_size)


@router.post("/capture/ocr/{log_id}/retry", summary="重试 OCR 识别")
async def ocr_retry(db: DbSession, ctx: Ctx, user: CurrentUserDep, log_id: str) -> dict:
    from app.services import ocr as ocr_service

    require_action(user, "create_draft")
    log = await ocr_service.retry_log(db, user, log_id, ctx=ctx)
    await db.commit()
    return success(ocr_service.serialize_log(log), message="OCR 重试成功")


# ---------------- 提醒中心（M8，08 §5/§12） ----------------

@router.get("/reminders", summary="提醒列表（实时计算）")
async def reminders(db: DbSession, user: CurrentUserDep) -> dict:
    from app.services import reminders as reminder_service
    data = await reminder_service.list_reminders(db, user)
    return success(data, message="统计完成")


@router.post("/reminders/send", summary="手动发送提醒通知（同步）")
async def send_reminders(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                         payload: dict = Body(default={})) -> dict:
    from app.services import audit as audit_service
    from app.services import reminders as reminder_service
    keys = payload.get("keys") or None
    result = await reminder_service.send_reminders(db, user, keys)
    await audit_service.record_audit(
        db, action="reminder.send", action_label="发送提醒通知",
        object_type="reminder", after=result, result="success" if result["failed"] == 0 else "failure",
        external_provider="pushplus", user=user, ctx=ctx,
    )
    await db.commit()
    return success(result, message=f"发送 {result['sent']} 条成功，{result['failed']} 条失败")


@router.get("/notifications/records", summary="通知发送记录")
async def notification_records(db: DbSession, user: CurrentUserDep,
                               page: int = Query(1, ge=1),
                               page_size: int = Query(20, ge=1, le=100)) -> dict:
    from app.services import reminders as reminder_service
    items, total = await reminder_service.list_records(db, user, page, page_size)
    return list_payload(items, total, page, page_size)


@router.post("/notifications/records/{record_id}/retry", summary="重试失败通知")
async def retry_notification(db: DbSession, ctx: Ctx, user: CurrentUserDep, record_id: str) -> dict:
    from app.services import audit as audit_service
    from app.services import reminders as reminder_service
    record = await reminder_service.retry_record(db, user, record_id)
    await audit_service.record_audit(
        db, action="notification.retry", action_label="重试通知",
        object_type="notification_record", object_id=record_id,
        after={"delivery_status": record["delivery_status"]}, result=record["delivery_status"],
        external_provider="pushplus", user=user, ctx=ctx,
    )
    await db.commit()
    return success(record, message=record["delivery_status_label"])



# ---------------- 数据导入（24 §7） ----------------

@router.post("/imports/preview", summary="导入预览（解析 CSV 并校验，不落库）")
async def preview_import(db: DbSession, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    from app.services import imports as import_service

    text = payload.get("content") or ""
    if not text.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "请提供 CSV 内容",
                       field_errors={"content": "CSV 内容不能为空"})
    result = await import_service.preview_import(db, user, text)
    return success(result, message="预览完成")


@router.post("/imports", summary="创建导入批次（生成草稿事件）")
async def create_import(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                        payload: dict = Body(...),
                        idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict:
    require_action(user, "create_draft")
    from app.services import idempotency as idem_service
    from app.services import imports as import_service

    file_name = payload.get("file_name") or "import.csv"
    text = payload.get("content") or ""
    if not text.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "请提供 CSV 内容",
                       field_errors={"content": "CSV 内容不能为空"})
    # 用户在预览页确认「与已有流水是同一笔」的行：跳过，不生成草稿事件
    raw_skip = payload.get("skip_rows") or []
    if not isinstance(raw_skip, list):
        raise AppError(ErrorCode.VALIDATION_ERROR, "skip_rows 必须是行号数组",
                       field_errors={"skip_rows": "请传入行号数组"})
    skip_rows = [int(n) for n in raw_skip if str(n).strip().isdigit()]

    key = idem_service.key_from(payload, idempotency_key)
    record, replayed = await idem_service.guard(
        db, user.household_id, "import.create", key, payload)
    if replayed:
        if not (record and record.object_id):
            raise idem_service.ReplayInProgress()
        existing = await import_service.get_batch(db, user, record.object_id)
        return success(import_service.serialize_batch(existing[0]),
                       message="该导入已处理过，已返回原批次（未重复创建）")

    batch = await import_service.create_import(
        db, user, file_name, text, ctx, skip_rows=skip_rows)
    idem_service.complete_idempotent(record, batch.id)
    await db.commit()
    return success(import_service.serialize_batch(batch),
                   message="导入批次已创建，成功行已生成草稿事件，确认后才入账")


@router.get("/imports", summary="导入批次列表")
async def list_imports(db: DbSession, user: CurrentUserDep) -> dict:
    from app.services import imports as import_service

    batches = await import_service.list_batches(db, user)
    items = [import_service.serialize_batch(b) for b in batches]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.get("/imports/{batch_id}", summary="导入批次详情（记录列表）")
async def get_import(db: DbSession, user: CurrentUserDep, batch_id: str) -> dict:
    from app.services import imports as import_service

    batch, records = await import_service.get_batch(db, user, batch_id)
    return success({
        "batch": import_service.serialize_batch(batch),
        "records": [import_service.serialize_record(r) for r in records],
    })


@router.post("/imports/{batch_id}/confirm", summary="批量确认导入草稿")
async def confirm_import(db: DbSession, ctx: Ctx, user: CurrentUserDep, batch_id: str) -> dict:
    require_action(user, "confirm")
    from app.services import imports as import_service

    batch, confirmed = await import_service.confirm_batch(db, user, batch_id, ctx)
    await db.commit()
    return success({"batch": import_service.serialize_batch(batch), "confirmed": confirmed},
                   message=f"已批量确认 {confirmed} 条导入草稿")


# ---------------- 智能解析：账单 / 聊天记录 → 待确认草稿建议 ----------------

@router.post("/parse-text", summary="智能解析：自然语言/聊天记录 → 待确认草稿建议")
async def parse_text(db: DbSession, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    from app.services import bill_parse

    text = (payload.get("text") or "").strip()
    if not text:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请输入要解析的文本",
                       field_errors={"text": "文本不能为空"})
    result = await bill_parse.parse_text_to_drafts(db, user, text)
    return success(result, message=f"解析完成，共 {result['total']} 条")
