"""账户领域服务（06 §3、18 §4 M1 核心账务）。

余额口径（重要，后端确定性计算，前端不得自行计算）：
    当前余额 = 期初余额 + Σ(已确认事件的资金变动净额)
草稿、已取消、已冲销事件不计入余额。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.ids import new_id, utc_iso
from app.core.money import fmt_amount
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.services import audit as audit_service

EDITABLE_FIELDS = (
    "name", "account_type", "category_id", "institution", "currency", "member_id",
    "card_last4", "holder_name",
    "credit_limit", "credit_used", "card_expiry", "bill_day", "due_day",
    "interest_rate", "term_months", "maturity_date",
    "opening_balance", "opening_date", "include_in_net_worth", "note",
)

# Numeric(20,4) 的安全上限：整数部分超过 16 位在 MySQL 严格模式下会直接报错（500）
MAX_AMOUNT = Decimal("1000000000000000")

# 文本列宽（与 db/models/business.py 保持一致），超长在生产 MySQL 上会 500
MAX_TEXT_LENGTH = {
    "name": (128, "账户名称"),
    "institution": (128, "开户机构"),
    "holder_name": (64, "开户人"),
}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def serialize_account(
    a: Account, balance: Decimal | None = None, *, category_label: str | None = None
) -> dict:
    bal = _dec(a.opening_balance) if balance is None else balance
    return {
        "id": a.id,
        "name": a.name,
        "account_type": a.account_type,
        "account_type_label": enum_zh.zh("account_type", a.account_type),
        "category_id": a.category_id,
        "category_label": category_label or "",
        "institution": a.institution,
        "card_last4": a.card_last4,
        "card_masked": f"尾号 {a.card_last4}" if a.card_last4 else "",
        "holder_name": a.holder_name,
        # 信用类账户（信用卡 / 消费信贷 / 借款额度）：
        # 「已用额度」由余额派生——余额为负表示欠款，为正表示溢缴款。
        # 不再单独维护手工 credit_used，避免额度与余额两套账。
        "credit_limit": fmt_amount(a.credit_limit) if a.credit_limit is not None else None,
        "credit_used": (
            fmt_amount(max(Decimal("0"), -bal)) if a.credit_limit is not None else None
        ),
        "available_credit": (
            fmt_amount(_dec(a.credit_limit) - max(Decimal("0"), -bal))
            if a.credit_limit is not None else None
        ),
        "credit_overpaid": (
            fmt_amount(bal) if a.credit_limit is not None and bal > 0 else None
        ),
        "is_credit": a.credit_limit is not None,
        "card_expiry": a.card_expiry,
        "bill_day": a.bill_day,
        "due_day": a.due_day,
        # 储蓄 / 借款类：年利率（%）、存期（月）、到期日（利率非金额，保留 4 位）
        "interest_rate": f"{_dec(a.interest_rate):.4f}" if a.interest_rate is not None else None,
        "term_months": a.term_months,
        "maturity_date": local_iso(a.maturity_date) if a.maturity_date else None,
        "currency": a.currency,
        "member_id": a.member_id,
        "opening_balance": fmt_amount(a.opening_balance),
        "opening_date": local_iso(a.opening_date) if a.opening_date else None,
        # 余额与可用余额均由确定性服务计算
        "balance": fmt_amount(bal),
        "available_balance": fmt_amount(bal),
        "include_in_net_worth": a.include_in_net_worth,
        "status": a.status,
        "status_label": enum_zh.zh("lifecycle_status", a.status),
        "last_reconciled_at": utc_iso(a.last_reconciled_at) if a.last_reconciled_at else None,
        "note": a.note,
        "version": a.version,
        "created_at": utc_iso(a.created_at),
        "updated_at": utc_iso(a.updated_at),
    }


async def household_accounts(db: AsyncSession, household_id: str) -> list[Account]:
    """按家庭读全部未删除账户（请求级缓存）。

    一次快照里账户汇总、流动性/偿债覆盖、提醒、余额计算各读一遍同一张表；
    同一请求内账户不会变，读一次共享即可。账户的增删改之后要
    `agg_cache.invalidate(db, "accounts")`。
    """
    from app.core.agg_cache import request_cached

    async def load() -> list[Account]:
        return list(
            (await db.execute(
                select(Account)
                .where(Account.household_id == household_id, Account.deleted_at.is_(None))
                .order_by(Account.created_at.asc())
            )).scalars().all()
        )

    return await request_cached(db, ("accounts", household_id), load)


async def balances_for(
    db: AsyncSession, household_id: str, account_ids: list[str] | None = None
) -> dict[str, Decimal]:
    """批量计算账户余额（带请求级缓存）。

    一次快照里余额会被账户汇总、流动性/偿债覆盖、提醒三处各算一次，
    每次都是一次全表 group by；同一请求内数据不变，缓存后只算一次。
    余额只在读路径使用（列表/详情/汇总），写操作后再要最新余额的路径
    请先调 `agg_cache.invalidate(db, "balances")`。
    """
    from app.core.agg_cache import request_cached

    ids_key = tuple(sorted(set(account_ids))) if account_ids else "*"
    return await request_cached(
        db, ("balances", household_id, ids_key), lambda: _balances_for(db, household_id, account_ids)
    )


async def _balances_for(
    db: AsyncSession, household_id: str, account_ids: list[str] | None = None
) -> dict[str, Decimal]:
    """批量计算账户余额：期初余额 + 已确认资金变动净额。

    只统计 target_type='account' 且所属事件为 confirmed 的变动；
    草稿/取消/冲销一律不计入。
    """
    stmt = (
        select(
            EventMovement.target_id,
            EventMovement.direction,
            func.coalesce(func.sum(EventMovement.amount), 0),
        )
        .join(BusinessEvent, BusinessEvent.id == EventMovement.event_id)
        .where(
            EventMovement.household_id == household_id,
            EventMovement.target_type == "account",
            EventMovement.target_id.is_not(None),
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
        )
        .group_by(EventMovement.target_id, EventMovement.direction)
    )
    if account_ids:
        stmt = stmt.where(EventMovement.target_id.in_(account_ids))

    rows = (await db.execute(stmt)).all()
    net: dict[str, Decimal] = {}
    for target_id, direction, total in rows:
        amount = _dec(total)
        net[target_id] = net.get(target_id, Decimal("0")) + (amount if direction == "in" else -amount)

    accounts = await household_accounts(db, household_id)
    result: dict[str, Decimal] = {}
    for acc in accounts:
        if account_ids and acc.id not in account_ids:
            continue
        result[acc.id] = _dec(acc.opening_balance) + net.get(acc.id, Decimal("0"))
    return result


async def balance_of(db: AsyncSession, account: Account) -> Decimal:
    balances = await balances_for(db, account.household_id, [account.id])
    return balances.get(account.id, _dec(account.opening_balance))


async def list_accounts(
    db: AsyncSession, user: CurrentUser, *, keyword: str | None = None,
    account_type: str | None = None, status: str | None = None,
    category_id: str | None = None,
) -> list[tuple[Account, Decimal]]:
    # 账户数量是家庭级的（几十个量级），读一次再在内存里筛，比按条件拼 SQL 更省
    rows = await household_accounts(db, user.household_id or "")
    if keyword:
        needle = keyword.lower()
        rows = [r for r in rows if needle in (r.name or "").lower()]
    if account_type:
        rows = [r for r in rows if r.account_type == account_type]
    if status:
        rows = [r for r in rows if r.status == status]
    if category_id:
        rows = [r for r in rows if r.category_id == category_id]
    balances = await balances_for(db, user.household_id or "", [r.id for r in rows])
    return [(r, balances.get(r.id, _dec(r.opening_balance))) for r in rows]


async def get_account(db: AsyncSession, user: CurrentUser, account_id: str) -> Account:
    account = (
        await db.execute(select(Account).where(Account.id == account_id))
    ).scalars().first()
    if account is None or account.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "账户不存在", status_code=404)
    if account.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return account


def _validate_payload(payload: dict, partial: bool = False) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}

    name = (payload.get("name") or "").strip() if "name" in payload else None
    if not partial or "name" in payload:
        if not name:
            field_errors["name"] = "请填写账户名称"
    if name is not None:
        if len(name) > MAX_TEXT_LENGTH["name"][0]:
            field_errors["name"] = f"账户名称最多 {MAX_TEXT_LENGTH['name'][0]} 个字符"
        else:
            data["name"] = name

    if "account_type" in payload:
        acc_type = payload.get("account_type") or "bank"
        if acc_type not in enum_zh.ACCOUNT_TYPE:
            field_errors["account_type"] = "账户类型不合法"
        else:
            data["account_type"] = acc_type

    if "currency" in payload:
        currency = (payload.get("currency") or "CNY").upper()
        if not (2 <= len(currency) <= 8):
            field_errors["currency"] = "币种代码不合法"
        else:
            data["currency"] = currency

    for money_field in ("opening_balance",):
        if money_field in payload and payload[money_field] not in (None, ""):
            try:
                value = Decimal(str(payload[money_field]))
                if not value.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors[money_field] = "金额格式不正确"
            else:
                if value > MAX_AMOUNT:
                    field_errors[money_field] = "金额超出允许范围"
                else:
                    data[money_field] = value

    if "opening_date" in payload and payload["opening_date"]:
        try:
            data["opening_date"] = to_local_naive(datetime.fromisoformat(
                str(payload["opening_date"]).replace("Z", "+00:00")
            ))
        except ValueError:
            field_errors["opening_date"] = "日期格式应为 ISO 8601"

    for plain in ("institution", "member_id", "note", "category_id", "holder_name"):
        if plain in payload:
            data[plain] = payload[plain]

    # 文本长度上限：模型列宽有限，超长在 MySQL 严格模式下会抛 DataError（前端表现为 500）
    for field, (limit, label) in MAX_TEXT_LENGTH.items():
        if field in data and data[field] is not None:
            if len(str(data[field])) > limit:
                field_errors[field] = f"{label}最多 {limit} 个字符"
                data.pop(field, None)

    # 信用类账户字段：额度 / 已用额度 / 卡片有效期 / 账单日 / 还款日（06 §7）
    for money_field in ("credit_limit", "credit_used"):
        if money_field not in payload:
            continue
        if payload[money_field] in (None, ""):
            data[money_field] = None
            continue
        try:
            value = Decimal(str(payload[money_field]))
            if not value.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors[money_field] = "金额格式不正确"
        else:
            if value < 0:
                field_errors[money_field] = "额度不能为负数"
            elif value > MAX_AMOUNT:
                field_errors[money_field] = "额度超出允许范围"
            else:
                data[money_field] = value

    if "card_expiry" in payload:
        raw = str(payload.get("card_expiry") or "").strip()
        if not raw:
            data["card_expiry"] = None
        elif re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
            data["card_expiry"] = raw
        else:
            field_errors["card_expiry"] = "有效期格式应为 YYYY-MM，例如 2029-05"

    for day_field in ("bill_day", "due_day"):
        if day_field not in payload:
            continue
        if payload[day_field] in (None, ""):
            data[day_field] = None
            continue
        try:
            day = int(payload[day_field])
        except (TypeError, ValueError):
            field_errors[day_field] = "请填写 1-28 的整数"
        else:
            if not 1 <= day <= 28:
                field_errors[day_field] = "账单日 / 还款日应在 1-28 之间"
            else:
                data[day_field] = day

    # 储蓄 / 借款类：年利率、存期、到期日（08 §3 账户分类细化）
    if "interest_rate" in payload:
        if payload["interest_rate"] in (None, ""):
            data["interest_rate"] = None
        else:
            try:
                rate = Decimal(str(payload["interest_rate"]))
                if not rate.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors["interest_rate"] = "利率格式不正确"
            else:
                if rate < 0 or rate > 100:
                    field_errors["interest_rate"] = "年利率应为 0-100 的百分数，例如 1.5"
                else:
                    data["interest_rate"] = rate

    if "term_months" in payload:
        if payload["term_months"] in (None, ""):
            data["term_months"] = None
        else:
            try:
                months = int(payload["term_months"])
            except (TypeError, ValueError):
                field_errors["term_months"] = "存期应填写整数月"
            else:
                if not 1 <= months <= 600:
                    field_errors["term_months"] = "存期应在 1-600 个月之间"
                else:
                    data["term_months"] = months

    if "maturity_date" in payload:
        if not payload["maturity_date"]:
            data["maturity_date"] = None
        else:
            try:
                data["maturity_date"] = datetime.fromisoformat(
                    str(payload["maturity_date"]).replace("Z", "+00:00")
                )
            except ValueError:
                field_errors["maturity_date"] = "到期日格式应为 YYYY-MM-DD"

    # 卡号只保留后四位：输入完整卡号时自动截取，不落库完整卡号
    if "card_last4" in payload:
        raw = re.sub(r"\D", "", str(payload.get("card_last4") or ""))
        if raw:
            if len(raw) < 4:
                field_errors["card_last4"] = "卡号至少需要 4 位数字"
            else:
                data["card_last4"] = raw[-4:]
        else:
            data["card_last4"] = None

    if "include_in_net_worth" in payload:
        data["include_in_net_worth"] = bool(payload["include_in_net_worth"])

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "账户信息校验失败", field_errors=field_errors)
    return data


async def _validate_category(db: AsyncSession, household_id: str, category_id: str | None) -> str | None:
    """账户分类必须属于「账户」分类域；为空表示未分类。"""
    if not category_id:
        return None
    from app.db.models.config import Category

    cat = (await db.execute(select(Category).where(
        Category.id == category_id,
        Category.household_id == household_id,
        Category.deleted_at.is_(None),
    ))).scalars().first()
    if cat is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "分类不存在",
                       field_errors={"category_id": "分类不存在"})
    if cat.domain != "account":
        raise AppError(ErrorCode.VALIDATION_ERROR, "分类域不匹配",
                       field_errors={"category_id": "请选择「账户」分类域下的分类"})
    return cat.id


async def _ensure_unique_name(
    db: AsyncSession, household_id: str, name: str, exclude_id: str | None = None,
) -> None:
    """同一家庭下账户名不能重复（数据库无唯一约束，因为软删除后要允许重建同名）。"""
    conditions = [
        Account.household_id == household_id,
        Account.name == name,
        Account.deleted_at.is_(None),
    ]
    if exclude_id:
        conditions.append(Account.id != exclude_id)
    exists = (await db.execute(select(Account.id).where(*conditions).limit(1))).scalars().first()
    if exists:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "同名账户已存在",
            field_errors={"name": "同一家庭下账户名称不能重复"},
        )


async def create_account(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None
) -> Account:
    data = _validate_payload(payload)
    await _ensure_unique_name(db, user.household_id or "", data["name"])

    # 信用类账户：表单里填的是「已用额度」，落库时转成期初欠款（负数）。
    # 这样消费、还款、退款都通过资金变动自动反映，不再出现「额度 5 万、余额 0」的两套账。
    if data.get("credit_limit") is not None and data.get("credit_used") is not None:
        if not data.get("opening_balance"):
            data["opening_balance"] = -_dec(data["credit_used"])
    data.pop("credit_used", None)

    category_id = await _validate_category(db, user.household_id or "", data.get("category_id"))
    account = Account(
        id=new_id(),
        household_id=user.household_id or "",
        name=data["name"],
        account_type=data.get("account_type", "bank"),
        category_id=category_id,
        card_last4=data.get("card_last4"),
        holder_name=data.get("holder_name"),
        credit_limit=data.get("credit_limit"),
        credit_used=data.get("credit_used"),
        card_expiry=data.get("card_expiry"),
        bill_day=data.get("bill_day"),
        due_day=data.get("due_day"),
        interest_rate=data.get("interest_rate"),
        term_months=data.get("term_months"),
        maturity_date=data.get("maturity_date"),
        institution=data.get("institution"),
        currency=data.get("currency", "CNY"),
        member_id=data.get("member_id"),
        opening_balance=data.get("opening_balance", Decimal("0")),
        opening_date=data.get("opening_date") or local_now(),
        include_in_net_worth=data.get("include_in_net_worth", True),
        note=data.get("note"),
        status="active",
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(account)
    await db.flush()

    await audit_service.record_audit(
        db,
        action="account.create",
        action_label="新增账户",
        object_type="account",
        object_id=account.id,
        after={
            "name": account.name,
            "account_type": account.account_type,
            "currency": account.currency,
            "opening_balance": account.opening_balance,
            "opening_date": account.opening_date,
            "include_in_net_worth": account.include_in_net_worth,
        },
        user=user,
        ctx=ctx,
    )
    # 新建账户必须清掉缓存列表，否则同一个请求里再读账户列表看不到它
    from app.core.agg_cache import invalidate
    invalidate(db, "accounts")
    return account


async def update_account(
    db: AsyncSession, user: CurrentUser, account_id: str, payload: dict,
    expected_version: int | None = None, ctx: RequestContext | None = None,
) -> Account:
    account = await get_account(db, user, account_id)
    if expected_version is not None and account.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)

    data = _validate_payload(payload, partial=True)
    if data.get("name"):
        await _ensure_unique_name(db, user.household_id or "", data["name"], exclude_id=account.id)

    # 「已用额度」是派生值（由余额决定），编辑时不再作为可写字段。
    # 要修正历史欠款请走冲销/更正，避免与资金变动冲突。
    if data.get("credit_limit") is not None and data.get("credit_used") is not None:
        if not data.get("opening_balance") and not account.opening_balance:
            data["opening_balance"] = -_dec(data["credit_used"])
    data.pop("credit_used", None)

    before = {f: getattr(account, f) for f in EDITABLE_FIELDS}

    # 已有业务数据的账户不允许直接改币种：会造成历史汇率口径错乱
    if "currency" in data and data["currency"] != account.currency:
        used = (
            await db.execute(
                select(func.count(EventMovement.id)).where(
                    EventMovement.target_type == "account",
                    EventMovement.target_id == account.id,
                )
            )
        ).scalar_one()
        if int(used) > 0:
            raise AppError(
                ErrorCode.STATE_INVALID,
                "该账户已存在资金变动，不能直接修改币种；请新建账户后通过转账迁移",
                field_errors={"currency": "已有流水记录的账户不可改币种"},
                status_code=409,
            )

    if "category_id" in data:
        data["category_id"] = await _validate_category(
            db, user.household_id or "", data["category_id"])

    for field, value in data.items():
        setattr(account, field, value)
    account.version += 1
    account.updated_by = user.user_id

    after = {f: getattr(account, f) for f in EDITABLE_FIELDS}
    await audit_service.record_audit(
        db,
        action="account.update",
        action_label="编辑账户",
        object_type="account",
        object_id=account.id,
        before=before,
        after=after,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return account


async def assert_account_deletable(db: AsyncSession, account: Account) -> None:
    """有资金变动的账户不能删除，只能停用（06 §12：删除统一改为冲销/归档/软删除）。"""
    used = (
        await db.execute(
            select(func.count(EventMovement.id)).where(
                EventMovement.target_type == "account",
                EventMovement.target_id == account.id,
            )
        )
    ).scalar_one()
    if int(used) > 0:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该账户已存在资金变动，不能删除，只能停用",
            status_code=409,
        )


async def archive_account(
    db: AsyncSession, user: CurrentUser, account_id: str, reason: str,
    ctx: RequestContext | None = None,
) -> Account:
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "停用账户必须填写原因",
                       field_errors={"reason": "请填写停用原因"})
    account = await get_account(db, user, account_id)
    before = {"status": account.status}
    account.status = "disabled"
    account.version += 1
    account.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="account.archive",
        action_label="停用账户",
        object_type="account",
        object_id=account.id,
        before=before,
        after={"status": account.status},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return account


async def account_flow(
    db: AsyncSession, user: CurrentUser, account_id: str, *, limit: int = 50
) -> list[tuple[BusinessEvent, EventMovement]]:
    """账户流水：该账户相关的已确认资金变动及其事件。"""
    rows = (
        await db.execute(
            select(BusinessEvent, EventMovement)
            .join(EventMovement, EventMovement.event_id == BusinessEvent.id)
            .where(
                EventMovement.target_type == "account",
                EventMovement.target_id == account_id,
                BusinessEvent.household_id == user.household_id,
                BusinessEvent.deleted_at.is_(None),
            )
            .order_by(BusinessEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    return [(r[0], r[1]) for r in rows]
