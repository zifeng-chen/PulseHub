"""借出款（应收）服务。

口径：
- 借出不是支出。借出时：资金来源账户减少（信用卡则欠款增加），同时新增一笔应收资产，
  净资产不变；因此借出款**计入净资产**，但不需要再从净资产里扣一次。
- 回款不是收入。回款时：账户余额增加、应收资产减少，净资产仍然不变；
  只有全部收回后资产自动归档，形态从「应收」变回「现金」。
- 借款人承担手续费时，出借人没有成本；自己承担时手续费计入支出。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.ids import new_id
from app.core.logging import mask_value
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.db.models.assets import AssetReceivable, ReceivablePeriod, ReceivableRepayment
from app.db.models.business import Asset


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _parse_dt(value, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return to_local_naive(value)
    try:
        return to_local_naive(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        raise AppError(ErrorCode.VALIDATION_ERROR, "日期格式不正确",
                       field_errors={field: "日期格式应为 ISO 8601"})


def validate_create(payload: dict) -> dict:
    """校验借出款创建参数（不写库）。"""
    field_errors: dict[str, str] = {}
    data: dict = {}

    borrower = (payload.get("borrower_name") or "").strip()
    if not borrower:
        field_errors["borrower_name"] = "请填写借款人"
    elif len(borrower) > 64:
        field_errors["borrower_name"] = "借款人姓名最多 64 个字符"
    else:
        data["borrower_name"] = borrower

    id_no = (payload.get("borrower_id_no") or "").strip().replace(" ", "")
    if not id_no:
        # 借出款有凭证属性：身份证号必填，否则将来无法主张权利
        field_errors["borrower_id_no"] = "请填写借款人身份证号"
    elif len(id_no) > 64:
        field_errors["borrower_id_no"] = "身份证号过长"
    else:
        # 简单形状校验：18 位居民身份证（末位可为 X）或 15 位旧证；其他证件号不强行卡死
        if not (len(id_no) in (15, 18) and id_no[:-1].isdigit()
                and (id_no[-1].isdigit() or id_no[-1] in "xX")):
            field_errors["borrower_id_no"] = "身份证号应为 18 位（末位可为 X）"
        else:
            data["borrower_id_no"] = id_no.upper()

    # 登记方式：account=从账户出借（生成资金事件）/ existing=补录既有借出（只登记应收）
    lent_mode = (payload.get("lent_mode") or "account").strip()
    if lent_mode not in ("account", "existing"):
        field_errors["lent_mode"] = "登记方式只能是「从账户出借」或「补录既有借出」"
    else:
        data["lent_mode"] = lent_mode

    lend_type = payload.get("lend_type") or "cash"
    if lend_type not in enum_zh.LEND_TYPE:
        field_errors["lend_type"] = "请选择有效的出借方式"
    else:
        data["lend_type"] = lend_type

    try:
        principal = Decimal(str(payload.get("principal") or "0"))
        if not principal.is_finite() or principal <= 0:
            raise ValueError
    except Exception:  # noqa: BLE001
        field_errors["principal"] = "借款金额必须大于 0"
    else:
        if principal > Decimal("1e15"):
            field_errors["principal"] = "借款金额超出允许范围"
        else:
            data["principal"] = principal

    repayment_method = payload.get("repayment_method") or "lump_sum"
    if repayment_method not in enum_zh.LEND_REPAYMENT_METHOD:
        field_errors["repayment_method"] = "请选择还款方式"
    else:
        data["repayment_method"] = repayment_method

    fee_bearer = payload.get("fee_bearer") or "borrower"
    if fee_bearer not in enum_zh.FEE_BEARER:
        field_errors["fee_bearer"] = "请选择手续费承担方"
    else:
        data["fee_bearer"] = fee_bearer

    fee_amount = payload.get("fee_amount")
    if fee_amount not in (None, ""):
        try:
            fee = Decimal(str(fee_amount))
            if not fee.is_finite() or fee < 0:
                raise ValueError
        except Exception:  # noqa: BLE001
            field_errors["fee_amount"] = "手续费必须大于等于 0"
        else:
            data["fee_amount"] = fee

    data["lent_at"] = _parse_dt(payload.get("lent_at"), "lent_at")
    data["due_date"] = _parse_dt(payload.get("due_date"), "due_date")
    if not data["lent_at"]:
        data["lent_at"] = local_now()

    note = (payload.get("note") or "").strip()
    if note:
        data["note"] = note[:2000]

    source_account_id = payload.get("source_account_id") or None
    if lent_mode == "existing":
        # 补录既有借出：钱早就出去了，不再从现在的账户扣，来源账户可不填
        data["source_account_id"] = source_account_id
    elif source_account_id:
        data["source_account_id"] = source_account_id
    elif lend_type in ("bank_card", "credit_card", "e_wallet"):
        field_errors["source_account_id"] = "请选择出借资金来源账户"

    # 利息约定：可选；计息时利率必填且有合理上限
    interest_enabled = bool(payload.get("interest_enabled"))
    data["interest_enabled"] = interest_enabled
    if interest_enabled:
        raw_rate = payload.get("interest_rate")
        if raw_rate in (None, ""):
            field_errors["interest_rate"] = "约定计息时请填写年化利率"
        else:
            try:
                rate = Decimal(str(raw_rate))
                if not rate.is_finite() or rate < 0:
                    raise ValueError
            except Exception:  # noqa: BLE001
                field_errors["interest_rate"] = "利率必须是非负数字"
            else:
                if rate > Decimal("100"):
                    field_errors["interest_rate"] = "年化利率不应超过 100%"
                else:
                    data["interest_rate"] = rate
    else:
        data["interest_rate"] = None

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "借出款信息校验失败", field_errors=field_errors)
    return data


async def create_for_asset(
    db: AsyncSession, user: CurrentUser, asset: Asset, payload: dict,
) -> AssetReceivable:
    """在资产创建成功后写入借出款明细（与资产同一事务）。"""
    data = validate_create(payload)
    row = AssetReceivable(
        id=new_id(),
        household_id=asset.household_id,
        asset_id=asset.id,
        borrower_name=data["borrower_name"],
        borrower_id_no=encrypt_secret(data.get("borrower_id_no")),
        lend_type=data["lend_type"],
        lent_mode=data.get("lent_mode", "account"),
        interest_enabled=data.get("interest_enabled", False),
        interest_rate=data.get("interest_rate"),
        principal=data["principal"],
        lent_at=data["lent_at"],
        repayment_method=data["repayment_method"],
        due_date=data.get("due_date"),
        fee_bearer=data["fee_bearer"],
        fee_amount=data.get("fee_amount"),
        source_account_id=data.get("source_account_id"),
        note=data.get("note"),
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(row)
    # 借出款的「当前价值」就是未收回本金
    asset.current_value = data["principal"]
    asset.acquire_cost = data["principal"]
    if not asset.acquire_date:
        asset.acquire_date = data["lent_at"]
    await db.flush()
    return row


async def get_for_asset(db: AsyncSession, user: CurrentUser, asset_id: str) -> AssetReceivable | None:
    row = (
        await db.execute(
            select(AssetReceivable).where(
                AssetReceivable.asset_id == asset_id,
                AssetReceivable.household_id == user.household_id,
                AssetReceivable.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    return row


async def repaid_total(db: AsyncSession, receivable_id: str) -> Decimal:
    # 只累计「本金」部分：含利息的回款若按全额计，会把应收多减，剩余本金就不对了。
    # 历史记录没有拆分字段（principal_part=0），退回到 amount 以保持原值。
    rows = list((await db.execute(
        select(
            ReceivableRepayment.amount,
            ReceivableRepayment.principal_part,
            ReceivableRepayment.interest_part,
        ).where(
            ReceivableRepayment.receivable_id == receivable_id,
            ReceivableRepayment.deleted_at.is_(None),
        )
    )).all())
    total = Decimal("0")
    for amount, principal_part, interest_part in rows:
        total += principal_of(amount, principal_part, interest_part)
    return total


async def list_repayments(db: AsyncSession, receivable_id: str) -> list[ReceivableRepayment]:
    return list(
        (
            await db.execute(
                select(ReceivableRepayment)
                .where(
                    ReceivableRepayment.receivable_id == receivable_id,
                    ReceivableRepayment.deleted_at.is_(None),
                )
                .order_by(ReceivableRepayment.received_at.desc())
            )
        ).scalars().all()
    )


def serialize_repayment(row: ReceivableRepayment, account_name: str = "") -> dict:
    return {
        "id": row.id,
        "amount": f"{_dec(row.amount):.2f}",
        # 历史回款没有拆分字段时按全额本金回退，避免显示 0
        "principal_part": f"{_dec(row.principal_part or row.amount):.2f}",
        "interest_part": f"{_dec(row.interest_part):.2f}",
        "period_no": row.period_no,
        "affects_account": bool(row.affects_account),
        "account_id": row.account_id,
        "account_name": account_name,
        "received_at": local_iso(row.received_at),
        "note": row.note,
        "event_id": row.event_id,
    }


# ---------------- 分期计划 ----------------

def _month_end(year: int, month: int) -> datetime:
    """该月最后一天：约定「某月还」按月底截止，对借款人更宽容。"""
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return nxt - timedelta(days=1)


def _add_months(base: datetime, months: int) -> tuple[int, int]:
    total = base.year * 12 + (base.month - 1) + months
    return total // 12, total % 12 + 1


async def list_periods(db: AsyncSession, receivable_id: str) -> list[ReceivablePeriod]:
    return list((await db.execute(
        select(ReceivablePeriod).where(
            ReceivablePeriod.receivable_id == receivable_id,
            ReceivablePeriod.deleted_at.is_(None),
        ).order_by(ReceivablePeriod.period_no)
    )).scalars().all())


def build_periods(
    segments: list[dict], start_year: int | None = None, start_month: int | None = None,
) -> list[dict]:
    """按「分段」生成分期表：每段 = 连续 N 期、每期金额相同。

    用户的分期表常常是这种形状：首期 6000、之后 22 期 3200、再 13 期 5000、末期 3100。
    逐期手填 37 行太麻烦，用分段描述一次生成。
    """
    rows: list[dict] = []
    cursor = 0
    if start_year is None or start_month is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请提供起始年月",
                       field_errors={"start": "请提供起始年月"})
    for seg in segments:
        try:
            count = int(seg.get("count") or 0)
            amount = Decimal(str(seg.get("amount") or "0"))
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "分期分段格式不正确",
                           field_errors={"segments": "请填写期数与金额"})
        if count <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "每段期数必须大于 0",
                           field_errors={"segments": "每段期数必须大于 0"})
        if not amount.is_finite() or amount <= 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "每期金额必须大于 0",
                           field_errors={"segments": "每期金额必须大于 0"})
        for _ in range(count):
            year, month = _add_months(datetime(start_year, start_month, 1), cursor)
            rows.append({"period_no": cursor + 1, "due_date": _month_end(year, month),
                         "amount": amount})
            cursor += 1
    return rows


async def replace_periods(
    db: AsyncSession, user: CurrentUser, rec: AssetReceivable, rows: list[dict],
) -> list[ReceivablePeriod]:
    """整体替换分期计划（软删旧的），随后按已收本金重新分摊。"""
    # 期次号必须唯一：重复期次会让「按顺序分摊」把同一期算两次
    period_nos = [int(item["period_no"]) for item in rows]
    if len(period_nos) != len(set(period_nos)):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "分期期次重复",
            field_errors={"segments": "同一笔借款的分期期次不能重复"},
        )

    # 先校验再写：合计必须等于本金，否则逐期对账永远差一截
    total = sum((_dec(item["amount"]) for item in rows), Decimal("0"))
    if abs(total - _dec(rec.principal)) > Decimal("0.01"):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"分期合计 {total:.2f} 与借款本金 {_dec(rec.principal):.2f} 不一致",
            field_errors={"segments": f"分期合计应等于本金 {_dec(rec.principal):.2f}"},
        )

    old = await list_periods(db, rec.id)
    for row in old:
        row.deleted_at = local_now()
        row.updated_by = user.user_id

    for item in rows:
        db.add(ReceivablePeriod(
            id=new_id(), household_id=rec.household_id, receivable_id=rec.id,
            period_no=int(item["period_no"]),
            due_date=item.get("due_date"),
            amount=item["amount"],
            paid_amount=Decimal("0"),
            note=item.get("note"),
            created_by=user.user_id, updated_by=user.user_id,
        ))
    await db.flush()
    await reallocate(db, user, rec)
    return await list_periods(db, rec.id)


async def reallocate(db: AsyncSession, user: CurrentUser, rec: AssetReceivable) -> None:
    """把已收本金按顺序分摊到各期（先冲最早未结清的期）。

    不把回款绑死到某一期：一笔 57,200 可能覆盖前 17 期，一笔 1,000 也可能只够还半期。
    每次回款后整体重算，口径永远一致。
    """
    periods = await list_periods(db, rec.id)
    if not periods:
        return
    remaining = await repaid_total(db, rec.id)
    for period in periods:
        amount = _dec(period.amount)
        if remaining <= 0:
            period.paid_amount = Decimal("0")
        else:
            alloc = min(remaining, amount)
            period.paid_amount = alloc
            remaining -= alloc
        period.updated_by = user.user_id
    await db.flush()


def serialize_period(period: ReceivablePeriod, now: datetime | None = None) -> dict:
    now = now or local_now()
    amount = _dec(period.amount)
    paid = _dec(period.paid_amount)
    unpaid = max(Decimal("0"), amount - paid)
    if unpaid <= Decimal("0.005"):
        status, label = "paid", "已还"
    elif paid > 0:
        status, label = "partial", f"部分（还欠 {unpaid:.2f}）"
    elif period.due_date and period.due_date < now:
        status, label = "overdue", "逾期未还"
    else:
        status, label = "pending", "未还"
    return {
        "id": period.id,
        "period_no": period.period_no,
        "due_date": local_iso(period.due_date),
        "amount": f"{amount:.2f}",
        "paid_amount": f"{paid:.2f}",
        "unpaid": f"{unpaid:.2f}",
        "status": status,
        "status_label": label,
        "note": period.note,
    }


def read_id_no(value: str | None) -> str | None:
    """读取身份证号：兼容尚未加密的历史明文（解密失败即按明文处理）。"""
    if not value:
        return None
    plain = decrypt_secret(value)
    return plain if plain is not None else value


def principal_of(amount, principal_part, interest_part) -> Decimal:
    """一笔回款的「本金部分」口径。

    历史记录没有拆分字段，本金/利息都是默认 0，此时整笔金额都算本金；
    新记录明确拆分的，`principal_part = 0` 就是「只收了利息」，不能回退成全额。
    """
    principal = _dec(principal_part)
    if principal == 0 and _dec(interest_part) == 0:
        return _dec(amount)
    return principal


def mask_id_no(value: str | None) -> str:
    """身份证号脱敏：保留前 6 位（地区码）与后 6 位（含校验位），中间打码。

    通用的 mask_value 只留前后 2 位（31**************19），既看不清是谁、
    也不方便本人核对；身份证按前 6 后 6 既能确认归属，又不泄露完整号码。
    """
    if not value:
        return ""
    text = str(value).strip()
    if len(text) <= 12:
        return "*" * len(text)
    keep = 6
    return f"{text[:keep]}{'*' * (len(text) - keep * 2)}{text[-keep:]}"


def _pct(part: Decimal, whole: Decimal) -> float:
    if whole <= 0:
        return 0.0
    return round(float(part) / float(whole) * 100, 1)


def _period_spans(
    periods: list[dict], repayments: list[ReceivableRepayment],
) -> dict[str, str]:
    """算每笔回款覆盖哪几期（如 "18" / "18-19"），供界面直接显示，不用用户手填期次。

    做法：把分期表按顺序铺成金额区间，再按回款时间顺序累计本金，
    看每笔的起止落在哪些期。一笔跨多期、多笔凑一期都能算对。
    """
    if not periods:
        return {}
    bounds: list[tuple[Decimal, Decimal, int]] = []
    cursor = Decimal("0")
    for p in periods:
        amount = _dec(p["amount"])
        if amount <= 0:
            continue
        bounds.append((cursor, cursor + amount, int(p["period_no"])))
        cursor += amount
    if not bounds:
        return {}

    def locate(value: Decimal) -> int | None:
        # 严格按左闭右开区间找：正好等于某期结束额时，应算作「这一期已还完」，
        # 所以调用方在求结束期次时会先减去 1 分钱，避免落到下一期。
        for _start, end, no in bounds:
            if value < end:
                return no
        return bounds[-1][2]

    spans: dict[str, str] = {}
    cum = Decimal("0")
    ordered = sorted(
        repayments,
        key=lambda r: (r.received_at or datetime.min, r.id),
    )
    for r in ordered:
        part = principal_of(r.amount, r.principal_part, r.interest_part)
        if part <= 0:
            continue
        from_no = locate(cum)
        cum += part
        to_no = locate(max(Decimal("0"), cum - Decimal("0.01")))
        if from_no and to_no:
            spans[r.id] = f"{from_no}" if from_no == to_no else f"{from_no}-{to_no}"
    return spans


def build_reconcile(
    rec: AssetReceivable, repaid: Decimal, repayments: list[ReceivableRepayment],
    now: datetime | None = None,
) -> dict:
    """借出款对账口径：只看「总额 + 期限够不够」，不逐期卡金额。

    约定金额每期不同（甚至不固定期数）时，逐期计划没有意义——用户要的是
    「到约定还款日能不能还完」。这里给三个判断：进度 vs 时间进度、
    落后时还差多少、按当前节奏预计何时还清。
    """
    now = now or local_now()
    principal = _dec(rec.principal)
    remaining = max(Decimal("0"), principal - repaid)
    lent_at = rec.lent_at
    due = rec.due_date

    paid_pct = _pct(repaid, principal)
    result: dict = {
        "principal": f"{principal:.2f}",
        "paid": f"{repaid:.2f}",
        "remaining": f"{remaining:.2f}",
        "paid_percent": paid_pct,
        "repayment_count": len(repayments),
        "settled": remaining <= 0,
        "has_due_date": due is not None,
        "has_schedule_window": bool(lent_at and due and due > lent_at),
    }
    if remaining <= 0:
        result.update({
            "status": "settled", "status_label": "已结清",
            "time_percent": 100.0, "days_left": 0, "days_total": None,
            "gap_to_catch_up": "0.00", "projected_settle_date": None,
            "daily_needed": None, "overdue_days": 0,
        })
        return result

    if not (lent_at and due and due > lent_at):
        # 没登记「何时借出 / 何时该还完」就没法比时间进度，只报总额
        result.update({
            "status": "no_window", "status_label": "未约定还清期限",
            "time_percent": None, "days_left": None, "days_total": None,
            "gap_to_catch_up": "0.00", "projected_settle_date": None,
            "daily_needed": None, "overdue_days": 0,
        })
        return result

    days_total = max(1, (due - lent_at).days)
    days_elapsed = min(days_total, max(0, (now - lent_at).days))
    time_pct = round(days_elapsed / days_total * 100, 1)
    days_left = max(0, (due - now).days)
    overdue_days = 0 if now <= due else (now - due).days

    # 落后时要补多少才能回到「时间进度」这条线上
    expected_paid = principal * Decimal(days_elapsed) / Decimal(days_total)
    gap = max(Decimal("0"), expected_paid - repaid)

    # 剩余期限内每天至少要还多少
    daily_needed = None
    if days_left > 0:
        daily_needed = f"{(remaining / Decimal(days_left)):.2f}"

    # 按已有回款节奏推算还清日期（回款太少时不推）
    projected = None
    projected_dt = None
    elapsed_days = max(1, (now - lent_at).days)
    if repaid > 0 and elapsed_days >= 1:
        per_day = repaid / Decimal(elapsed_days)
        if per_day > 0:
            need_days = int(float(remaining / per_day))
            projected_dt = now + timedelta(days=max(0, need_days))
            projected = local_iso(projected_dt)

    # 只有「一分未收 + 期限逼近」才算真风险；其余情况不猜节奏会怎样。
    will_be_late = projected_dt is not None and projected_dt > due

    # 口径：借贷双方约定的是「期限内还完」，不是「均匀还」。
    # 前期还得少、后期按计划还，用「已还% vs 时间%」去判落后会误报，
    # 所以状态只反映事实——到期没有？还有多少？每天要还多少。
    if overdue_days > 0:
        status = "overdue"
        label = f"已逾期 {overdue_days} 天，尚欠 {remaining:.2f}"
    elif repaid <= 0 and days_left <= 30:
        status = "at_risk"
        label = f"尚未收到回款 · 剩 {days_left} 天需还 {remaining:.2f}"
    else:
        status = "on_track"
        label = f"剩余 {remaining:.2f} · 剩 {days_left} 天 · 每天需还 {daily_needed}"

    result.update({
        "status": status,
        "status_label": label,
        "time_percent": time_pct,
        "days_total": days_total,
        "days_elapsed": days_elapsed,
        "days_left": days_left,
        "overdue_days": overdue_days,
        "gap_to_catch_up": f"{gap:.2f}",
        "daily_needed": daily_needed,
        "projected_settle_date": projected,
        "will_be_late": will_be_late,
        "due_date": local_iso(due),
    })
    return result


def serialize_receivable(
    row: AssetReceivable,
    *,
    repaid: Decimal,
    repayments: list[tuple[ReceivableRepayment, str]],
    reveal: bool = False,
    now: datetime | None = None,
    periods: list[ReceivablePeriod] | None = None,
) -> dict:
    outstanding = max(Decimal("0"), _dec(row.principal) - repaid)
    reconcile = build_reconcile(row, repaid, [r for r, _ in repayments], now=now)
    period_rows = [serialize_period(p, now=now) for p in (periods or [])]
    spans = _period_spans(period_rows, [r for r, _ in repayments])
    if period_rows:
        reconcile["has_schedule"] = True
        reconcile["period_count"] = len(period_rows)
        reconcile["paid_periods"] = sum(1 for p in period_rows if p["status"] == "paid")
        reconcile["unpaid_periods"] = sum(1 for p in period_rows if p["status"] != "paid")
        reconcile["overdue_periods"] = sum(1 for p in period_rows if p["status"] == "overdue")
    return {
        "id": row.id,
        "borrower_name": row.borrower_name,
        "borrower_id_no": (
            read_id_no(row.borrower_id_no) if reveal
            else mask_id_no(read_id_no(row.borrower_id_no))
        ),
        "lend_type": row.lend_type,
        "lend_type_label": enum_zh.zh("lend_type", row.lend_type),
        "lent_mode": row.lent_mode or "account",
        "lent_mode_label": "从账户出借" if (row.lent_mode or "account") == "account" else "补录既有借出",
        "principal": f"{_dec(row.principal):.2f}",
        "repaid_total": f"{_dec(repaid):.2f}",
        "outstanding": f"{outstanding:.2f}",
        "lent_at": local_iso(row.lent_at),
        "due_date": local_iso(row.due_date),
        "repayment_method": row.repayment_method,
        "repayment_method_label": enum_zh.zh("lend_repayment_method", row.repayment_method),
        "interest_enabled": bool(row.interest_enabled),
        "interest_rate": f"{_dec(row.interest_rate):.2f}" if row.interest_rate is not None else None,
        "interest_received": f"{_dec(row.interest_received):.2f}",
        "fee_bearer": row.fee_bearer,
        "fee_bearer_label": enum_zh.zh("fee_bearer", row.fee_bearer),
        "fee_amount": f"{_dec(row.fee_amount):.2f}" if row.fee_amount is not None else None,
        "source_account_id": row.source_account_id,
        "note": row.note,
        "settled": outstanding <= 0,
        # 对账：金额不固定的分期只比「总额 + 期限」，不逐期卡金额
        "reconcile": reconcile,
        "periods": period_rows,
        "repayments": [
            {**serialize_repayment(r, name), "period_range": spans.get(r.id)}
            for r, name in repayments
        ],
    }


async def update_for_asset(
    db: AsyncSession, user: CurrentUser, asset: Asset, payload: dict,
) -> AssetReceivable:
    """更新借出款明细（编辑资产时调用）。

    以前 update_asset 完全忽略 payload.receivable，用户改编借款人 / 还款方式 / 利率
    后保存，改动被静默丢弃——这里补上。

    两条保护：
    1. 身份证号留空或传回掩码值时保留原值（详情接口默认脱敏，不能拿掩码覆盖真值）。
    2. 已经有回款记录时不允许改本金，否则「已还 + 未还」对不上。
    """
    rec = (await db.execute(
        select(AssetReceivable).where(AssetReceivable.asset_id == asset.id)
    )).scalars().first()
    if rec is None:
        return await create_for_asset(db, user, asset, payload)

    # 身份证：留空或用掩码回传，都视为「不修改」
    raw_id = (payload.get("borrower_id_no") or "").strip().replace(" ", "")
    keep_id = (not raw_id) or ("*" in raw_id)
    # 校验时把原值塞回去，避免「必填」规则挡住编辑
    probe = dict(payload)
    if keep_id:
        probe["borrower_id_no"] = read_id_no(rec.borrower_id_no) or ""
    data = validate_create(probe)
    if keep_id:
        # 保留原密文，避免重新加密同一份明文
        data["borrower_id_no"] = rec.borrower_id_no
    else:
        data["borrower_id_no"] = encrypt_secret(data.get("borrower_id_no"))

    repaid = await repaid_total(db, rec.id)
    if _dec(data["principal"]) != _dec(rec.principal) and repaid > 0:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "已有回款记录，不能修改借款本金；如需调整请先作废回款",
            status_code=409,
            field_errors={"principal": f"已收回 {repaid:.2f}，本金不可修改"},
        )

    for field in ("borrower_name", "borrower_id_no", "lend_type", "lent_mode",
                  "principal", "lent_at", "repayment_method", "due_date",
                  "interest_enabled", "interest_rate", "fee_bearer", "fee_amount",
                  "source_account_id", "note"):
        if field in data:
            setattr(rec, field, data[field])
    rec.updated_by = user.user_id

    # 资产当前价值始终等于「未收回本金」
    asset.current_value = max(Decimal("0"), _dec(rec.principal) - repaid)
    if not asset.acquire_date and rec.lent_at:
        asset.acquire_date = rec.lent_at
    asset.updated_by = user.user_id
    await db.flush()
    return rec


async def add_repayment(
    db: AsyncSession,
    user: CurrentUser,
    asset_id: str,
    payload: dict,
    ctx: RequestContext | None = None,
) -> tuple[ReceivableRepayment, dict]:
    """登记一笔回款（支持部分回款），生成 receivable_repay 事件并更新应收余额。"""
    from app.services import audit as audit_service
    from app.services import events as event_service

    rec = await get_for_asset(db, user, asset_id)
    if rec is None:
        raise AppError(ErrorCode.NOT_FOUND, "借出款记录不存在", status_code=404)

    asset = (
        await db.execute(
            select(Asset).where(Asset.id == asset_id, Asset.household_id == user.household_id)
        )
    ).scalars().first()
    if asset is None or asset.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "资产不存在", status_code=404)

    try:
        amount = Decimal(str(payload.get("amount") or "0"))
        if not amount.is_finite() or amount <= 0:
            raise ValueError
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "回款金额必须大于 0",
                       field_errors={"amount": "回款金额必须大于 0"})

    # 一次回款可以含利息：本金冲减应收，利息计入利息收入（真正算收入的部分）
    interest_part = Decimal("0")
    raw_interest = payload.get("interest_part")
    if raw_interest not in (None, ""):
        try:
            interest_part = Decimal(str(raw_interest))
            if not interest_part.is_finite() or interest_part < 0:
                raise ValueError
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "利息金额必须是非负数字",
                           field_errors={"interest_part": "利息金额必须是非负数字"})
    if interest_part > amount:
        raise AppError(ErrorCode.VALIDATION_ERROR, "利息不能超过回款总额",
                       field_errors={"interest_part": "利息不能超过回款总额"})
    principal_part = amount - interest_part
    if principal_part <= 0 and interest_part > 0:
        # 纯收利息、不动本金：允许（先息后本的常见形态）
        principal_part = Decimal("0")

    # 期次可选手填：金额不固定的分期，用户自己说这是第几期，方便和对方核对
    period_no = None
    raw_period = payload.get("period_no")
    if raw_period not in (None, ""):
        try:
            period_no = int(raw_period)
            if period_no <= 0:
                raise ValueError
        except Exception:  # noqa: BLE001
            raise AppError(ErrorCode.VALIDATION_ERROR, "期次应是大于 0 的整数",
                           field_errors={"period_no": "期次应是大于 0 的整数"})

    # 补录历史回款：钱当时确实收到了，但早已花掉，不能让它把当前账户余额冲高
    affects_account = payload.get("affects_account")
    affects_account = True if affects_account is None else bool(affects_account)

    account_id = payload.get("account_id")
    if not account_id and affects_account:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请选择到账账户",
                       field_errors={"account_id": "请选择回款到账账户"})

    repaid_before = await repaid_total(db, rec.id)
    outstanding_before = max(Decimal("0"), _dec(rec.principal) - repaid_before)
    if outstanding_before <= 0 and interest_part <= 0:
        raise AppError(ErrorCode.STATE_INVALID, "该笔借出款已全部收回", status_code=409)
    if principal_part > outstanding_before + Decimal("0.005"):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"本金部分不能超过未收回本金 {outstanding_before:.2f}",
            field_errors={"amount": f"本金最多可登记 {outstanding_before:.2f}"},
        )

    occurred_at = _parse_dt(payload.get("occurred_at"), "occurred_at") or local_now()
    note = (payload.get("note") or "").strip() or None

    # 本金回款：资金转入（不计收入）。
    # 历史回款（affects_account=False）不生成事件，否则当前账户会凭空多出一笔钱。
    event = None
    if principal_part > 0 and affects_account:
        event = await event_service.create_draft(db, user, {
            "event_type": "receivable_repay",
            "title": f"借出款回款 · {rec.borrower_name}",
            "amount": f"{principal_part:.4f}",
            "occurred_at": local_iso(occurred_at),
            "account_id": account_id,
            "asset_id": asset_id,
            "note": note,
        }, ctx)
        await event_service.confirm_event(db, user, event.id, None, ctx)

    # 利息：这是出借人的真实收入，单独记一笔收入事件，否则收入统计会漏。
    # 历史回款的利息当年已经收过（也花掉了），不能再记一次收入。
    if interest_part > 0 and affects_account:
        from app.services import catalog as catalog_service

        cat_id = await catalog_service.ensure_category_id(
            db, user.household_id or "", "income", "利息收入", parent_name="投资收益")
        interest_event = await event_service.create_draft(db, user, {
            "event_type": "income",
            "title": f"借出款利息 · {rec.borrower_name}",
            "amount": f"{interest_part:.4f}",
            "occurred_at": local_iso(occurred_at),
            "account_id": account_id,
            "asset_id": asset_id,
            "category_id": cat_id,
            "note": note or "借出款回款中的利息部分",
        }, ctx)
        await event_service.confirm_event(db, user, interest_event.id, None, ctx)

    repayment = ReceivableRepayment(
        id=new_id(),
        household_id=asset.household_id,
        receivable_id=rec.id,
        asset_id=asset_id,
        amount=amount,
        principal_part=principal_part,
        interest_part=interest_part,
        period_no=period_no,
        affects_account=affects_account,
        account_id=account_id,
        received_at=occurred_at,
        note=note,
        event_id=event.id if event else None,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(repayment)

    if interest_part > 0:
        rec.interest_received = _dec(rec.interest_received) + interest_part

    outstanding_after = outstanding_before - principal_part
    asset.current_value = outstanding_after
    if outstanding_after <= 0:
        asset.status = "archived"
    asset.updated_by = user.user_id

    # 应收余额变化写入估值时间线，保证资产价值可追溯
    from app.db.models.assets import AssetValuation

    db.add(AssetValuation(
        id=new_id(),
        household_id=asset.household_id,
        asset_id=asset_id,
        value=outstanding_after,
        currency=asset.currency,
        valued_at=occurred_at,
        source="manual",
        reason="借出款回款",
        created_by=user.user_id,
        updated_by=user.user_id,
    ))
    await db.flush()

    # 回款后按顺序重新分摊到各期（有分期计划时才有意义）
    await reallocate(db, user, rec)

    await audit_service.record_audit(
        db,
        action="asset.receivable_repayment",
        action_label="借出款回款",
        object_type="asset",
        object_id=asset_id,
        after={"amount": f"{amount:.4f}", "principal": f"{principal_part:.4f}",
               "interest": f"{interest_part:.4f}",
               "outstanding": f"{outstanding_after:.4f}",
               "account_id": account_id,
               "event_no": event.event_no if event else None},
        user=user,
        ctx=ctx,
    )
    return repayment, serialize_receivable(
        rec, repaid=repaid_before + principal_part, repayments=[], reveal=False,
    )
