"""投资领域服务（06 §8、07 §6、18 §4 M3）。

口径（后端确定性计算，前端不自行计算）：
- 投资账户余额（账户内可用资金） = 初始可用资金 − Σ(买入金额 + 费用) + Σ(卖出金额 − 费用)
  + Σ(分红) − Σ(费用单)；
- 持仓按**加权平均成本**：成本价 = Σ(买入金额+费用) / Σ(买入数量)；
  剩余持仓成本 = 剩余数量 × 成本价；
- 已实现损益 = Σ(卖出净额 − 卖出数量 × 成本价) + Σ(分红)；未实现损益 = 持仓市值 − 剩余持仓成本；
- 当前价来自**手工估值**（investment_valuations 最新一条）；无估值时按成本展示并明确提示；
- 交易中的买入/卖出生成统一业务事件（investment_buy / investment_sell），
  分红与费用作为投资账户内部记录并写入审计；AI 不做交易下单与自动估值。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.money import fmt_amount
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.finance import InvestmentAccount, InvestmentTrade, InvestmentValuation
from app.services import audit as audit_service
from app.services import quotes as quote_service

ACCOUNT_EDITABLE = (
    "name", "account_type", "category_id", "institution", "currency", "opening_cash",
    "linked_account_id", "member_id", "note", "include_in_net_worth",
)

CALIBER = {
    "cash_balance": "账户余额 = 初始可用资金 − Σ(买入金额+费用) + Σ(卖出金额−费用) + Σ(分红) − Σ(费用)",
    "position_cost": "持仓成本按加权平均法：成本价 = Σ(买入金额+费用) / Σ(买入数量)",
    "realized": "已实现损益 = Σ(卖出净额 − 卖出数量 × 成本价) + Σ(分红) − Σ(费用)",
    "unrealized": "未实现损益 = 持仓市值 − 剩余持仓成本；当前价来自手工估值",
    "market_value": "持仓市值 = 剩余数量 × 最新手工估值价；无估值时按成本展示并标注「仅基于手工估值」",
    "net_worth": "投资总额（持仓市值 + 账户现金）是否计入净资产由「财务口径」中的开关决定（默认计入）",
    "events": "买入/卖出生成统一业务事件；分红与费用为投资账户内部记录并写入审计，不虚构事件类型",
}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _q4(value) -> Decimal:
    return _dec(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _q6(value) -> Decimal:
    return _dec(value).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def money(value) -> str:
    """金额对外统一输出（默认两位小数）。"""
    return fmt_amount(_dec(value))


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


def serialize_trade(trade: InvestmentTrade) -> dict:
    return {
        "id": trade.id,
        "account_id": trade.account_id,
        "trade_type": trade.trade_type,
        "trade_type_label": enum_zh.zh("investment_trade_type", trade.trade_type),
        "symbol": trade.symbol,
        "symbol_name": trade.symbol_name,
        "quantity": f"{_dec(trade.quantity):.6f}" if trade.quantity is not None else None,
        "price": f"{_dec(trade.price):.6f}" if trade.price is not None else None,
        "amount": money(trade.amount),
        "fee": money(trade.fee),
        "net_amount": money(_net_amount(trade)),
        "currency": trade.currency,
        "traded_at": local_iso(trade.traded_at) if trade.traded_at else None,
        "status": trade.status,
        "event_id": trade.event_id,
        "member_id": trade.member_id,
        "note": trade.note,
        "created_at": utc_iso(trade.created_at),
    }


def _net_amount(trade: InvestmentTrade) -> Decimal:
    """交易对账户资金的净影响：买入/费用为流出（负），卖出/分红为流入（正）。"""
    amount = _dec(trade.amount)
    fee = _dec(trade.fee)
    if trade.trade_type == "buy":
        return -(amount + fee)
    if trade.trade_type == "sell":
        return amount - fee
    if trade.trade_type == "dividend":
        return amount
    return -(amount + fee)


def serialize_valuation(row: InvestmentValuation) -> dict:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "symbol": row.symbol,
        "symbol_name": row.symbol_name,
        "price": f"{_dec(row.price):.6f}",
        "currency": row.currency,
        "valued_at": local_iso(row.valued_at) if row.valued_at else None,
        "source": row.source,
        "source_label": enum_zh.zh("valuation_source", row.source),
        "note": row.note,
        "created_at": utc_iso(row.created_at),
    }


def _invalidate_portfolio(db: AsyncSession) -> None:
    """写操作后清掉组合缓存：同一个请求里「写完再回显最新组合」是常见路径，
    不清就会返回写之前的市值/持仓。"""
    from app.core.agg_cache import invalidate
    invalidate(db, "portfolio")


async def get_account(db: AsyncSession, user: CurrentUser, account_id: str) -> InvestmentAccount:
    account = (
        await db.execute(select(InvestmentAccount).where(InvestmentAccount.id == account_id))
    ).scalars().first()
    if account is None or account.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "投资账户不存在", status_code=404)
    if account.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return account


async def cash_balance_for(
    db: AsyncSession, household_id: str, account_ids: list[str] | None = None
) -> dict[str, Decimal]:
    """投资账户余额 = 初始可用资金 + Σ交易净影响。"""
    conditions = [
        InvestmentAccount.household_id == household_id,
        InvestmentAccount.deleted_at.is_(None),
    ]
    if account_ids:
        conditions.append(InvestmentAccount.id.in_(account_ids))
    accounts = list((await db.execute(select(InvestmentAccount).where(*conditions))).scalars().all())
    if not accounts:
        return {}
    ids = [a.id for a in accounts]
    trades = list(
        (await db.execute(
            select(InvestmentTrade).where(
                InvestmentTrade.account_id.in_(ids),
                InvestmentTrade.deleted_at.is_(None),
                InvestmentTrade.status == "confirmed",
            )
        )).scalars().all()
    )
    net: dict[str, Decimal] = {}
    for trade in trades:
        net[trade.account_id] = net.get(trade.account_id, Decimal("0")) + _net_amount(trade)
    return {a.id: _q4(_dec(a.opening_cash) + net.get(a.id, Decimal("0"))) for a in accounts}


async def trades_and_valuations(
    db: AsyncSession, account: InvestmentAccount
) -> tuple[list[InvestmentTrade], list[InvestmentValuation]]:
    """一次读出账户的交易与估值。

    持仓、现金、已实现损益、年化收益都要用这两张表，之前各查一遍
    （一个账户要被读 4 次交易）。读一次往下传，谁都不再单独查库。
    """
    trades = list(
        (await db.execute(
            select(InvestmentTrade)
            .where(
                InvestmentTrade.account_id == account.id,
                InvestmentTrade.deleted_at.is_(None),
                InvestmentTrade.status == "confirmed",
            )
            .order_by(InvestmentTrade.traded_at.asc(), InvestmentTrade.created_at.asc())
        )).scalars().all()
    )
    valuations = list(
        (await db.execute(
            select(InvestmentValuation)
            .where(InvestmentValuation.account_id == account.id,
                   InvestmentValuation.deleted_at.is_(None))
            .order_by(InvestmentValuation.valued_at.asc(), InvestmentValuation.created_at.asc())
        )).scalars().all()
    )
    return trades, valuations


async def positions_for(db: AsyncSession, account: InvestmentAccount) -> list[dict]:
    """持仓（加权平均成本法）：标的、数量、成本价、当前价、市值、已实现/未实现损益、占比。"""
    trades, valuations = await trades_and_valuations(db, account)
    return positions_from(account, trades, valuations)


def positions_from(
    account: InvestmentAccount,
    trades: list[InvestmentTrade],
    valuations: list[InvestmentValuation],
) -> list[dict]:
    """由已读出的交易与估值算出持仓（纯计算，不访问数据库）。"""
    latest_price: dict[str, tuple[Decimal, InvestmentValuation]] = {}
    for v in valuations:
        latest_price[v.symbol] = (_dec(v.price), v)

    by_symbol: dict[str, dict] = {}
    account_dividend = Decimal("0")
    account_fee = Decimal("0")
    for trade in trades:
        if not trade.symbol:
            if trade.trade_type == "dividend":
                account_dividend += _dec(trade.amount)
            elif trade.trade_type == "fee":
                account_fee += _dec(trade.amount) + _dec(trade.fee)
            continue
        bucket = by_symbol.setdefault(trade.symbol, {
            "symbol": trade.symbol,
            "symbol_name": trade.symbol_name,
            "buy_qty": Decimal("0"), "buy_cost": Decimal("0"),
            "sell_qty": Decimal("0"), "sell_net": Decimal("0"),
            "dividend": Decimal("0"), "fee": Decimal("0"),
        })
        if trade.symbol_name:
            bucket["symbol_name"] = trade.symbol_name
        if trade.trade_type == "buy":
            bucket["buy_qty"] += _dec(trade.quantity)
            bucket["buy_cost"] += _dec(trade.amount) + _dec(trade.fee)
        elif trade.trade_type == "sell":
            bucket["sell_qty"] += _dec(trade.quantity)
            bucket["sell_net"] += _dec(trade.amount) - _dec(trade.fee)
        elif trade.trade_type == "dividend":
            bucket["dividend"] += _dec(trade.amount)
        elif trade.trade_type == "fee":
            bucket["fee"] += _dec(trade.amount) + _dec(trade.fee)

    positions: list[dict] = []
    for symbol, bucket in by_symbol.items():
        buy_qty = bucket["buy_qty"]
        buy_cost = bucket["buy_cost"]
        avg_cost = _q6(buy_cost / buy_qty) if buy_qty > 0 else Decimal("0")
        remaining_qty = buy_qty - bucket["sell_qty"]
        remaining_cost = _q4(remaining_qty * avg_cost)
        realized = _q4(
            bucket["sell_net"] - bucket["sell_qty"] * avg_cost + bucket["dividend"] - bucket["fee"]
        )
        price, valuation = latest_price.get(symbol, (None, None))
        market_value = _q4(remaining_qty * price) if price is not None else None
        unrealized = _q4(market_value - remaining_cost) if market_value is not None else None
        positions.append({
            "symbol": symbol,
            "symbol_name": bucket["symbol_name"],
            "quantity": f"{remaining_qty:.6f}",
            "avg_cost": f"{avg_cost:.6f}",
            "cost": money(remaining_cost),
            "current_price": f"{price:.6f}" if price is not None else None,
            "market_value": money(market_value) if market_value is not None else money(remaining_cost),
            "market_value_is_estimated": market_value is None,
            "realized_pnl": money(realized),
            "unrealized_pnl": money(unrealized) if unrealized is not None else None,
            "valuation_date": local_iso(valuation.valued_at) if valuation and valuation.valued_at else None,
            "valuation_source": valuation.source if valuation else None,
            "valuation_source_label": (
                enum_zh.zh("valuation_source", valuation.source) if valuation else ""
            ),
            "sold_out": remaining_qty <= 0,
            "currency": account.currency,
        })

    total_market = sum(
        (_dec(p["market_value"]) for p in positions if not p["sold_out"]), Decimal("0")
    )
    for position in positions:
        weight = (
            _dec(position["market_value"]) / total_market
            if total_market > 0 and not position["sold_out"] else Decimal("0")
        )
        position["weight"] = f"{weight * 100:.2f}"
    return positions


def _xnpv(rate: float, flows: list[tuple[datetime, float]]) -> float:
    """净现值。入参是 (时间, 现金流) 列表。"""
    base = flows[0][0]
    return sum(cf / ((1 + rate) ** ((d - base).days / 365.0)) for d, cf in flows)


def _xnpv_days(rate: float, days_flows: list[tuple[float, float]]) -> float:
    """同上，但入参已换算成「距首笔的天数」，省掉每次重启算日期差。

    XIRR 要先在 600 个利率点上扫一遍再二分，每次都要遍历全部现金流；
    日期差对同一条现金流是常量，预算一次即可。公式与 `_xnpv` 完全一致。
    """
    return sum(cf / ((1 + rate) ** (days / 365.0)) for days, cf in days_flows)


def _xirr(flows: list[tuple[datetime, float]]) -> float | None:
    """按现金流求年化收益率（XIRR）；无法收敛时返回 None。"""
    if len(flows) < 2:
        return None
    if not (any(cf > 0 for _, cf in flows) and any(cf < 0 for _, cf in flows)):
        return None
    flows = sorted(flows, key=lambda x: x[0])
    if (flows[-1][0] - flows[0][0]).days < 30:
        return None

    base = flows[0][0]
    days_flows: list[tuple[float, float]] = [((d - base).days, cf) for d, cf in flows]

    # 先网格找符号变化，再二分，避免牛顿法在极端现金流下发散
    prev_rate = -0.999
    prev_val = _xnpv_days(prev_rate, days_flows)
    bracket: tuple[float, float] | None = None
    for i in range(1, 601):
        rate = -0.999 + i * 0.01
        val = _xnpv_days(rate, days_flows)
        if prev_val == 0:
            return prev_rate
        if prev_val * val < 0:
            bracket = (prev_rate, rate)
            break
        prev_rate, prev_val = rate, val
    if bracket is None:
        return None

    lo, hi = bracket
    for _ in range(80):
        mid = (lo + hi) / 2
        val = _xnpv_days(mid, days_flows)
        if abs(val) < 1e-7:
            return mid
        if _xnpv_days(lo, days_flows) * val < 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


async def _annualized_return(
    db: AsyncSession,
    account: InvestmentAccount,
    terminal_value: Decimal,
    trades: list[InvestmentTrade] | None = None,
) -> tuple[str | None, int | None]:
    """账户级年化收益率：交易现金流 + 当前总价值，按 XIRR 计算。"""
    if trades is None:
        trades = list((await db.execute(
            select(InvestmentTrade).where(
                InvestmentTrade.account_id == account.id,
                InvestmentTrade.deleted_at.is_(None),
                InvestmentTrade.status == "confirmed",
            )
        )).scalars().all())
    flows: list[tuple[datetime, float]] = []
    for t in trades:
        when = t.traded_at or t.created_at
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        amount = float(_dec(t.amount))
        fee = float(_dec(t.fee))
        if t.trade_type == "buy":
            flows.append((when, -(amount + fee)))
        elif t.trade_type == "sell":
            flows.append((when, amount - fee))
        elif t.trade_type == "dividend":
            flows.append((when, amount))
        elif t.trade_type == "fee":
            flows.append((when, -(amount + fee)))
    if not flows:
        return None, None
    flows.sort(key=lambda x: x[0])
    span = (datetime.now(timezone.utc) - flows[0][0]).days
    flows.append((datetime.now(timezone.utc), float(terminal_value)))
    rate = _xirr(flows)
    if rate is None:
        return None, span
    return f"{rate * 100:.2f}", span


async def portfolio_of(db: AsyncSession, account: InvestmentAccount) -> dict:
    """单个投资账户的组合（带请求级缓存）。

    组合要读交易、估值、现金三条线，而一次快照里既被「计入净资产的投资」
    又被「投资汇总」用到，加上接口层回显，同一账户会被算 3~4 次
    （实测一次快照里 investment_trades 被查 17 次）。同一请求内结果不变，
    缓存按账户维度存；任何写操作（建/改/删交易、估值、账户）后必须
    调 `agg_cache.invalidate(db, "portfolio")`。
    """
    from app.core.agg_cache import request_cached

    return await request_cached(
        db, ("portfolio", account.household_id, account.id), lambda: _portfolio_of(db, account)
    )


async def _portfolio_of(db: AsyncSession, account: InvestmentAccount) -> dict:
    trades, valuations = await trades_and_valuations(db, account)
    # 现金 = 初始可用资金 + Σ交易净影响（与 cash_balance_for 同口径，只是不再查库）
    cash = _dec(account.opening_cash) + sum((_net_amount(t) for t in trades), Decimal("0"))
    positions = positions_from(account, trades, valuations)
    active = [p for p in positions if not p["sold_out"]]
    market_value = sum((_dec(p["market_value"]) for p in active), Decimal("0"))
    cost = sum((_dec(p["cost"]) for p in active), Decimal("0"))
    unrealized = sum((_dec(p["unrealized_pnl"]) for p in positions if p["unrealized_pnl"]), Decimal("0"))
    realized = sum((_dec(p["realized_pnl"]) for p in positions), Decimal("0"))
    # 账户级（未指定标的）的分红与费用同样计入已实现损益
    for trade in (t for t in trades if t.symbol is None):
        if trade.trade_type == "dividend":
            realized += _dec(trade.amount)
        elif trade.trade_type == "fee":
            realized -= _dec(trade.amount) + _dec(trade.fee)
    annualized, return_days = await _annualized_return(db, account, cash + market_value, trades=trades)
    return {
        "annualized_return_pct": annualized,
        "return_days": return_days,
        "cash_balance": cash,
        "market_value": _q4(market_value),
        "cost": _q4(cost),
        "unrealized_pnl": _q4(unrealized),
        "realized_pnl": _q4(realized),
        # 现金为负说明还没登记入金（买入金额超过账户初始资金）：
        # 账户总值不把负数带进去，否则「有 3 万持仓」会算成 0；负现金由 cash_negative 单独提示
        "total_value": _q4((cash if cash > Decimal("0") else Decimal("0")) + market_value),
        "position_count": len(active),
        "positions": positions,
        "all_priced": all(not p["market_value_is_estimated"] for p in active) if active else True,
        "cash_negative": cash < 0,
    }


def serialize_account(account: InvestmentAccount, portfolio: dict | None = None, *,
                      extra: dict | None = None, category_label: str | None = None) -> dict:
    data = {
        "id": account.id,
        "name": account.name,
        "account_type": account.account_type,
        "account_type_label": enum_zh.zh("investment_account_type", account.account_type),
        "category_id": account.category_id,
        "category_label": category_label or "",
        "institution": account.institution,
        "currency": account.currency,
        "opening_cash": money(account.opening_cash),
        "linked_account_id": account.linked_account_id,
        "member_id": account.member_id,
        # 投资市值是净资产的加项：关闭后该账户市值不计入家庭净值（如代持/测试账户）
        "include_in_net_worth": bool(account.include_in_net_worth),
        "status": account.status,
        "status_label": enum_zh.zh("lifecycle_status", account.status),
        "note": account.note,
        "version": account.version,
        "created_at": utc_iso(account.created_at),
        "updated_at": utc_iso(account.updated_at),
        "caliber": CALIBER,
    }
    if portfolio:
        data.update({
            "cash_balance": money(portfolio["cash_balance"]),
            "market_value": money(portfolio["market_value"]),
            "cost": money(portfolio["cost"]),
            "unrealized_pnl": money(portfolio["unrealized_pnl"]),
            "realized_pnl": money(portfolio["realized_pnl"]),
            "total_value": money(portfolio["total_value"]),
            "annualized_return_pct": portfolio.get("annualized_return_pct"),
            "return_days": portfolio.get("return_days"),
            "position_count": portfolio["position_count"],
            "all_priced": portfolio["all_priced"],
            "cash_negative": portfolio["cash_negative"],
        })
    if extra:
        data.update(extra)
    return data


async def list_accounts(
    db: AsyncSession, user: CurrentUser, *, keyword: str | None = None,
    account_type: str | None = None, category_id: str | None = None, member_id: str | None = None,
) -> list[tuple[InvestmentAccount, dict]]:
    conditions = [
        InvestmentAccount.household_id == user.household_id,
        InvestmentAccount.deleted_at.is_(None),
    ]
    if account_type:
        conditions.append(InvestmentAccount.account_type == account_type)
    if category_id:
        conditions.append(InvestmentAccount.category_id == category_id)
    if member_id:
        conditions.append(InvestmentAccount.member_id == member_id)
    if keyword:
        conditions.append(InvestmentAccount.name.ilike(f"%{keyword}%"))
    accounts = list(
        (await db.execute(select(InvestmentAccount).where(*conditions)
                          .order_by(InvestmentAccount.created_at.asc()))).scalars().all()
    )
    result: list[tuple[InvestmentAccount, dict]] = []
    for account in accounts:
        portfolio = await portfolio_of(db, account)
        result.append((account, portfolio))
    return result


async def portfolio_summary(db: AsyncSession, household_id: str, base_currency: str = "CNY") -> dict:
    accounts = list((await db.execute(
        select(InvestmentAccount).where(
            InvestmentAccount.household_id == household_id,
            InvestmentAccount.deleted_at.is_(None),
        )
    )).scalars().all())
    by_currency: dict[str, dict] = {}
    structure: dict[str, dict] = {}
    base_market = Decimal("0")
    base_cost = Decimal("0")
    base_cash = Decimal("0")
    base_realized = Decimal("0")
    base_unrealized = Decimal("0")
    included = 0
    for account in accounts:
        portfolio = await portfolio_of(db, account)
        included += 1
        bucket = by_currency.setdefault(account.currency, {
            "market_value": Decimal("0"), "cost": Decimal("0"), "cash": Decimal("0"),
            "realized": Decimal("0"), "unrealized": Decimal("0"), "count": 0,
        })
        bucket["market_value"] += portfolio["market_value"]
        bucket["cost"] += portfolio["cost"]
        bucket["cash"] += max(portfolio["cash_balance"], Decimal("0"))
        bucket["realized"] += portfolio["realized_pnl"]
        bucket["unrealized"] += portfolio["unrealized_pnl"]
        bucket["count"] += 1
        if account.currency == base_currency:
            base_market += portfolio["market_value"]
            base_cash += portfolio["cash_balance"]
            base_cost += portfolio["cost"]
            base_realized += portfolio["realized_pnl"]
            base_unrealized += portfolio["unrealized_pnl"]
            for position in portfolio["positions"]:
                if position["sold_out"]:
                    continue
                row = structure.setdefault(position["symbol"], {
                    "symbol": position["symbol"],
                    "symbol_name": position["symbol_name"],
                    "value": Decimal("0"),
                })
                row["value"] += _dec(position["market_value"])

    # 账户里没投出去的现金（银行理财的本金常记在这里）也要进构成，
    # 否则「只登记了本金、没有买入交易」的账户在总览里会显示 0
    if base_cash > 0:
        structure.setdefault("__cash__", {
            "symbol": "__cash__", "symbol_name": "现金 / 未投资本金",
            "value": Decimal("0"),
        })["value"] += base_cash

    return {
        "base_currency": base_currency,
        "count": included,
        "market_value": money(base_market),
        "cash": money(base_cash),
        # 投资总额 = 持仓市值 + 账户现金；净资产与总览按这个口径统计
        "total_value": money(base_market + base_cash),
        "cost": money(base_cost),
        "realized_pnl": money(base_realized),
        "unrealized_pnl": money(base_unrealized),
        "total_pnl": money(base_realized + base_unrealized),
        "by_currency": [
            {
                "currency": cur, "market_value": money(b["market_value"]),
                "cost": money(b["cost"]), "cash": money(b["cash"]),
                "total_value": money(b["market_value"] + b["cash"]),
                "realized_pnl": money(b["realized"]), "unrealized_pnl": money(b["unrealized"]),
                "count": b["count"],
            }
            for cur, b in by_currency.items()
        ],
        "structure": [
            {
                "symbol": row["symbol"],
                "symbol_name": row["symbol_name"],
                "value": money(row["value"]),
                "currency": base_currency,
            }
            for row in sorted(structure.values(), key=lambda r: r["value"], reverse=True)
        ],
        "multi_currency": len(by_currency) > 1,
        "caliber": CALIBER,
    }


def _validate_account_payload(payload: dict, *, partial: bool) -> dict:
    field_errors: dict[str, str] = {}
    data: dict = {}
    if "name" in payload or not partial:
        name = (payload.get("name") or "").strip()
        if not name:
            field_errors["name"] = "请填写投资账户名称"
        else:
            data["name"] = name
    if "account_type" in payload or not partial:
        account_type = payload.get("account_type") or "securities"
        if account_type not in enum_zh.INVESTMENT_ACCOUNT_TYPE:
            field_errors["account_type"] = "账户类型不合法"
        else:
            data["account_type"] = account_type
    if "currency" in payload:
        currency = (payload.get("currency") or "CNY").upper()
        if not (2 <= len(currency) <= 8):
            field_errors["currency"] = "币种代码不合法"
        else:
            data["currency"] = currency
    if "opening_cash" in payload and payload["opening_cash"] not in (None, ""):
        try:
            value = Decimal(str(payload["opening_cash"]))
            if not value.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors["opening_cash"] = "金额格式不正确"
        else:
            if value < 0:
                field_errors["opening_cash"] = "初始可用资金不能为负数"
            else:
                data["opening_cash"] = value
    for plain in ("institution", "category_id", "linked_account_id", "member_id", "note"):
        if plain in payload:
            data[plain] = payload[plain] or None
    if "include_in_net_worth" in payload:
        data["include_in_net_worth"] = bool(payload["include_in_net_worth"])
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "投资账户校验失败", field_errors=field_errors)
    return data


async def create_account(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None
) -> InvestmentAccount:
    data = _validate_account_payload(payload, partial=False)
    account = InvestmentAccount(
        id=new_id(),
        household_id=user.household_id or "",
        name=data["name"],
        account_type=data.get("account_type", "securities"),
        category_id=data.get("category_id"),
        institution=data.get("institution"),
        currency=data.get("currency", "CNY"),
        opening_cash=data.get("opening_cash", Decimal("0")),
        linked_account_id=data.get("linked_account_id"),
        member_id=data.get("member_id"),
        status="active",
        note=data.get("note"),
        include_in_net_worth=bool(data.get("include_in_net_worth", True)),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(account)
    await db.flush()
    await audit_service.record_audit(
        db,
        action="investment.account_create",
        action_label="新增投资账户",
        object_type="investment_account",
        object_id=account.id,
        after={"name": account.name, "account_type": account.account_type,
               "currency": account.currency, "opening_cash": account.opening_cash},
        user=user,
        ctx=ctx,
    )
    _invalidate_portfolio(db)
    return account


async def update_account(
    db: AsyncSession, user: CurrentUser, account_id: str, payload: dict,
    expected_version: int | None = None, ctx: RequestContext | None = None,
) -> InvestmentAccount:
    account = await get_account(db, user, account_id)
    if expected_version is not None and account.version != expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, status_code=409)
    data = _validate_account_payload(payload, partial=True)

    trade_count = int((await db.execute(
        select(func.count(InvestmentTrade.id)).where(InvestmentTrade.account_id == account.id)
    )).scalar_one())
    if "currency" in data and data["currency"] != account.currency and trade_count > 0:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该投资账户已有交易记录，不能直接修改币种",
            field_errors={"currency": "已有交易记录，不可改币种"},
            status_code=409,
        )
    if "opening_cash" in data and _dec(data["opening_cash"]) != _dec(account.opening_cash) \
            and trade_count > 0:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该投资账户已有交易记录，不能直接修改初始可用资金",
            field_errors={"opening_cash": "已有交易记录，不可直接修改初始可用资金"},
            status_code=409,
        )

    before = {f: getattr(account, f) for f in ACCOUNT_EDITABLE}
    for field, value in data.items():
        setattr(account, field, value)
    account.version += 1
    account.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="investment.account_update",
        action_label="编辑投资账户",
        object_type="investment_account",
        object_id=account.id,
        before=before,
        after={f: getattr(account, f) for f in ACCOUNT_EDITABLE},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    _invalidate_portfolio(db)
    return account


async def delete_account(
    db: AsyncSession, user: CurrentUser, account_id: str, reason: str,
    ctx: RequestContext | None = None,
) -> InvestmentAccount:
    if not reason or not reason.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除投资账户必须填写原因",
                       field_errors={"reason": "请填写删除原因"})
    account = await get_account(db, user, account_id)
    positions = await positions_for(db, account)
    if any(not p["sold_out"] for p in positions):
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该投资账户仍有持仓，不能删除；请先卖出或转移持仓",
            status_code=409,
        )
    account.deleted_at = datetime.now(timezone.utc)
    account.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="investment.account_delete",
        action_label="删除投资账户",
        object_type="investment_account",
        object_id=account.id,
        before={"name": account.name},
        reason=reason,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    _invalidate_portfolio(db)
    return account


async def list_trades(
    db: AsyncSession, user: CurrentUser, account_id: str, *, limit: int = 200
) -> list[InvestmentTrade]:
    await get_account(db, user, account_id)
    return list((await db.execute(
        select(InvestmentTrade)
        .where(InvestmentTrade.account_id == account_id, InvestmentTrade.deleted_at.is_(None))
        .order_by(InvestmentTrade.traded_at.desc(), InvestmentTrade.created_at.desc())
        .limit(limit)
    )).scalars().all())


async def add_trade(
    db: AsyncSession, user: CurrentUser, account_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> tuple[InvestmentTrade, BusinessEvent | None]:
    """登记交易（06 §8）：买入、卖出、分红、费用。"""
    account = await get_account(db, user, account_id)
    field_errors: dict[str, str] = {}

    trade_type = payload.get("trade_type") or "buy"
    if trade_type not in enum_zh.INVESTMENT_TRADE_TYPE:
        raise AppError(ErrorCode.VALIDATION_ERROR, "交易类型不合法",
                       field_errors={"trade_type": "请选择买入 / 卖出 / 分红 / 费用"})

    symbol = (payload.get("symbol") or "").strip() or None
    symbol_name = (payload.get("symbol_name") or "").strip() or None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount = Decimal("0")

    if trade_type in ("buy", "sell"):
        if not symbol:
            field_errors["symbol"] = "请填写标的代码"
        if payload.get("quantity") in (None, ""):
            field_errors["quantity"] = "请填写数量"
        else:
            try:
                quantity = Decimal(str(payload["quantity"]))
                if not quantity.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors["quantity"] = "数量格式不正确"
            else:
                if quantity <= 0:
                    field_errors["quantity"] = "数量必须大于 0"
        if payload.get("price") in (None, ""):
            field_errors["price"] = "请填写单价"
        else:
            try:
                price = Decimal(str(payload["price"]))
                if not price.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors["price"] = "单价格式不正确"
            else:
                if price <= 0:
                    field_errors["price"] = "单价必须大于 0"
        if quantity is not None and price is not None and quantity > 0 and price > 0:
            amount = _q4(quantity * price)
    else:
        if payload.get("amount") in (None, ""):
            field_errors["amount"] = "请填写金额"
        else:
            try:
                amount = Decimal(str(payload["amount"]))
                if not amount.is_finite():
                    raise ValueError("non-finite")
            except Exception:  # noqa: BLE001
                field_errors["amount"] = "金额格式不正确"
            else:
                if amount <= 0:
                    field_errors["amount"] = "金额必须大于 0"

    fee = Decimal("0")
    if payload.get("fee") not in (None, ""):
        try:
            fee = Decimal(str(payload["fee"]))
            if not fee.is_finite():
                raise ValueError("non-finite")
        except Exception:  # noqa: BLE001
            field_errors["fee"] = "费用格式不正确"
        else:
            if fee < 0:
                field_errors["fee"] = "费用不能为负数"

    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "交易信息校验失败", field_errors=field_errors)

    # 卖出不能超过持仓数量（不允许卖空）
    if trade_type == "sell" and quantity is not None:
        positions = await positions_for(db, account)
        held = next((p for p in positions if p["symbol"] == symbol and not p["sold_out"]), None)
        held_qty = _dec(held["quantity"]) if held else Decimal("0")
        if quantity > held_qty:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"卖出数量不能超过当前持仓 {held_qty:.6f}",
                field_errors={"quantity": f"当前持仓 {held_qty:.6f}"},
            )

    traded_at = _parse_dt(payload.get("traded_at"), "traded_at") or local_now()
    trade = InvestmentTrade(
        id=new_id(),
        household_id=account.household_id,
        account_id=account.id,
        trade_type=trade_type,
        symbol=symbol,
        symbol_name=symbol_name,
        quantity=quantity,
        price=price,
        amount=amount,
        fee=fee,
        currency=account.currency,
        traded_at=traded_at,
        status="confirmed",
        member_id=account.member_id,
        note=payload.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(trade)
    await db.flush()

    # 买入/卖出生成统一业务事件（分红与费用为投资账户内部记录）
    event: BusinessEvent | None = None
    if trade_type in ("buy", "sell"):
        net = _net_amount(trade)
        event = BusinessEvent(
            id=new_id(), household_id=account.household_id,
            event_no=f"EV{datetime.now(timezone.utc).strftime('%Y%m%d')}{new_id()[:6].upper()}",
            event_type="investment_buy" if trade_type == "buy" else "investment_sell",
            status="confirmed", occurred_at=traded_at, confirmed_at=traded_at,
            amount=abs(net), currency=account.currency,
            original_amount=abs(net), original_currency=account.currency,
            base_currency=account.currency, conversion_status="same_currency",
            investment_account_id=account.id, member_id=account.member_id,
            title=(
                f"投资买入 · {symbol_name or symbol} {quantity:.6f} × {price:.6f}"
                if trade_type == "buy"
                else f"投资卖出 · {symbol_name or symbol} {quantity:.6f} × {price:.6f}"
            ),
            counterparty=payload.get("counterparty"), source="manual",
            note=payload.get("note"), version=1,
            created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(event)
        await db.flush()
        trade.event_id = event.id
        db.add(EventMovement(
            id=new_id(), household_id=account.household_id, event_id=event.id,
            target_type="investment", target_id=account.id,
            direction="out" if trade_type == "buy" else "in",
            amount=abs(net), currency=account.currency,
            note="买入资金减少" if trade_type == "buy" else "卖出资金增加",
            created_by=user.user_id, updated_by=user.user_id,
        ))

    await audit_service.record_audit(
        db,
        action="investment.trade",
        action_label="登记投资交易",
        object_type="investment_account",
        object_id=account.id,
        related_object_id=trade.id,
        after={
            "trade_type": trade_type, "symbol": symbol, "quantity": quantity,
            "price": price, "amount": amount, "fee": fee,
        },
        user=user,
        ctx=ctx,
    )
    await db.flush()
    _invalidate_portfolio(db)
    return trade, event


async def list_valuations(
    db: AsyncSession, user: CurrentUser, account_id: str, *, symbol: str | None = None,
    limit: int = 200,
) -> list[InvestmentValuation]:
    await get_account(db, user, account_id)
    conditions = [
        InvestmentValuation.account_id == account_id,
        InvestmentValuation.deleted_at.is_(None),
    ]
    if symbol:
        conditions.append(InvestmentValuation.symbol == symbol)
    return list((await db.execute(
        select(InvestmentValuation).where(*conditions)
        .order_by(InvestmentValuation.valued_at.desc(), InvestmentValuation.created_at.desc())
        .limit(limit)
    )).scalars().all())


async def add_valuation(
    db: AsyncSession, user: CurrentUser, account_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> InvestmentValuation:
    """手工录入估值（06 §8）。"""
    account = await get_account(db, user, account_id)
    field_errors: dict[str, str] = {}
    symbol = (payload.get("symbol") or "").strip()
    if not symbol:
        field_errors["symbol"] = "请填写标的代码"
    try:
        price = Decimal(str(payload.get("price") or "0"))
        if not price.is_finite():
            raise ValueError("non-finite")
    except Exception:  # noqa: BLE001
        field_errors["price"] = "价格格式不正确"
        price = Decimal("0")
    else:
        if price <= 0:
            field_errors["price"] = "价格必须大于 0"
    source = payload.get("source") or "manual"
    if source not in enum_zh.VALUATION_SOURCE:
        field_errors["source"] = "估值来源不合法"
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "估值信息校验失败", field_errors=field_errors)

    valuation = InvestmentValuation(
        id=new_id(),
        household_id=account.household_id,
        account_id=account.id,
        symbol=symbol,
        symbol_name=(payload.get("symbol_name") or "").strip() or None,
        price=_q6(price),
        currency=account.currency,
        valued_at=_parse_dt(payload.get("valued_at"), "valued_at") or local_now(),
        source=source,
        note=payload.get("note"),
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(valuation)
    await db.flush()
    await audit_service.record_audit(
        db,
        action="investment.valuation",
        action_label="录入投资估值",
        object_type="investment_account",
        object_id=account.id,
        related_object_id=valuation.id,
        after={"symbol": symbol, "price": price, "source": source,
               "valued_at": valuation.valued_at},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    _invalidate_portfolio(db)
    return valuation

# ---------------- 行情（每天查一次，只读预览 + 用户确认后写估值） ----------------

async def quote_preview(
    db: AsyncSession, user: CurrentUser, account_id: str,
) -> dict:
    """按行情预估持仓：最新价、当日涨跌、相对成本的收益率、预估市值。

    只读，不写任何业务表；是否落成估值由 apply_quotes 显式执行。
    银行理财等无公开行情的标的返回 available=False，由页面提示手工估值。
    """
    import asyncio

    account = await get_account(db, user, account_id)
    positions = [p for p in await positions_for(db, account) if not p["sold_out"]]
    symbols = [p["symbol"] for p in positions]
    # 基金 / 理财账户里的 6 位代码按场外净值取；证券账户按场内行情取
    prefer_fund = account.account_type in ("fund", "bank_wm")
    quotes = (
        await asyncio.to_thread(quote_service.fetch_quotes, symbols, prefer_fund)
        if symbols else {}
    )

    items: list[dict] = []
    for position in positions:
        quote = quotes.get(position["symbol"], {})
        price = _dec(quote.get("price"))
        avg_cost = _dec(position["avg_cost"])
        quantity = _dec(position["quantity"])
        cost = _dec(position["cost"])
        market_value = _q4(quantity * price) if price is not None else None
        # 收益率 = (最新价 − 成本价) / 成本价，和持仓行里的未实现损益同口径
        return_pct = (
            (price - avg_cost) / avg_cost * Decimal("100")
            if price is not None and avg_cost > 0 else None
        )
        items.append({
            "symbol": position["symbol"],
            "symbol_name": quote.get("name") or position["symbol_name"],
            "quantity": position["quantity"],
            "avg_cost": position["avg_cost"],
            "cost": position["cost"],
            "price": quote.get("price"),
            "prev_close": quote.get("prev_close"),
            "change": quote.get("change"),
            "change_pct": quote.get("change_pct"),
            "income_per_10k": quote.get("income_per_10k"),
            "return_pct": f"{return_pct:.2f}" if return_pct is not None else None,
            "market_value": money(market_value) if market_value is not None else None,
            "pnl": money(_q4(market_value - cost)) if market_value is not None else None,
            "as_of": quote.get("as_of"),
            "source": quote.get("source"),
            "source_label": quote.get("source_label"),
            "kind": quote.get("kind"),
            "available": bool(quote.get("available")) and quote.get("price") is not None,
            "reason": quote.get("reason") or (
                "货币基金没有净值涨跌，按万份收益展示" if quote.get("kind") == "money_fund" else None
            ),
        })

    updated = [i for i in items if i["available"]]
    skipped = [i for i in items if not i["available"]]
    return {
        "account_id": account.id,
        "currency": account.currency,
        "as_of": local_iso(local_now()),
        "items": items,
        "summary": {
            "updated": len(updated),
            "skipped": len(skipped),
            "skipped_symbols": [i["symbol"] for i in skipped],
        },
    }


async def apply_quotes(
    db: AsyncSession, user: CurrentUser, account_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> dict:
    """把行情预览里成功的价格写成估值（source=market），并返回写入条数。

    跳过没有行情的标的（银行理财等），不会用成本价或估算值冒充净值。
    """
    preview = await quote_preview(db, user, account_id)
    valued_at = payload.get("valued_at")
    written: list[str] = []
    for item in preview["items"]:
        if not item["available"] or not item["price"]:
            continue
        await add_valuation(db, user, account_id, {
            "symbol": item["symbol"],
            "symbol_name": item["symbol_name"],
            "price": item["price"],
            "valued_at": valued_at,
            "source": "market",
            "note": f"按行情更新（{item['source_label'] or '行情'}，数据日 {item['as_of'] or '—'}）",
        }, ctx)
        written.append(item["symbol"])
    return {
        "updated": len(written),
        "symbols": written,
        "skipped": preview["summary"]["skipped_symbols"],
    }
