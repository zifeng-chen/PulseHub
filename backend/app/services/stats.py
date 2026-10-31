"""统计查询基座（23 §10、01 §4、18 §4）。

所有金额由后端确定性服务计算，前端不得自行计算净资产、余额、损益或汇总金额。

口径（与《01 §4》一致）：
- 收入只包含已确认收入；借款、押金、内部转入不算收入；
- 支出含已确认消费、利息、手续费与贷款还款本金；信用卡还款不重复计入；
- 内部转账只改变账户余额，不改变收入和支出；
- 草稿、已取消、已冲销事件一律不进统计；
- 多币种**按币种分组**，不同币种不做相加；只有存在有效汇率且用户确认后才换算。
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.timeutil import local_iso, to_local_naive
from app.core.context import CurrentUser
from app.core.ids import utc_iso
from app.core.money import fmt_amount
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.config import HouseholdSetting

# 时区兜底：Windows 等环境可能没有 IANA 时区库（需 pip 包 tzdata），
# 此时退化为固定偏移，保证统计接口永不因时区解析失败而不可用。
_TZ_FIXED_OFFSET = {
    "Asia/Shanghai": 8, "Asia/Hong_Kong": 8, "Asia/Taipei": 8,
    "Asia/Singapore": 8, "Asia/Tokyo": 9, "Asia/Seoul": 9, "UTC": 0,
}


def resolve_tz(name: str):
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=_TZ_FIXED_OFFSET.get(name, 0)))

PERIODS = {
    "current_month": "本月",
    "last_month": "上月",
    "last_3_months": "近三个月",
    "last_6_months": "近六个月",
    "current_year": "本年",
    "all": "全部",
}

# 可计入收入/支出统计的事件类型（其余类型在其所属模块交付后接入）
# M4：租金实收计入收入（06 §9）；押金收取/退还与租金应收均不计入。
INCOME_EVENT_TYPES = ("income", "rent_received")
# 支出含「消费/利息/手续费」与「贷款还款本金」：还款本金是实打实的现金流出，
# 用户看「本期支出」时希望看到还贷掏了多少钱，而不是只剩利息那一小部分。
EXPENSE_EVENT_TYPES = ("expense", "liability_repay")

# 生存跑道动态模拟假设（保守取值，来自家庭财务规划的通行口径）：
# - 闲置资金按货币基金/国债水平 2.5% 年化滚动
# - 家庭支出按 3.5% 年通胀复利递增
# - 模拟上限 120 个月（超过即视为「10 年以上」）
RUNWAY_INVEST_RETURN = Decimal("0.025")
RUNWAY_INFLATION = Decimal("0.035")
RUNWAY_MAX_MONTHS = 120


def _dynamic_runway(
    asset: Decimal, monthly_expense: Decimal, months: int = RUNWAY_MAX_MONTHS,
) -> int | None:
    """逐月模拟「这笔钱还能撑几个月」：资产按年化收益滚存，支出按年通胀递增。

    比「流动资金 ÷ 月均支出」更接近现实——钱在账上会有收益，但支出会逐年变贵。
    返回可支撑月数；撑过 `months` 个月返回 None（表示 10 年以上）。
    """
    if asset <= 0 or monthly_expense <= 0:
        return None
    balance = asset
    monthly_return = RUNWAY_INVEST_RETURN / Decimal(12)
    for month in range(1, months + 1):
        balance += balance * monthly_return
        inflator = (Decimal(1) + RUNWAY_INFLATION) ** ((month - 1) // 12)
        balance -= monthly_expense * inflator
        if balance <= 0:
            return month
    return None

CALIBER = {
    "net_worth": (
        "净资产 = 账户余额 + 资产当前价值 + 投资总额（持仓市值 + 账户现金） − 负债剩余本金。"
        "账户、资产、负债、投资都可单独设置「是否纳入净资产」；"
        "信用卡等信用账户余额为负表示欠款，会作为负项扣减；投资总额另受全局「投资估值纳入净资产」开关控制"
    ),
    "account_balance": "期初余额 + 已确认事件的资金变动净额",
    "liquidity": (
        "流动资金 = 现金类账户余额（储蓄卡 / 电子钱包 / 现金，有授信额度的信用类账户不算）"
        " + 投资账户现金；**不扣除**信用卡待还与负债，"
        "能否还上由「偿债覆盖」判断（流动资金 ÷ 待还合计）"
    ),
    "debt_service": (
        "信用卡本期应还 = 未结清账单的未还金额（没有建过账单的卡按欠款总额估算）；"
        "待还合计 = 信用卡本期应还 + 未来 30 天负债应还（各负债月供）；"
        "偿债覆盖 = 流动资金 ÷ 待还合计，≥1.5 倍从容、1–1.5 倍偏紧、<1 倍不足"
    ),
    "asset_value": "资产当前价值以资产主表为准；只有「计入净资产 且 未出售/未报废/未归档」的资产参与净资产",
    "income": "已确认收入与租金实收；借款、押金、内部转入、租金应收均不计入",
    "expense": "已确认消费、利息、手续费与贷款还款本金；信用卡还款属内部转账不重复计入",
    "transfer": "内部转账只改变账户余额，不改变收入与支出",
    "multi_currency": "不同币种按币种分组，不做相加；缺少有效汇率时不换算",
    "excludes_draft": True,
}


def _liability_base(liability) -> Decimal:
    """剩余本金起算点（与负债服务同口径：建档剩余优先，否则贷款金额）。"""
    from app.services.liabilities import base_outstanding

    return base_outstanding(liability)


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def money(value: Decimal) -> str:
    """金额对外统一输出（默认两位小数，遵循家庭「金额精度」配置）。"""
    return fmt_amount(value)


async def _cached_rows(db: AsyncSession, key: tuple, loader):
    """同一请求内复用同一批原始行。

    净资产趋势要对 12 个月末各算一次「资产/投资/负债在该时点的值」，
    每次都会把全表读一遍再按日期筛。同一只读请求里数据不会变，
    所以把「原始行」缓存在 db.info（会话级），改成读一次、按月算 12 次。
    """
    from app.core.agg_cache import request_cached

    return await request_cached(db, ("trend_rows",) + tuple(key), loader)


def period_range(period: str, tz_name: str) -> tuple[datetime | None, datetime | None, str]:
    """返回 (起始UTC, 结束UTC, 人类可读期间)。开始包含、结束不包含。"""
    tz = resolve_tz(tz_name)

    now_local = datetime.now(tz)
    start_local: datetime | None
    end_local: datetime | None

    if period == "current_month":
        start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = (start_local + timedelta(days=32)).replace(day=1)
    elif period == "last_month":
        first_this = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = first_this
        start_local = (first_this - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period in ("last_3_months", "last_6_months"):
        months = 3 if period == "last_3_months" else 6
        first_this = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        year, month = first_this.year, first_this.month
        month -= months - 1
        while month <= 0:
            month += 12
            year -= 1
        start_local = first_this.replace(year=year, month=month, day=1)
        end_local = (first_this + timedelta(days=32)).replace(day=1)
    elif period == "current_year":
        start_local = now_local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local.replace(year=start_local.year + 1)
    else:  # all
        return None, None, "全部期间"

    label = f"{start_local:%Y-%m-%d} ~ {(end_local - timedelta(days=1)):%Y-%m-%d}"
    # 业务时间按本地墙钟存储（见 app/core/timeutil.py），期间边界也用本地墙钟比较
    return start_local.replace(tzinfo=None), end_local.replace(tzinfo=None), label


async def _credit_card_liability_ids(db: AsyncSession, household_id: str) -> list[str]:
    """家庭内所有信用卡负债的 id（信用卡还款属内部转账，不重复计支出）。"""
    from app.db.models.finance import Liability

    rows = (await db.execute(
        select(Liability.id).where(
            Liability.household_id == household_id,
            Liability.liability_type == "credit_card",
            Liability.deleted_at.is_(None),
        )
    )).scalars().all()
    return list(rows)


async def _event_totals(
    db: AsyncSession, household_id: str, start: datetime | None, end: datetime | None
) -> dict[str, dict[str, Decimal]]:
    """按币种汇总已确认的收入与支出（带请求级缓存）。"""
    from app.core.agg_cache import request_cached

    return await request_cached(
        db,
        ("event_totals", household_id, start.isoformat() if start else "", end.isoformat() if end else ""),
        lambda: _event_totals_uncached(db, household_id, start, end),
    )


async def _event_totals_uncached(
    db: AsyncSession, household_id: str, start: datetime | None, end: datetime | None
) -> dict[str, dict[str, Decimal]]:
    """按币种汇总已确认的收入与支出。"""
    conditions = [
        BusinessEvent.household_id == household_id,
        BusinessEvent.status == "confirmed",
        BusinessEvent.deleted_at.is_(None),
        BusinessEvent.event_type.in_(INCOME_EVENT_TYPES + EXPENSE_EVENT_TYPES + ("refund",)),
    ]
    if start is not None:
        conditions.append(BusinessEvent.occurred_at >= start)
    if end is not None:
        conditions.append(BusinessEvent.occurred_at < end)

    # 贷款还款本金计入支出，但信用卡还款除外（信用卡消费已计支出，还款不重复）
    card_ids = await _credit_card_liability_ids(db, household_id)
    if card_ids:
        conditions.append(
            ~(BusinessEvent.event_type == "liability_repay")
            | BusinessEvent.liability_id.not_in(card_ids)
            | BusinessEvent.liability_id.is_(None)
        )

    rows = (
        await db.execute(
            select(
                BusinessEvent.currency,
                BusinessEvent.event_type,
                func.coalesce(func.sum(BusinessEvent.amount), 0),
            )
            .where(*conditions)
            .group_by(BusinessEvent.currency, BusinessEvent.event_type)
        )
    ).all()

    totals: dict[str, dict[str, Decimal]] = {}
    for currency, event_type, total in rows:
        bucket = totals.setdefault(currency, {"income": Decimal("0"), "expense": Decimal("0")})
        # 退款不是收入：它冲减支出，避免「退款月收入虚高、支出不变」
        if event_type == "refund":
            bucket["expense"] -= _dec(total)
        elif event_type in INCOME_EVENT_TYPES:
            bucket["income"] += _dec(total)
        else:
            bucket["expense"] += _dec(total)
    return totals


async def _monthly_series(
    db: AsyncSession, household_id: str, months: int, tz_name: str
) -> list[dict]:
    """近 N 个月的收入/支出序列（按家庭时区切月）。同请求内结果不变，缓存复用。"""
    from app.core.agg_cache import request_cached

    return await request_cached(
        db,
        ("monthly_series", household_id, months, tz_name),
        lambda: _monthly_series_uncached(db, household_id, months, tz_name),
    )


async def _monthly_series_uncached(
    db: AsyncSession, household_id: str, months: int, tz_name: str
) -> list[dict]:
    tz = resolve_tz(tz_name)

    now_local = datetime.now(tz)
    first_this = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    starts: list[datetime] = []
    year, month = first_this.year, first_this.month
    month -= months - 1
    while month <= 0:
        month += 12
        year -= 1
    cursor = first_this.replace(year=year, month=month, day=1)
    for _ in range(months):
        starts.append(cursor)
        cursor = (cursor + timedelta(days=32)).replace(day=1)

    # 业务时间存本地墙钟，比较边界也用本地墙钟
    start_utc = starts[0].replace(tzinfo=None)
    end_utc = (first_this + timedelta(days=32)).replace(day=1).replace(tzinfo=None)

    card_ids = await _credit_card_liability_ids(db, household_id)
    card_filter = BusinessEvent.liability_id.not_in(card_ids) if card_ids else True
    rows = (
        await db.execute(
            select(BusinessEvent.occurred_at, BusinessEvent.event_type, BusinessEvent.amount,
                   BusinessEvent.currency, BusinessEvent.liability_id)
            .where(
                BusinessEvent.household_id == household_id,
                BusinessEvent.status == "confirmed",
                BusinessEvent.deleted_at.is_(None),
                BusinessEvent.event_type.in_(INCOME_EVENT_TYPES + EXPENSE_EVENT_TYPES + ("refund",)),
                # 信用卡还款本金不计支出（还款属内部转账）
                ~(BusinessEvent.event_type == "liability_repay") | card_filter
                | BusinessEvent.liability_id.is_(None),
                BusinessEvent.occurred_at >= start_utc,
                BusinessEvent.occurred_at < end_utc,
            )
        )
    ).all()

    series = []
    for idx, month_start in enumerate(starts):
        month_key = f"{month_start:%Y-%m}"
        income = expense = Decimal("0")
        for occurred_at, event_type, amount, _currency, _lid in rows:
            dt = occurred_at
            if dt is None:
                continue
            if dt.tzinfo is not None:
                dt = dt.astimezone(tz).replace(tzinfo=None)
            if f"{dt:%Y-%m}" != month_key:
                continue
            if event_type == "refund":
                expense -= _dec(amount)
            elif event_type in INCOME_EVENT_TYPES:
                income += _dec(amount)
            else:
                expense += _dec(amount)
        series.append({
            "month": month_key,
            "label": f"{month_start.month}月",
            "income": money(income),
            "expense": money(expense),
            "balance": money(income - expense),
        })
    return series


async def _asset_net_worth_parts(db: AsyncSession, household_id: str, base_currency: str) -> dict:
    """资产参与净资产的部分（M2 口径）+ 资产结构图数据。

    只纳入「计入净资产 且 未出售/未报废/未归档」的资产；多币种按币种分组。
    """
    from app.db.models.business import Asset
    from app.db.models.leases import Lease as _Lease, LeaseDeposit as _LeaseDeposit

    # 房产是否计入净资产由资产自身控制（不动产编辑页），租约不再参与；
    # 这里只处理「押金是否计入」——钱已在账户里（有 held 记录）则不重复
    leases = list((await db.execute(
        select(_Lease).where(
            _Lease.household_id == household_id,
            _Lease.status == "active",
            _Lease.deleted_at.is_(None),
        )
    )).scalars().all())
    deposit_leases = [
        l for l in leases
        if l.deposit_received and l.deposit_include_in_net_worth and _dec(l.deposit_amount) > 0
    ]
    depossited_ids: set[str] = set()
    if deposit_leases:
        depossited_ids = {
            row[0] for row in (await db.execute(
                select(_LeaseDeposit.lease_id).where(
                    _LeaseDeposit.lease_id.in_([l.id for l in deposit_leases]),
                    _LeaseDeposit.status == "held",
                    _LeaseDeposit.deleted_at.is_(None),
                )
            )).all()
        }

    assets = list(
        (await db.execute(
            select(Asset).where(
                Asset.household_id == household_id,
                Asset.deleted_at.is_(None),
                Asset.include_in_net_worth.is_(True),
            )
        )).scalars().all()
    )
    by_currency: dict[str, dict] = {}
    structure: dict[str, Decimal] = {}
    base_total = Decimal("0")
    included = 0
    for asset in assets:
        if asset.status in ("disposed", "scrapped", "archived"):
            continue
        included += 1
        value = _dec(asset.current_value)
        bucket = by_currency.setdefault(asset.currency, {"total": Decimal("0"), "count": 0})
        bucket["total"] += value
        bucket["count"] += 1
        if asset.currency == base_currency:
            base_total += value
            structure[asset.asset_type] = structure.get(asset.asset_type, Decimal("0")) + value

    # 押金计入：只有「勾选已收 + 勾选计入净资产 + 没有 held 押金记录」才加，
    # 有 held 记录说明钱已经在账户里，再加就重复了
    for lease in deposit_leases:
        if lease.id in depossited_ids:
            continue
        value = _dec(lease.deposit_amount)
        bucket = by_currency.setdefault(lease.currency, {"total": Decimal("0"), "count": 0})
        bucket["total"] += value
        bucket["count"] += 1
        if lease.currency == base_currency:
            base_total += value
            structure["lease_deposit"] = structure.get("lease_deposit", Decimal("0")) + value

    from app.core import enums as enum_zh

    structure_items = [
        {
            "asset_type": key,
            "asset_type_label": "租赁押金" if key == "lease_deposit" else enum_zh.zh("asset_type", key),
            "value": money(value),
            "currency": base_currency,
        }
        for key, value in sorted(structure.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {
        "base_total": base_total,
        "by_currency": [
            {"currency": cur, "total": money(b["total"]), "count": b["count"]}
            for cur, b in by_currency.items()
        ],
        "included": included,
        "structure": structure_items,
    }


async def _asset_value_at(
    db: AsyncSession, household_id: str, base_currency: str, month_end_utc: datetime
) -> Decimal:
    """某时点的资产价值（净资产趋势用）：按估值历史取值，估值缺失时用购置成本近似。"""
    month_end_utc = to_local_naive(month_end_utc) or month_end_utc
    from app.db.models.assets import AssetValuation
    from app.db.models.business import Asset

    # 房产是否计入净资产由资产自身控制（不动产编辑页），租约不再参与；
    # 押金（已收 + 计入净资产）在「当前净资产」口径里由 _asset_net_worth_parts 处理，
    # 这里的历史趋势只算资产估值，不重复算押金（钱已在账户余额里表达）。
    async def load_assets():
        return list((await db.execute(
            select(Asset).where(
                Asset.household_id == household_id,
                Asset.deleted_at.is_(None),
                Asset.include_in_net_worth.is_(True),
                Asset.currency == base_currency,
            )
        )).scalars().all())

    assets = await _cached_rows(db, ("trend.assets", household_id, base_currency), load_assets)
    if not assets:
        return Decimal("0")

    ids = [a.id for a in assets]

    async def load_valuations():
        return (await db.execute(
            select(AssetValuation.asset_id, AssetValuation.value, AssetValuation.valued_at)
            .where(AssetValuation.asset_id.in_(ids), AssetValuation.deleted_at.is_(None))
            .order_by(AssetValuation.valued_at.asc())
        )).all()

    async def load_disposals():
        return (await db.execute(
            select(BusinessEvent.asset_id, func.min(BusinessEvent.occurred_at))
            .where(
                BusinessEvent.household_id == household_id,
                BusinessEvent.event_type == "asset_dispose",
                BusinessEvent.status == "confirmed",
                BusinessEvent.asset_id.in_(ids),
                BusinessEvent.deleted_at.is_(None),
            )
            .group_by(BusinessEvent.asset_id)
        )).all()

    valuations = await _cached_rows(db, ("trend.asset_valuations", household_id, base_currency), load_valuations)
    disposals = await _cached_rows(db, ("trend.asset_disposals", household_id, base_currency), load_disposals)
    disposal_map: dict[str, datetime] = {}
    for asset_id, occurred_at in disposals:
        if occurred_at is not None:
            dt = occurred_at.replace(tzinfo=None) if occurred_at.tzinfo else occurred_at
            disposal_map[asset_id] = dt

    by_asset: dict[str, list[tuple[datetime, Decimal]]] = {}
    for asset_id, value, valued_at in valuations:
        if valued_at is None:
            continue
        dt = valued_at.replace(tzinfo=None) if valued_at.tzinfo else valued_at
        by_asset.setdefault(asset_id, []).append((dt, _dec(value)))

    total = Decimal("0")
    for asset in assets:
        acquire_dt = asset.acquire_date
        if acquire_dt is not None and acquire_dt.tzinfo is None:
            acquire_dt = acquire_dt.replace(tzinfo=None)
        disposal_dt = disposal_map.get(asset.id)
        if disposal_dt is not None and disposal_dt <= month_end_utc:
            continue
        if acquire_dt is not None and acquire_dt > month_end_utc:
            continue
        latest: Decimal | None = None
        for dt, value in by_asset.get(asset.id, []):
            if dt <= month_end_utc:
                latest = value
            else:
                break
        if latest is not None:
            total += latest
        elif _dec(asset.acquire_cost) > 0:
            # 尚无估值记录时用购置成本近似（趋势口径，见 caliber）
            total += _dec(asset.acquire_cost)
    return total


async def _liability_parts(db: AsyncSession, household_id: str, base_currency: str) -> dict:
    """负债参与净资产的部分（M3）：未结清负债的剩余本金，按币种分组。"""
    from app.db.models.finance import Liability
    from app.services.liabilities import outstanding_for

    liabilities = list((await db.execute(
        select(Liability).where(
            Liability.household_id == household_id, Liability.deleted_at.is_(None)
        )
    )).scalars().all())
    if not liabilities:
        return {"base_total": Decimal("0"), "by_currency": [], "by_type": [], "included": 0}

    outstanding = await outstanding_for(db, household_id, [l.id for l in liabilities])
    by_currency: dict[str, dict] = {}
    by_type: dict[str, Decimal] = {}
    base_total = Decimal("0")
    included = 0
    for liability in liabilities:
        if not liability.include_in_net_worth:
            continue  # 关闭后不计入家庭净值（如公司贷款）
        # 信用卡不是债务：它的欠款已经由账户余额（负值）表达，
        # 若这里再算一次会重复扣减净资产。
        if liability.liability_type == "credit_card":
            continue
        out = outstanding.get(liability.id, _liability_base(liability))
        if out <= 0:
            continue
        included += 1
        bucket = by_currency.setdefault(liability.currency, {"total": Decimal("0"), "count": 0})
        bucket["total"] += out
        bucket["count"] += 1
        if liability.currency == base_currency:
            base_total += out
            by_type[liability.liability_type] = by_type.get(liability.liability_type, Decimal("0")) + out

    from app.core import enums as enum_zh

    return {
        "base_total": base_total,
        "by_currency": [
            {"currency": cur, "total": money(b["total"]), "count": b["count"]}
            for cur, b in by_currency.items()
        ],
        "by_type": [
            {
                "liability_type": key,
                "liability_type_label": enum_zh.zh("liability_type", key),
                "outstanding": money(value),
                "currency": base_currency,
            }
            for key, value in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "included": included,
    }


async def _investment_parts(db: AsyncSession, household_id: str, base_currency: str) -> dict:
    """投资总额（持仓市值 + 账户现金）参与净资产的部分（受「投资估值纳入净资产」开关控制）。"""
    from app.db.models.finance import InvestmentAccount
    from app.services.investments import portfolio_of

    accounts = list((await db.execute(
        select(InvestmentAccount).where(
            InvestmentAccount.household_id == household_id,
            InvestmentAccount.deleted_at.is_(None),
        )
    )).scalars().all())
    by_currency: dict[str, dict] = {}
    base_total = Decimal("0")
    included = 0
    for account in accounts:
        if not account.include_in_net_worth:
            continue  # 关闭后该账户市值不计入家庭净值
        portfolio = await portfolio_of(db, account)
        included += 1
        bucket = by_currency.setdefault(account.currency, {
            "market_value": Decimal("0"), "cash": Decimal("0"), "count": 0,
        })
        bucket["market_value"] += portfolio["market_value"]
        bucket["cash"] += portfolio["cash_balance"]
        bucket["count"] += 1
        # 计入净资产的是账户总值：持仓市值 + 账户内现金（银行理财的本金就在现金里）
        if account.currency == base_currency:
            base_total += portfolio["total_value"]
    return {
        "base_total": base_total,
        "by_currency": [
            {
                "currency": cur, "market_value": money(b["market_value"]),
                "cash": money(b["cash"]),
                "total_value": money(b["market_value"] + b["cash"]),
                "count": b["count"],
            }
            for cur, b in by_currency.items()
        ],
        "included": included,
    }


async def _liability_value_at(
    db: AsyncSession, household_id: str, base_currency: str, month_end_utc: datetime
) -> Decimal:
    """某时点的负债剩余本金（净资产趋势用）：初始金额 + 截至该时点的已确认变动。"""
    month_end_utc = to_local_naive(month_end_utc) or month_end_utc
    from app.db.models.finance import Liability

    async def load_liabilities():
        return list((await db.execute(
            select(Liability).where(
                Liability.household_id == household_id,
                Liability.deleted_at.is_(None),
                Liability.currency == base_currency,
            )
        )).scalars().all())

    liabilities = await _cached_rows(db, ("trend.liabilities", household_id, base_currency), load_liabilities)
    if not liabilities:
        return Decimal("0")
    ids = [l.id for l in liabilities]

    async def load_movements():
        return (await db.execute(
            select(EventMovement.target_id, EventMovement.direction, EventMovement.amount,
                   BusinessEvent.occurred_at, BusinessEvent.created_at)
            .join(BusinessEvent, BusinessEvent.id == EventMovement.event_id)
            .where(
                EventMovement.household_id == household_id,
                EventMovement.target_type == "liability",
                EventMovement.target_id.in_(ids),
                BusinessEvent.status == "confirmed",
                BusinessEvent.deleted_at.is_(None),
            )
        )).all()

    rows = await _cached_rows(db, ("trend.liability_movements", household_id, base_currency), load_movements)
    total = Decimal("0")
    for liability in liabilities:
        balance = _liability_base(liability)
        for target_id, direction, amount, occurred_at, created_at in rows:
            if target_id != liability.id:
                continue
            dt = occurred_at or created_at
            if dt is None:
                continue
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            if dt > month_end_utc:
                continue
            balance += _dec(amount) if direction == "in" else -_dec(amount)
        if balance > 0:
            total += balance
    return total


async def _investment_value_at(
    db: AsyncSession, household_id: str, base_currency: str, month_end_utc: datetime
) -> Decimal:
    """某时点的投资总额（净资产趋势用）：现金 + 持仓数量 × 该时点最新估值价（回退成交价/成本价）。"""
    month_end_utc = to_local_naive(month_end_utc) or month_end_utc
    from app.db.models.finance import InvestmentAccount, InvestmentTrade, InvestmentValuation

    async def load_invest_accounts():
        return list((await db.execute(
            select(InvestmentAccount).where(
                InvestmentAccount.household_id == household_id,
                InvestmentAccount.deleted_at.is_(None),
                InvestmentAccount.currency == base_currency,
            )
        )).scalars().all())

    accounts = await _cached_rows(db, ("trend.invest_accounts", household_id, base_currency), load_invest_accounts)
    if not accounts:
        return Decimal("0")
    ids = [a.id for a in accounts]

    async def load_trades():
        return list((await db.execute(
            select(InvestmentTrade).where(
                InvestmentTrade.account_id.in_(ids),
                InvestmentTrade.deleted_at.is_(None),
                InvestmentTrade.status == "confirmed",
            )
        )).scalars().all())

    async def load_invest_valuations():
        return list((await db.execute(
            select(InvestmentValuation).where(
                InvestmentValuation.account_id.in_(ids), InvestmentValuation.deleted_at.is_(None)
            )
        )).scalars().all())

    trades = await _cached_rows(db, ("trend.invest_trades", household_id, base_currency), load_trades)
    valuations = await _cached_rows(db, ("trend.invest_valuations", household_id, base_currency), load_invest_valuations)

    total = Decimal("0")
    for account in accounts:
        cash = _dec(account.opening_cash)
        positions: dict[str, dict] = {}
        last_trade_price: dict[str, Decimal] = {}
        for trade in trades:
            if trade.account_id != account.id:
                continue
            dt = trade.traded_at or trade.created_at
            if dt is None:
                continue
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            if dt > month_end_utc:
                continue
            amount = _dec(trade.amount)
            fee = _dec(trade.fee)
            if trade.trade_type == "buy":
                cash -= amount + fee
            elif trade.trade_type == "sell":
                cash += amount - fee
            elif trade.trade_type == "dividend":
                cash += amount
            else:
                cash -= amount + fee
            if trade.symbol:
                bucket = positions.setdefault(trade.symbol, {"qty": Decimal("0"), "cost": Decimal("0")})
                if trade.trade_type == "buy":
                    bucket["qty"] += _dec(trade.quantity)
                    bucket["cost"] += amount + fee
                elif trade.trade_type == "sell":
                    bucket["qty"] -= _dec(trade.quantity)
                if trade.price is not None:
                    last_trade_price[trade.symbol] = _dec(trade.price)

        price_map: dict[str, Decimal] = {}
        for valuation in valuations:
            if valuation.account_id != account.id:
                continue
            dt = valuation.valued_at or valuation.created_at
            if dt is None:
                continue
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            if dt <= month_end_utc:
                price_map[valuation.symbol] = _dec(valuation.price)

        market = Decimal("0")
        for symbol, bucket in positions.items():
            qty = bucket["qty"]
            if qty <= 0:
                continue
            price = price_map.get(symbol) or last_trade_price.get(symbol)
            if price is None:
                avg = bucket["cost"] / qty if qty > 0 else Decimal("0")
                price = avg
            market += qty * price
        # 与当期口径一致：现金为负（还没登记入金）时按 0 计，不让趋势被负现金拉下去
        total += (cash if cash > Decimal("0") else Decimal("0")) + market
    return total


async def _net_worth_trend(
    db: AsyncSession, household_id: str, months: int, tz_name: str, base_currency: str = "CNY"
) -> list[dict]:
    """近 N 个月月末账户余额趋势（仅计入净资产的账户、基础币种）。

    注意：这里只算账户余额，不含资产、负债与投资的历史值（缺少历史估值无法还原）；
    完整净资产以当前时点的 net_worth 为准，UI 上标题也据此标注为「账户余额趋势」。
    """
    tz = resolve_tz(tz_name)

    accounts = list(
        (await db.execute(
            select(Account).where(
                Account.household_id == household_id,
                Account.deleted_at.is_(None),
                Account.include_in_net_worth.is_(True),
                Account.currency == base_currency,
            )
        )).scalars().all()
    )
    by_id = {a.id: a for a in accounts}

    movements = (
        await db.execute(
            select(EventMovement.target_id, EventMovement.direction, EventMovement.amount,
                   BusinessEvent.occurred_at)
            .join(BusinessEvent, BusinessEvent.id == EventMovement.event_id)
            .where(
                EventMovement.household_id == household_id,
                EventMovement.target_type == "account",
                EventMovement.target_id.in_(list(by_id.keys()) or [""]),
                BusinessEvent.status == "confirmed",
                BusinessEvent.deleted_at.is_(None),
            )
        )
    ).all()

    # 先把每个账户的资金变动按时间排好并做前缀和，再按月末二分查余额。
    # 原来是「12 个月 × 每个账户 × 每笔流水」三重循环（8 账户 × 1400 笔 ≈ 13 万次/次调用），
    # 单这一项就占了快照 40% 的耗时。
    from bisect import bisect_left

    per_account: dict[str, list[tuple[datetime, Decimal]]] = {}
    for target_id, direction, amount, occurred_at in movements:
        if occurred_at is None:
            continue
        dt = occurred_at.replace(tzinfo=None) if occurred_at.tzinfo else occurred_at
        delta = _dec(amount) if direction == "in" else -_dec(amount)
        per_account.setdefault(target_id, []).append((dt, delta))

    series_index: dict[str, tuple[list[datetime], list[Decimal]]] = {}
    for acc_id, rows in per_account.items():
        rows.sort(key=lambda item: item[0])
        times: list[datetime] = []
        prefix: list[Decimal] = []
        running = Decimal("0")
        for dt, delta in rows:
            times.append(dt)
            running += delta
            prefix.append(running)
        series_index[acc_id] = (times, prefix)

    now_local = datetime.now(tz)
    first_this = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year, month = first_this.year, first_this.month
    month -= months - 1
    while month <= 0:
        month += 12
        year -= 1
    cursor = first_this.replace(year=year, month=month, day=1)

    series = []
    for _ in range(months):
        month_end_local = (cursor + timedelta(days=32)).replace(day=1)
        month_end_utc = month_end_local.replace(tzinfo=None)
        total = Decimal("0")
        for acc in accounts:
            bal = _dec(acc.opening_balance)
            times, prefix = series_index.get(acc.id, ((), ()))
            if times:
                # 只累加「发生在月末之前」的变动：bisect_left 正好给出这个数量
                count = bisect_left(times, month_end_utc)
                if count:
                    bal += prefix[count - 1]
            total += bal
        total += await _asset_value_at(db, household_id, base_currency, month_end_utc)
        total += await _investment_value_at(db, household_id, base_currency, month_end_utc)
        total -= await _liability_value_at(db, household_id, base_currency, month_end_utc)
        series.append({
            "month": f"{cursor:%Y-%m}",
            "label": f"{cursor.month}月",
            "value": money(total),
        })
        cursor = month_end_local
    return series


async def _liability_trend(
    db: AsyncSession, household_id: str, months: int, tz_name: str, base_currency: str
) -> list[dict]:
    """近 N 个月月末负债剩余本金（07 §5 负债余额趋势）。"""
    tz = resolve_tz(tz_name)
    now_local = datetime.now(tz)
    first_this = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year, month = first_this.year, first_this.month
    month -= months - 1
    while month <= 0:
        month += 12
        year -= 1
    cursor = first_this.replace(year=year, month=month, day=1)

    series: list[dict] = []
    for _ in range(months):
        month_end_local = (cursor + timedelta(days=32)).replace(day=1)
        month_end_utc = month_end_local.replace(tzinfo=None)
        value = await _liability_value_at(db, household_id, base_currency, month_end_utc)
        series.append({
            "month": f"{cursor:%Y-%m}",
            "label": f"{cursor.month}月",
            "value": money(value),
        })
        cursor = month_end_local
    return series


async def _counts(db: AsyncSession, household_id: str) -> dict[str, int]:
    result = await db.execute(
        select(BusinessEvent.status, func.count(BusinessEvent.id))
        .where(BusinessEvent.household_id == household_id, BusinessEvent.deleted_at.is_(None))
        .group_by(BusinessEvent.status)
    )
    by_status = {row[0]: int(row[1]) for row in result.all()}
    return {
        "confirmed": by_status.get("confirmed", 0),
        "draft": by_status.get("draft", 0),
        "excluded_draft": by_status.get("draft", 0),
        "total_records": sum(by_status.values()),
    }


async def account_summary(db: AsyncSession, household_id: str, base_currency: str = "CNY") -> dict:
    """账户汇总：按币种（不相加）+ 按账户类型（数量与合计）。

    by_type 只把基础币种余额累加进 total（外币单独放在 other_currency_count 里），
    避免不同币种直接相加造成误读。
    """
    from app.services.accounts import balances_for, household_accounts

    accounts = await household_accounts(db, household_id)
    balances = await balances_for(db, household_id, [a.id for a in accounts])

    grouped: "OrderedDict[str, dict]" = OrderedDict()
    by_type: "OrderedDict[str, dict]" = OrderedDict()
    for acc in accounts:
        bal = balances.get(acc.id, Decimal("0"))
        bucket = grouped.setdefault(acc.currency, {
            "currency": acc.currency, "total": Decimal("0"), "count": 0,
            "in_net_worth": Decimal("0"), "disabled_count": 0,
        })
        bucket["count"] += 1
        if acc.status != "active":
            bucket["disabled_count"] += 1
        bucket["total"] += bal
        if acc.include_in_net_worth and acc.status == "active":
            bucket["in_net_worth"] += bal

        type_bucket = by_type.setdefault(acc.account_type, {
            "account_type": acc.account_type,
            "label": enum_zh.zh("account_type", acc.account_type),
            "count": 0, "total": Decimal("0"), "other_currency_count": 0,
        })
        type_bucket["count"] += 1
        if acc.currency == base_currency:
            type_bucket["total"] += bal
        else:
            type_bucket["other_currency_count"] += 1

    items = [
        {
            "currency": b["currency"],
            "total": money(b["total"]),
            "in_net_worth": money(b["in_net_worth"]),
            "count": b["count"],
            "disabled_count": b["disabled_count"],
        }
        for b in grouped.values()
    ]
    type_items = [
        {
            "account_type": b["account_type"],
            "label": b["label"],
            "count": b["count"],
            "total": money(b["total"]),
            "other_currency_count": b["other_currency_count"],
        }
        for b in by_type.values()
    ]
    base_total = next((i["total"] for i in items if i["currency"] == base_currency), money(Decimal("0")))
    return {
        "count": len(accounts),
        "by_currency": items,
        "by_type": type_items,
        "base_currency": base_currency,
        "base_total": base_total,
    }


async def _liquidity_parts(db: AsyncSession, household_id: str, base_currency: str) -> dict:
    """流动资金构成（口径见 CALIBER["liquidity"]）。

    关键区别（用户明确要求）：
    - cash 只累计**能马上动用**的现金类账户：启用、纳入净资产、无授信额度（即非信用卡/花呗/借呗等）；
    - credit_due 把信用类账户的欠款单列为「待还」，**不冲减现金**。
      以前把信用卡负余额直接算进账户现金，等于提前扣掉待还金额，会让流动资金虚低；
      现在流动资金反映手头现金，能不能还上由 debt_service 的覆盖倍数回答。
    """
    from app.services.accounts import balances_for, household_accounts

    # 与账户汇总共用同一次读取：停用账户在循环里跳过即可
    accounts = await household_accounts(db, household_id)
    balances = await balances_for(db, household_id, [a.id for a in accounts])

    cash = Decimal("0")
    credit_due = Decimal("0")
    credit_by_account: dict[str, Decimal] = {}
    for acc in accounts:
        if acc.status != "active":
            continue
        if acc.currency != base_currency:
            continue
        bal = balances.get(acc.id, Decimal("0"))
        if acc.credit_limit is not None:
            # 信用类账户：余额为负表示欠款，为正表示溢缴款（溢缴可算现金）
            if bal < 0:
                credit_due += -bal
                credit_by_account[acc.id] = -bal
            else:
                cash += bal
            continue
        if not acc.include_in_net_worth:
            continue
        cash += bal
    return {"cash": cash, "credit_due": credit_due, "credit_by_account": credit_by_account}


async def dashboard_snapshot(
    db: AsyncSession,
    user: CurrentUser,
    period: str = "current_month",
    currency: str | None = None,
) -> dict:
    from app.services.setup import current_household

    household = await current_household(db)
    tz_name = household.timezone if household else "Asia/Shanghai"
    base_currency = currency or (household.base_currency if household else "CNY")
    household_id = user.household_id or ""

    start, end, range_label = period_range(period, tz_name)
    counts = await _counts(db, household_id)
    totals = await _event_totals(db, household_id, start, end)
    accounts = await account_summary(db, household_id, base_currency)

    # 同币种汇总；多币种只分组不相加
    currencies = sorted(set(totals.keys()) | {base_currency})
    multi_currency = len(accounts["by_currency"]) > 1

    def pick(field: str) -> Decimal:
        total = Decimal("0")
        for cur, bucket in totals.items():
            if cur == base_currency:
                total += bucket[field]
        if base_currency not in totals and not totals:
            return Decimal("0")
        return total

    income = pick("income")
    expense = pick("expense")

    # 净资产（M3 口径：账户余额 + 资产当前价值 + 投资市值 − 负债剩余本金）
    accounts_in_net = Decimal("0")
    for item in accounts["by_currency"]:
        if item["currency"] == base_currency:
            accounts_in_net += _dec(item["in_net_worth"])

    from app.services import assets as asset_service
    from app.services import investments as investment_service
    from app.services import leases as lease_service
    from app.services import liabilities as liability_service

    asset_parts = await _asset_net_worth_parts(db, household_id, base_currency)
    assets_in_net = asset_parts["base_total"]
    asset_summary = await asset_service.asset_summary(db, household_id, base_currency)

    liability_parts = await _liability_parts(db, household_id, base_currency)
    liability_summary = await liability_service.liability_summary(db, household_id, base_currency)
    investment_parts = await _investment_parts(db, household_id, base_currency)
    investment_summary = await investment_service.portfolio_summary(db, household_id, base_currency)
    lease_summary = await lease_service.lease_summary(db, household_id, base_currency)

    from app.services import reminders as reminder_service
    reminder_total = (await reminder_service.list_reminders(db, user))["total"]

    # 「投资估值纳入净资产」开关（08 §4 财务口径）
    investment_in_networth = True
    if household is not None:
        setting = (await db.execute(
            select(HouseholdSetting).where(HouseholdSetting.household_id == household.id)
        )).scalars().first()
        if setting is not None:
            investment_in_networth = bool(setting.investment_valuation_in_net_worth)
    investments_in_net = investment_parts["base_total"] if investment_in_networth else Decimal("0")

    total_assets_value = accounts_in_net + assets_in_net + investments_in_net
    total_liabilities = liability_parts["base_total"]
    net_worth_value = total_assets_value - total_liabilities

    # 流动资金（可动用现金）：现金类账户余额 + 投资账户现金（货币基金 / 银行活期理财本金）。
    # 信用卡等信用类账户的欠款不进这里，单列为「信用卡待还」，由偿债覆盖判断能否还上。
    liq_parts = await _liquidity_parts(db, household_id, base_currency)
    cash_accounts = liq_parts["cash"]
    credit_card_outstanding = liq_parts["credit_due"]
    invest_cash = _dec(investment_summary.get("cash", 0))
    liquidity_value = cash_accounts + invest_cash

    # 信用卡「本期应还」：优先用账单的未还金额（真正到期要还的部分），
    # 没有建过账单的卡退回欠款总额估算（否则覆盖倍数会虚高）。
    from app.services import card_bills as card_bill_service

    bill_parts = await card_bill_service.unpaid_parts(db, household_id)
    credit_billed_due = Decimal("0")
    credit_unbilled = Decimal("0")
    billed_cards = 0
    estimated_cards = 0
    for acc_id, outstanding in liq_parts["credit_by_account"].items():
        info = bill_parts.get(acc_id)
        if info and info["has_bill"]:
            billed = min(info["unpaid"], outstanding) if outstanding > 0 else info["unpaid"]
            credit_billed_due += billed
            credit_unbilled += max(Decimal("0"), outstanding - info["unpaid"])
            billed_cards += 1
        else:
            credit_billed_due += outstanding
            estimated_cards += 1

    # 偿债覆盖：流动资金 ÷ 待还合计（信用卡本期应还 + 未来 30 天负债应还）。
    # 流动资金本身不扣待还，两者相除才知道「手头的钱够不够还」。
    liability_monthly_due = _dec(liability_summary.get("monthly_due", 0))
    repay_due_total = credit_billed_due + liability_monthly_due
    if repay_due_total > 0:
        coverage_value = liquidity_value - repay_due_total
        coverage_ratio = liquidity_value / repay_due_total
        if coverage_ratio >= Decimal("1.5"):
            coverage_level = "ok"
        elif coverage_ratio >= Decimal("1"):
            coverage_level = "warn"
        else:
            coverage_level = "bad"
    else:
        coverage_value = liquidity_value
        coverage_ratio = None
        coverage_level = "none"

    # 资产负债率（负债 / 总资产）：≤40% 健康、40–60% 关注、>60% 偏高
    if total_assets_value > 0:
        debt_ratio = total_liabilities / total_assets_value * 100
    else:
        debt_ratio = Decimal("0")
    debt_ratio_level = (
        "ok" if debt_ratio <= 40 else ("warn" if debt_ratio <= 60 else "bad")
    )

    # 上一期间同比
    prev_income = prev_expense = Decimal("0")
    if period != "all" and start is not None:
        span = end - start
        prev_start, prev_end = start - span, start
        prev_totals = await _event_totals(db, household_id, prev_start, prev_end)
        prev_income = _dec(prev_totals.get(base_currency, {}).get("income", 0))
        prev_expense = _dec(prev_totals.get(base_currency, {}).get("expense", 0))

    monthly = await _monthly_series(db, household_id, 6, tz_name)
    trend = await _net_worth_trend(db, household_id, 6, tz_name, base_currency)
    liability_trend = await _liability_trend(db, household_id, 6, tz_name, base_currency)

    # 生存跑道（应急储备月数）= 流动资金 ÷ 近 3 个月月均支出。
    # 含义：如果收入中断，现有可动用资金能维持家庭运转几个月。
    # 基准参照：≥6 个月健康，3–6 偏紧，<3 不足（有小孩家庭建议 8 个月）。
    expense_samples = [_dec(m["expense"]) for m in monthly[-3:]]
    avg_monthly_expense = (
        sum(expense_samples) / Decimal(len(expense_samples)) if expense_samples else Decimal("0")
    )
    runway_months: Decimal | None = None
    if avg_monthly_expense > 0 and liquidity_value > 0:
        runway_months = liquidity_value / avg_monthly_expense
    # 动态跑道：把投资收益与通胀都算进去的逐月模拟
    runway_dynamic = _dynamic_runway(liquidity_value, avg_monthly_expense)
    if runway_months is None:
        runway_level = "unknown"
    elif runway_months >= 6:
        runway_level = "ok"
    elif runway_months >= 3:
        runway_level = "warn"
    else:
        runway_level = "bad"

    recent_events = list(
        (await db.execute(
            select(BusinessEvent)
            .where(BusinessEvent.household_id == household_id, BusinessEvent.deleted_at.is_(None))
            .order_by(BusinessEvent.created_at.desc()).limit(8)
        )).scalars().all()
    )

    return {
        "period": period,
        "period_label": PERIODS.get(period, period),
        "period_range": range_label,
        "base_currency": base_currency,
        "currency_badge": "多币种" if multi_currency else base_currency,
        "multi_currency": multi_currency,
        "caliber": CALIBER,
        "counts": counts,
        "net_worth": {
            "accounts": money(accounts_in_net),
            "assets": money(assets_in_net),
            "investments": money(investments_in_net),
            "investment_included": investment_in_networth,
            "total_assets": money(total_assets_value),
            "total_liabilities": money(total_liabilities),
            "net_worth": money(net_worth_value),
            "change_since_previous": money((income - expense) - (prev_income - prev_expense)),
            "debt_ratio": f"{debt_ratio:.1f}",
            "debt_ratio_level": debt_ratio_level,
            "debt_ratio_note": "资产负债率 = 负债剩余本金 ÷ 总资产；≤40% 健康、40–60% 需关注、>60% 偏高",
            "included_accounts": accounts["count"],
            "included_assets": asset_parts["included"],
            "included_liabilities": liability_parts["included"],
            "included_investments": investment_parts["included"],
            "by_currency": accounts["by_currency"],
            "assets_by_currency": asset_parts["by_currency"],
            "liabilities_by_currency": liability_parts["by_currency"],
            "investments_by_currency": investment_parts["by_currency"],
            "note": (
                "净资产 = 账户余额 + 资产当前价值 + 投资总额（持仓市值 + 账户现金） − 负债剩余本金（仅基础币种展示合计）；"
                "各对象可单独关闭「纳入净资产」"
            ),
        },
        "metrics": {
            "income": money(income),
            "expense": money(expense),
            "balance": money(income - expense),
            "pending_reminders": reminder_total,
        },
        "liquidity": {
            "value": money(liquidity_value),
            "cash_accounts": money(cash_accounts),
            # 兼容旧字段名：语义已改为「现金类账户」，不再含信用卡负余额
            "account_cash": money(cash_accounts),
            "invest_cash": money(invest_cash),
            "runway_months": f"{runway_months:.1f}" if runway_months is not None else None,
            "runway_months_dynamic": runway_dynamic,
            "runway_assumptions": {
                "invest_return": f"{RUNWAY_INVEST_RETURN * 100:.1f}%",
                "inflation": f"{RUNWAY_INFLATION * 100:.1f}%",
                "max_months": RUNWAY_MAX_MONTHS,
            },
            "monthly_expense": money(avg_monthly_expense),
            "runway_level": runway_level,
            "runway_note": (
                "生存跑道 = 流动资金 ÷ 近 3 个月月均支出；≥6 个月健康、3–6 个月偏紧、<3 个月不足"
                "（有小孩的家庭建议留 8 个月）。动态值按 2.5% 年化收益 + 3.5% 年通胀逐月模拟"
            ),
            "note": CALIBER["liquidity"],
            "caliber": CALIBER["liquidity"],
        },
        # 偿债能力：流动资金不扣待还，另用覆盖倍数回答「够不够还」
        "debt_service": {
            "credit_card_due": money(credit_billed_due),
            "credit_card_outstanding": money(credit_card_outstanding),
            "credit_card_unbilled": money(credit_unbilled),
            "credit_card_billed_count": billed_cards,
            "credit_card_estimated_count": estimated_cards,
            "liability_due_30d": money(liability_monthly_due),
            "total_due": money(repay_due_total),
            "coverage_value": money(coverage_value),
            "coverage_ratio": f"{coverage_ratio:.2f}" if coverage_ratio is not None else None,
            "coverage_level": coverage_level,
            "note": CALIBER["debt_service"],
            "caliber": CALIBER["debt_service"],
        },
        "charts": {
            "net_worth_trend": trend,
            "asset_structure": asset_parts["structure"],
            "income_expense": monthly,
            "liability_trend": liability_trend,
            "liability_structure": liability_parts["by_type"],
            "investment_structure": investment_summary["structure"],
        },
        "accounts": accounts,
        "assets": asset_summary,
        "liabilities": liability_summary,
        "investments": investment_summary,
        "summaries": {"liabilities": liability_summary, "investments": investment_summary,
                      "leases": lease_summary},
        "pending": {
            "ai_suggestions": 0,  # AI 建议随 M7 交付
            "unreconciled_accounts": len([
                a for a in (await db.execute(
                    select(Account).where(
                        Account.household_id == household_id,
                        Account.deleted_at.is_(None),
                        Account.status == "active",
                    )
                )).scalars().all() if a.last_reconciled_at is None
            ]),
            "upcoming_bills": len(liability_summary.get("upcoming", [])),
            "overdue_rents": lease_summary.get("overdue_count", 0),
            "reminders": reminder_total,
        },
        "timeline": [
            {
                "id": e.id,
                "event_no": e.event_no,
                "event_type": e.event_type,
                "title": e.title,
                "amount": money(_dec(e.amount)),
                "currency": e.currency,
                "status": e.status,
                "occurred_at": local_iso(e.occurred_at) if e.occurred_at else None,
            }
            for e in recent_events
        ],
        "data_quality": {
            "critical": 0, "warning": 0, "info": 0,
            "note": "数据质量中心在脉诊模块交付后提供完整规则",
        },
        "data_updated_at": utc_iso(datetime.now(timezone.utc)),
        "is_empty": (
            counts["total_records"] == 0
            and accounts["count"] == 0
            and asset_summary["count"] == 0
            and liability_summary["count"] == 0
            and investment_summary["count"] == 0
        ),
        "is_partial": counts["draft"] > 0,
    }
