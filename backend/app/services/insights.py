"""脉诊服务（07、18 §4 脉枢台/脉诊）。

所有金额与图表数据由后端确定性计算，前端不自行计算。
图表统一行为（07 §8）：颜色必须配文字图例与数值、可切表格、不提供导出；
每条结论必须含口径、期间、数据更新时间、来源数量与查看明细入口。

M6 交付范围：
- 综合脉诊 overview（复用脉枢台统计，重塑为「结论列表」）
- 六个主题 assets / income / expenses / liabilities / investments / leases
- 数据质量中心（真实规则检查，非盘点，不创建后台任务）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.context import CurrentUser
from app.core.money import fmt_amount
from app.core.ids import utc_iso
from app.core.timeutil import local_iso
from app.db.models.business import Account, Asset, BusinessEvent

CONCISION_TYPES = {
    "fact": "事实", "computed": "计算", "inference": "推断", "suggestion": "建议",
}

TOPIC_TITLES = {
    "assets": "资产分析",
    "income": "收入分析",
    "expenses": "消费分析",
    "liabilities": "负债分析",
    "investments": "投资分析",
    "leases": "租赁分析",
}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money(value: Decimal) -> str:
    """金额对外统一输出（默认两位小数，不带千分位——图表数值需要可解析）。"""
    return fmt_amount(value)


def _money_stat(value) -> str:
    """统计卡里的金额文案：带千分位，和前端 formatAmount 的观感一致。"""
    from app.core.money import get_amount_precision

    precision = get_amount_precision()
    return f"{_dec(value):,.{precision}f}"


async def _household_ctx(db: AsyncSession, user: CurrentUser, currency: str | None) -> tuple[str, str]:
    from app.services.setup import current_household

    household = await current_household(db)
    base_currency = currency or (household.base_currency if household else "CNY")
    return (user.household_id or ""), base_currency


async def _category_totals(
    db: AsyncSession,
    household_id: str,
    event_types: tuple[str, ...],
    start: datetime | None,
    end: datetime | None,
    top: int = 8,
) -> list[dict]:
    """按分类聚合已确认事件金额（收入或支出），返回前 top 名（含「未分类」）。"""
    from app.db.models.config import Category

    conditions = [
        BusinessEvent.household_id == household_id,
        BusinessEvent.status == "confirmed",
        BusinessEvent.deleted_at.is_(None),
        BusinessEvent.event_type.in_(event_types),
    ]
    if start is not None:
        conditions.append(BusinessEvent.occurred_at >= start)
    if end is not None:
        conditions.append(BusinessEvent.occurred_at < end)

    rows = (
        await db.execute(
            select(BusinessEvent.category_id, func.coalesce(func.sum(BusinessEvent.amount), 0))
            .where(*conditions)
            .group_by(BusinessEvent.category_id)
        )
    ).all()

    category_ids = [r[0] for r in rows if r[0]]
    names: dict[str, str] = {}
    if category_ids:
        cats = (await db.execute(
            select(Category.id, Category.name).where(Category.id.in_(category_ids))
        )).all()
        names = {c[0]: c[1] for c in cats}

    items: list[dict] = []
    for category_id, total in rows:
        items.append({
            "name": names.get(category_id, "未分类") if category_id else "未分类",
            "value": _money(_dec(total)),
        })
    items.sort(key=lambda x: Decimal(x["value"]), reverse=True)
    return items[:top]


async def _event_count(
    db: AsyncSession, household_id: str, event_types: tuple[str, ...],
    start: datetime | None, end: datetime | None,
) -> int:
    conditions = [
        BusinessEvent.household_id == household_id,
        BusinessEvent.status == "confirmed",
        BusinessEvent.deleted_at.is_(None),
        BusinessEvent.event_type.in_(event_types),
    ]
    if start is not None:
        conditions.append(BusinessEvent.occurred_at >= start)
    if end is not None:
        conditions.append(BusinessEvent.occurred_at < end)
    return int((await db.execute(select(func.count(BusinessEvent.id)).where(*conditions))).scalar_one())


async def insight_overview(
    db: AsyncSession, user: CurrentUser, period: str = "current_month", currency: str | None = None
) -> dict:
    """综合脉诊（07 §1）：统一筛选 + 结论列表（净资产/收支/负债/资产/投资/租赁）。"""
    from app.services import stats as stats_service

    snap = await stats_service.dashboard_snapshot(db, user, period, currency)
    base_currency = snap["base_currency"]
    nw = snap["net_worth"]
    m = snap["metrics"]

    data_updated_at = snap["data_updated_at"]
    period_label = snap["period_label"]

    def _conclusion(key, title, ctype, caliber, detail_to, summary, source_count, highlight=None):
        return {
            "key": key,
            "title": title,
            "type": ctype,
            "type_label": CONCISION_TYPES[ctype],
            "caliber": caliber,
            "period": period,
            "period_label": period_label,
            "data_updated_at": data_updated_at,
            "source_count": source_count,
            "detail_to": detail_to,
            "summary": summary,
            "highlight": highlight,
        }

    conclusions = [
        _conclusion(
            "net_worth", "净资产", "computed", snap["caliber"]["net_worth"], "/insights/assets",
            f"账户 {nw['accounts']} + 资产 {nw['assets']} + 投资 {nw['investments']} − 负债 {nw['total_liabilities']}",
            nw["included_accounts"] + nw["included_assets"] + nw["included_liabilities"] + nw["included_investments"],
            f"{nw['net_worth']} {base_currency}",
        ),
        _conclusion(
            "income", "收入", "computed", snap["caliber"]["income"], "/insights/income",
            "本期已确认收入（含租金实收，不含借款/押金/内部转入）",
            snap["counts"].get("confirmed", 0), f"{m['income']} {base_currency}",
        ),
        _conclusion(
            "expense", "支出", "computed", snap["caliber"]["expense"], "/insights/expenses",
            "本期已确认支出（含消费、利息/手续费与贷款还款本金，不含信用卡还款）",
            snap["counts"].get("confirmed", 0), f"{m['expense']} {base_currency}",
        ),
        _conclusion(
            "liabilities", "负债压力", "fact", "负债剩余本金 = 初始金额 + 已确认变动净额", "/insights/liabilities",
            "负债剩余本金、每月应还与逾期笔数",
            nw["included_liabilities"], f"{nw['total_liabilities']} {base_currency}",
        ),
        _conclusion(
            "assets", "资产结构", "computed", snap["caliber"]["asset_value"], "/insights/assets",
            "按资产类型展示当前价值占比（仅基础币种）",
            nw["included_assets"], f"{nw['assets']} {base_currency}",
        ),
        _conclusion(
            "investments", "投资", "computed", "投资总额 = 账户现金 + 持仓数量 × 估值价", "/insights/investments",
            "投资总额、成本与已实现/未实现损益",
            nw["included_investments"], f"{nw['investments']} {base_currency}",
        ),
        _conclusion(
            "leases", "租赁", "computed", "收租率 = 已收 /（已收 + 应收）", "/insights/leases",
            "应收/实收、收租率、逾期金额与押金余额",
            snap["summaries"]["leases"]["count"], None,
        ),
    ]

    return {
        "period": period,
        "period_label": period_label,
        "period_range": snap["period_range"],
        "base_currency": base_currency,
        "currency_badge": snap["currency_badge"],
        "members_scope": "全部成员",
        "include_investment_valuation": nw["investment_included"],
        "module_enabled": True,
        "data_completeness": {
            "complete": not snap["is_partial"],
            "missing": ["存在未确认草稿，草稿不计入统计"] if snap["is_partial"] else [],
            "note": "草稿不计入任何统计" if snap["is_partial"] else "统计口径完整",
        },
        "ai_summary": None,
        "ai_summary_state": "on_demand",
        "ai_summary_hint": "摘要由 AI 按需生成（GET /insights/ai-summary），不阻塞结论列表。",
        "conclusions": conclusions,
        "data_updated_at": data_updated_at,
        "conclusion_types": CONCISION_TYPES,
    }


async def _topic_assets(db: AsyncSession, household_id: str, base_currency: str, tz_name: str) -> dict:
    from app.services import stats as stats_service
    from app.services.assets import asset_summary

    structure = (await stats_service._asset_net_worth_parts(db, household_id, base_currency))["structure"]
    trend = await stats_service._net_worth_trend(db, household_id, 6, tz_name, base_currency)
    liability_parts = await stats_service._liability_parts(db, household_id, base_currency)
    summary = await asset_summary(db, household_id, base_currency)

    total_assets = _money(_dec(summary.get("total_value", "0")))
    current = _money(_dec(summary.get("current_value", "0")))
    non_current = _money(_dec(summary.get("non_current_value", "0")))
    liability_total = liability_parts["base_total"]

    panels = [
        {"key": "structure", "title": "资产类别环形图", "kind": "pie", "data": structure},
        {
            "key": "net_worth_trend", "title": "净资产趋势（近 6 个月）", "kind": "line",
            "data": {"labels": [p["label"] for p in trend],
                     "series": [{"name": "净资产", "values": [p["value"] for p in trend]}]},
        },
        {
            "key": "asset_vs_liability", "title": "资产与负债对照", "kind": "bar",
            "data": {"labels": ["总资产", "总负债", "净资产"],
                     "series": [{"name": "金额", "values": [total_assets, _money(liability_total),
                                                              _money(_dec(total_assets) - liability_total)]}]},
        },
    ]
    stats = [
        {"label": "资产总值", "value": f"{_money_stat(total_assets)} {base_currency}"},
        {"label": "流动资产", "value": f"{_money_stat(current)} {base_currency}"},
        {"label": "非流动资产", "value": f"{_money_stat(non_current)} {base_currency}"},
        {"label": "待估值资产", "value": f"{summary.get('pending_valuation_count', 0)} 项"},
    ]
    conclusions = [
        f"资产结构共 {len(structure)} 类，最大的类别是{structure[0]['asset_type_label']}（{structure[0]['value']} {base_currency}）。" if structure else "暂无资产数据。",
        "估值缺口：待估值资产在数据质量中心单独列出，建议补充当前价值与估值来源。",
    ]
    return {"panels": panels, "stats": stats, "conclusions": conclusions,
            "source_count": summary.get("count", 0)}


async def _topic_income(db: AsyncSession, household_id: str, base_currency: str, tz_name: str,
                        start, end) -> dict:
    from app.services import stats as stats_service

    monthly = await stats_service._monthly_series(db, household_id, 6, tz_name)
    totals = await stats_service._event_totals(db, household_id, start, end)
    income = totals.get(base_currency, {}).get("income", Decimal("0"))
    expense = totals.get(base_currency, {}).get("expense", Decimal("0"))
    by_category = await _category_totals(db, household_id, stats_service.INCOME_EVENT_TYPES, start, end)
    count = await _event_count(db, household_id, stats_service.INCOME_EVENT_TYPES, start, end)
    coverage = "—" if expense <= 0 else f"{income / expense:.2f}"

    panels = [
        {
            "key": "source", "title": "收入来源", "kind": "pie", "data": by_category,
        },
        {
            "key": "trend", "title": "收入月度趋势（近 6 个月）", "kind": "line",
            "data": {"labels": [p["label"] for p in monthly],
                     "series": [{"name": "收入", "values": [p["income"] for p in monthly]}]},
        },
    ]
    stats = [
        {"label": "本期收入", "value": f"{_money_stat(income)} {base_currency}"},
        {"label": "收入覆盖支出", "value": f"{coverage} 倍"},
        {"label": "收入笔数", "value": f"{count} 笔"},
    ]
    conclusions = [
        "收入按确认日期统计；借款、押金、内部转入与租金应收均不计入收入（单独排除说明）。",
        f"本期共 {count} 笔已确认收入；收入来源最多的分类是{by_category[0]['name']}。" if by_category else "本期暂无收入。",
    ]
    return {"panels": panels, "stats": stats, "conclusions": conclusions, "source_count": count}


async def _topic_expenses(db: AsyncSession, household_id: str, base_currency: str, tz_name: str,
                          start, end) -> dict:
    from app.services import stats as stats_service

    monthly = await stats_service._monthly_series(db, household_id, 6, tz_name)
    totals = await stats_service._event_totals(db, household_id, start, end)
    expense = totals.get(base_currency, {}).get("expense", Decimal("0"))
    by_category = await _category_totals(db, household_id, stats_service.EXPENSE_EVENT_TYPES, start, end)
    count = await _event_count(db, household_id, stats_service.EXPENSE_EVENT_TYPES, start, end)

    panels = [
        {"key": "category", "title": "分类环形图", "kind": "pie", "data": by_category},
        {
            "key": "trend", "title": "支出月度趋势（近 6 个月）", "kind": "line",
            "data": {"labels": [p["label"] for p in monthly],
                     "series": [{"name": "支出", "values": [p["expense"] for p in monthly]}]},
        },
    ]
    stats = [
        {"label": "本期支出", "value": f"{_money_stat(expense)} {base_currency}"},
        {"label": "支出笔数", "value": f"{count} 笔"},
        {"label": "最多分类", "value": by_category[0]["name"] if by_category else "—"},
    ]
    conclusions = [
        "支出含已确认消费、利息/手续费与贷款还款本金；信用卡还款属内部转账不重复计入。",
        f"本期共 {count} 笔已确认支出；占比最高的分类是{by_category[0]['name']}。" if by_category else "本期暂无支出。",
    ]
    return {"panels": panels, "stats": stats, "conclusions": conclusions, "source_count": count}


async def _topic_liabilities(db: AsyncSession, household_id: str, base_currency: str, tz_name: str) -> dict:
    from app.services import liabilities as liability_service
    from app.services import stats as stats_service

    trend = await stats_service._liability_trend(db, household_id, 6, tz_name, base_currency)
    summary = await liability_service.liability_summary(db, household_id, base_currency)
    by_type = summary["by_type"]
    upcoming = summary["upcoming"]

    panels = [
        {
            "key": "trend", "title": "负债余额趋势（近 6 个月）", "kind": "line",
            "data": {"labels": [p["label"] for p in trend],
                     "series": [{"name": "剩余本金", "values": [p["value"] for p in trend]}]},
        },
        {"key": "by_type", "title": "负债类型结构", "kind": "pie",
         "data": [{"name": x["liability_type_label"], "value": x["outstanding"]} for x in by_type]},
        {
            "key": "upcoming", "title": "临近还款", "kind": "table",
            "data": {"columns": ["负债", "类型", "下次还款日", "每期应还", "剩余本金"],
                     "rows": [[x["name"], x["liability_type_label"], (x["next_payment_date"] or "")[:10],
                               x["monthly_payment"], x["outstanding"]] for x in upcoming[:8]]},
        },
    ]
    stats = [
        {"label": "剩余本金", "value": f"{_money_stat(summary['total_outstanding'])} {base_currency}"},
        {"label": "每月应还", "value": f"{_money_stat(summary['monthly_due'])} {base_currency}"},
        {"label": "逾期笔数", "value": f"{summary['overdue_count']} 笔"},
    ]
    conclusions = [
        "负债分析只提供事实性压力分析，不提出不可执行的支付动作。",
        f"共 {summary['count']} 笔负债，剩余本金 {summary['total_outstanding']} {base_currency}；逾期 {summary['overdue_count']} 笔。",
    ]
    return {"panels": panels, "stats": stats, "conclusions": conclusions,
            "source_count": summary["count"]}


async def _topic_investments(db: AsyncSession, household_id: str, base_currency: str) -> dict:
    from app.services import investments as investment_service

    summary = await investment_service.portfolio_summary(db, household_id, base_currency)
    structure = summary["structure"]

    panels = [
        {"key": "structure", "title": "持仓占比", "kind": "pie",
         "data": [{"name": x["symbol_name"] or x["symbol"], "value": x["value"]} for x in structure]},
        {
            "key": "cost_market", "title": "成本与市值", "kind": "bar",
            "data": {"labels": ["成本", "市值"],
                     "series": [{"name": "金额", "values": [summary.get("cost", "0"),
                                                              summary.get("market_value", "0")]}]},
        },
    ]
    stats = [
        {"label": "投资总额", "value": f"{_money_stat(summary.get('total_value', '0'))} {base_currency}"},
        {"label": "其中持仓", "value": f"{_money_stat(summary.get('market_value', '0'))} {base_currency}"},
        {"label": "其中现金", "value": f"{_money_stat(summary.get('cash', '0'))} {base_currency}"},
        {"label": "成本", "value": f"{_money_stat(summary.get('cost', '0'))} {base_currency}"},
        {"label": "已实现损益", "value": f"{_money_stat(summary.get('realized_pnl', '0'))} {base_currency}"},
        {"label": "未实现损益", "value": f"{_money_stat(summary.get('unrealized_pnl', '0'))} {base_currency}"},
    ]

    # 集中度：单一标的市值占比（不含「现金/未投资本金」）> 50% 视为集中度过高
    concentration: dict = {"level": "ok", "top_name": None, "top_pct": "0.0", "position_count": 0}
    positions = [x for x in structure if x.get("symbol") != "__cash__"]
    position_total = sum((_dec(x["value"]) for x in positions), Decimal("0"))
    if positions and position_total > 0:
        top = max(positions, key=lambda x: _dec(x["value"]))
        top_pct = _dec(top["value"]) / position_total * 100
        level = "ok" if top_pct <= 50 else ("warn" if top_pct <= 70 else "bad")
        concentration = {
            "level": level,
            "top_name": top.get("symbol_name") or top.get("symbol"),
            "top_pct": f"{top_pct:.1f}",
            "position_count": len(positions),
        }
        stats.append({
            "label": "最大持仓占比",
            "value": f"{top_pct:.1f}%（{concentration['top_name']}）",
        })

    conclusions = [
        "投资分析明确不提供自动交易建议；无行情数据时仅基于手工估值。",
        f"共 {summary.get('count', 0)} 个投资账户，持仓 {len(structure)} 个标的。",
    ]
    if concentration["top_name"] and concentration["level"] != "ok":
        conclusions.append(
            f"集中度偏高：{concentration['top_name']} 占持仓市值 {concentration['top_pct']}%，"
            "单一标的波动会明显影响家庭净资产。"
        )
    return {"panels": panels, "stats": stats, "conclusions": conclusions,
            "concentration": concentration,
            "source_count": summary.get("count", 0)}


async def _topic_leases(db: AsyncSession, household_id: str, base_currency: str) -> dict:
    from app.services import leases as lease_service

    summary = await lease_service.lease_summary(db, household_id, base_currency)
    rate = summary.get("collection_rate")

    panels = [
        {
            "key": "receivable", "title": "应收 / 实收对比", "kind": "bar",
            "data": {"labels": ["应收（待收）", "实收（已收）"],
                     "series": [{"name": "金额",
                                 "values": [summary.get("receivable_pending", "0"),
                                            summary.get("receivable_paid", "0")]}]},
        },
    ]
    stats = [
        {"label": "收租率", "value": f"{rate}%" if rate else "—"},
        {"label": "逾期金额", "value": f"{_money_stat(summary.get('overdue_amount', '0'))} {base_currency}"},
        {"label": "逾期笔数", "value": f"{summary.get('overdue_count', 0)} 笔"},
        {"label": "押金余额", "value": f"{_money_stat(summary.get('deposit_held', '0'))} {base_currency}"},
        {"label": "90 天内到期", "value": f"{summary.get('expiring_count', 0)} 份"},
    ]
    conclusions = [
        "逾期区域可跳转对应租约与提醒；押金不计收支，单独显示为待退还资金。",
        f"共 {summary['count']} 份租约，其中在租 {summary['active_count']} 份。",
    ]
    return {"panels": panels, "stats": stats, "conclusions": conclusions,
            "source_count": summary["count"]}


async def topic_insight(
    db: AsyncSession, user: CurrentUser, topic: str,
    period: str = "current_month", currency: str | None = None,
) -> dict:
    """主题脉诊（07 §2–§7）：统一 panels 结构供前端通用渲染。"""
    from app.services import stats as stats_service
    from app.services.setup import current_household

    household_id, base_currency = await _household_ctx(db, user, currency)
    household = await current_household(db)
    tz_name = household.timezone if household else "Asia/Shanghai"
    start, end, _range = stats_service.period_range(period, tz_name)

    handlers = {
        "assets": _topic_assets,
        "income": _topic_income,
        "expenses": _topic_expenses,
        "liabilities": _topic_liabilities,
        "investments": _topic_investments,
        "leases": _topic_leases,
    }
    handler = handlers.get(topic)
    if handler is None:
        from app.core.errors import AppError, ErrorCode
        raise AppError(ErrorCode.VALIDATION_ERROR, "未知的脉诊主题", status_code=400)

    # income/expenses 需要期间参数；investments/leases 不需要时区参数
    if topic in ("income", "expenses"):
        result = await handler(db, household_id, base_currency, tz_name, start, end)
    elif topic in ("investments", "leases"):
        result = await handler(db, household_id, base_currency)
    else:
        result = await handler(db, household_id, base_currency, tz_name)

    return {
        "topic": topic,
        "title": TOPIC_TITLES[topic],
        "period": period,
        "period_label": stats_service.PERIODS.get(period, period),
        "period_range": _range,
        "base_currency": base_currency,
        "data_updated_at": utc_iso(datetime.now(timezone.utc)),
        "source_count": result["source_count"],
        "panels": result["panels"],
        "stats": result["stats"],
        "conclusions": result["conclusions"],
    }


async def data_quality_report(db: AsyncSession, user: CurrentUser) -> dict:
    """数据质量中心（07 §10）：真实规则检查，非盘点、不创建后台任务、不修改正式数据。"""
    from app.db.models.assets import (ParkingSpace, PreciousMetal, RealEstate,
                                      Vehicle, VehicleInsurance)
    from app.db.models.finance import Liability
    from app.db.models.leases import LeaseReceivable
    from app.services import stats as stats_service

    household_id = user.household_id or ""
    now = datetime.now(timezone.utc)
    items: list[dict] = []

    def _add(level, module, title, description, action_to, count):
        items.append({
            "level": level, "level_label": enum_zh.zh("issue_level", level),
            "module": module, "title": title, "description": description,
            "action_to": action_to, "count": count,
        })

    # 1) 未分类流水（收入/支出）
    uncat = (await db.execute(
        select(func.count(BusinessEvent.id)).where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.event_type.in_(stats_service.INCOME_EVENT_TYPES + stats_service.EXPENSE_EVENT_TYPES),
            BusinessEvent.category_id.is_(None),
        )
    )).scalar_one()
    if uncat:
        _add("warning", "流水", "未分类流水", f"{uncat} 笔已确认流水没有分类，影响消费/收入分析。",
             "/ledger/transactions", int(uncat))

    # 2) 收入/支出缺关联账户
    no_account = (await db.execute(
        select(func.count(BusinessEvent.id)).where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.event_type.in_(stats_service.INCOME_EVENT_TYPES + stats_service.EXPENSE_EVENT_TYPES),
            BusinessEvent.account_id.is_(None),
        )
    )).scalar_one()
    if no_account:
        _add("warning", "流水", "资金事件未关联账户", f"{no_account} 笔收支没有关联账户，无法计入账户余额。",
             "/ledger/transactions", int(no_account))

    # 3) 资产缺少当前估值
    assets = list((await db.execute(
        select(Asset).where(Asset.household_id == household_id, Asset.deleted_at.is_(None))
    )).scalars().all())
    no_valuation = [a for a in assets if _dec(a.current_value) <= 0 or a.last_valuation_date is None]
    if no_valuation:
        _add("warning", "资产", "资产缺少当前估值",
             f"{len(no_valuation)} 项资产没有当前价值或估值日期，不计入净资产。",
             "/ledger/assets", len(no_valuation))

    # 4) 不动产有房贷标记但未关联负债（已关联房贷的房产不算缺失）
    estates = list((await db.execute(
        select(RealEstate).where(RealEstate.household_id == household_id, RealEstate.deleted_at.is_(None))
    )).scalars().all())
    # 已被房贷负债关联的资产 id（关注 linked_asset_id，不能只看 has_mortgage 标记）
    linked_mortgage_assets = {
        row[0] for row in (await db.execute(
            select(Liability.linked_asset_id).where(
                Liability.household_id == household_id,
                Liability.liability_type == "mortgage",
                Liability.linked_asset_id.is_not(None),
                Liability.deleted_at.is_(None),
            )
        )).all() if row[0]
    }
    mortgage_missing = [
        e for e in estates
        if e.has_mortgage and e.asset_id not in linked_mortgage_assets
    ]
    if mortgage_missing:
        _add("info", "资产", "不动产房贷未关联负债",
             f"{len(mortgage_missing)} 处不动产标记有房贷，但未关联负债记录，贷款余额未并入统计。",
             "/ledger/assets/real-estates", len(mortgage_missing))

    # 5) 车位未关联不动产
    parking = list((await db.execute(
        select(ParkingSpace).where(ParkingSpace.household_id == household_id, ParkingSpace.deleted_at.is_(None))
    )).scalars().all())
    parking_unlinked = [p for p in parking if not p.linked_real_estate_id]
    if parking_unlinked:
        _add("info", "资产", "车位未关联不动产", f"{len(parking_unlinked)} 个车位未关联具体不动产。",
             "/ledger/assets/parking-spaces", len(parking_unlinked))

    # 6) 车辆缺少保险
    vehicles = list((await db.execute(
        select(Vehicle).where(Vehicle.household_id == household_id, Vehicle.deleted_at.is_(None))
    )).scalars().all())
    if vehicles:
        insured = (await db.execute(
            select(func.count(VehicleInsurance.id)).where(
                VehicleInsurance.household_id == household_id, VehicleInsurance.deleted_at.is_(None))
        )).scalar_one()
        if insured < len(vehicles):
            _add("info", "资产", "车辆缺少保险", f"{len(vehicles) - int(insured)} 辆车未登记保险。",
                 "/ledger/assets/vehicles", len(vehicles) - int(insured))

    # 7) 贵金属缺少纯度/重量/估值来源
    metals = list((await db.execute(
        select(PreciousMetal).where(PreciousMetal.household_id == household_id,
                                    PreciousMetal.deleted_at.is_(None))
    )).scalars().all())
    metal_missing = [m for m in metals if m.purity is None or m.weight is None or not m.valuation_source]
    if metal_missing:
        _add("warning", "资产", "贵金属缺少关键字段",
             f"{len(metal_missing)} 项贵金属缺少纯度、重量或估值来源。",
             "/ledger/assets/precious-metals", len(metal_missing))

    # 8) 负债缺少利率/还款方式/到期日
    liabilities = list((await db.execute(
        select(Liability).where(Liability.household_id == household_id, Liability.deleted_at.is_(None))
    )).scalars().all())
    liability_missing = [l for l in liabilities if l.liability_type not in ("credit_card",)
                         and (l.interest_rate is None or not l.repayment_method or l.end_date is None)]
    if liability_missing:
        _add("warning", "负债", "负债缺少利率/还款方式/到期日",
             f"{len(liability_missing)} 笔负债缺少利率、还款方式或到期日，无法测算还款计划。",
             "/ledger/liabilities", len(liability_missing))

    # 9) 租金应收未实收（逾期）
    overdue_receivables = (await db.execute(
        select(func.count(LeaseReceivable.id)).where(
            LeaseReceivable.household_id == household_id,
            LeaseReceivable.deleted_at.is_(None),
            LeaseReceivable.status == "pending",
            LeaseReceivable.due_date.isnot(None),
            LeaseReceivable.due_date < now,
        )
    )).scalar_one()
    if overdue_receivables:
        _add("warning", "租赁", "租金应收逾期", f"{overdue_receivables} 笔租金应收已逾期未实收。",
             "/ledger/leases", int(overdue_receivables))

    # 10) 疑似重复流水（同账户+同金额+同日+同标题）
    dupes = (await db.execute(
        select(BusinessEvent.account_id, BusinessEvent.amount, func.date(BusinessEvent.occurred_at),
               BusinessEvent.title, func.count(BusinessEvent.id))
        .where(BusinessEvent.household_id == household_id,
               BusinessEvent.status == "confirmed", BusinessEvent.deleted_at.is_(None),
               BusinessEvent.account_id.isnot(None))
        .group_by(BusinessEvent.account_id, BusinessEvent.amount,
                  func.date(BusinessEvent.occurred_at), BusinessEvent.title)
        .having(func.count(BusinessEvent.id) > 1)
    )).all()
    if dupes:
        dup_total = sum(int(r[4]) - 1 for r in dupes)
        _add("info", "流水", "疑似重复流水", f"检测到 {len(dupes)} 组疑似重复流水（共 {dup_total} 条冗余）。",
             "/ledger/transactions", dup_total)

    critical = sum(1 for i in items if i["level"] == "critical")
    warning = sum(1 for i in items if i["level"] == "warning")
    info = sum(1 for i in items if i["level"] == "info")
    total = critical + warning + info
    score = 100 - (critical * 20 + warning * 5 + info * 2) if total else 100
    score = max(0, min(100, score))

    items.sort(key=lambda x: {"critical": 0, "warning": 1, "info": 2}[x["level"]])

    return {
        "checked": True,
        "module_enabled": True,
        "score": score,
        "critical": critical,
        "warning": warning,
        "info": info,
        "last_checked_at": utc_iso(now),
        "is_inventory": False,
        "note": "数据质量中心只检查财务数据是否完整、可关联、可用于统计与 AI 分析；修复只能打开对应业务页面，质量中心不直接修改正式数据。",
        "items": items,
    }


async def spending_habits(
    db: AsyncSession, user: CurrentUser, period: str = "current_month",
    currency: str | None = None, months: int = 6,
) -> dict:
    """消费习惯分析（消费深度脉诊）：分类排行、趋势环比、大额消费、异常波动、周期性支出。

    全部为确定性统计，不依赖任何模型；模型未连接时照常可用。
    与「消费分析」主题互补：主题页给结论，本页面给「习惯」层面的深度拆解（同口径）。
    """
    from app.db.models.config import Category
    from app.services import stats as stats_service
    from app.services.setup import current_household

    household_id, base_currency = await _household_ctx(db, user, currency)
    household = await current_household(db)
    tz_name = household.timezone if household else "Asia/Shanghai"
    tz = stats_service.resolve_tz(tz_name)

    start, end, period_label = stats_service.period_range(period, tz_name)

    # 近 N 月窗口（用于趋势 / 周期性 / 环比）
    now_local = datetime.now(tz)
    first_this = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    y, m = first_this.year, first_this.month
    m -= months - 1
    while m <= 0:
        m += 12
        y -= 1
    window_start_local = first_this.replace(year=y, month=m, day=1)
    # 业务时间按本地墙钟存储，窗口边界也用本地墙钟（见 app/core/timeutil.py）
    window_start = window_start_local.replace(tzinfo=None)
    window_end = (first_this + timedelta(days=32)).replace(day=1).replace(tzinfo=None)

    # 还款（贷款偿还）不属于「消费」：房贷/车贷/其他贷款/信用卡还款归在「还款」一级分类下，
    # 消费习惯分析只分析真正的消费，把这些还款类支出（含还款利息）排除。
    repay_parent = (await db.execute(
        select(Category.id).where(
            Category.household_id == household_id,
            Category.domain == "expense",
            Category.name == "还款",
            Category.parent_id.is_(None),
            Category.deleted_at.is_(None),
        )
    )).scalars().first()
    excluded_category_ids: list[str] = []
    if repay_parent:
        excluded_category_ids = [repay_parent] + [
            cid for cid in (await db.execute(
                select(Category.id).where(Category.parent_id == repay_parent, Category.deleted_at.is_(None))
            )).scalars().all()
        ]

    rows = (await db.execute(
        select(BusinessEvent.occurred_at, BusinessEvent.amount, BusinessEvent.currency,
               BusinessEvent.category_id, BusinessEvent.title, BusinessEvent.counterparty)
        .where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.status == "confirmed",
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.event_type == "expense",
            BusinessEvent.occurred_at >= window_start,
            BusinessEvent.occurred_at < window_end,
            # 排除「还款」分类下的支出（贷款偿还不算消费）；未分类的支出保留（按「未分类」统计）
            ~BusinessEvent.category_id.in_(excluded_category_ids)
            | BusinessEvent.category_id.is_(None) if excluded_category_ids else True,
        )
        .order_by(BusinessEvent.occurred_at.desc())
    )).all()

    cat_ids = {r[3] for r in rows if r[3]}
    names: dict[str, str] = {}
    if cat_ids:
        cats = (await db.execute(select(Category.id, Category.name).where(Category.id.in_(cat_ids)))).all()
        names = {c[0]: c[1] for c in cats}

    def _cname(cid) -> str:
        return names.get(cid, "未分类") if cid else "未分类"

    def _pct(part: Decimal, whole: Decimal) -> str:
        return f"{part / whole * 100:.1f}%" if whole > 0 else "0.0%"

    # 只统计基准币种（多币种按币种分组，不做相加）
    month_rows = [r for r in rows if r[2] == base_currency]
    cur_rows = [
        r for r in month_rows
        if (start is None or r[0] >= start) and (end is None or r[0] < end)
    ]

    total = sum((_dec(r[1]) for r in cur_rows), Decimal("0"))
    count = len(cur_rows)

    # —— 分类聚合（本期）——
    cat_sum: dict[str, Decimal] = {}
    cat_cnt: dict[str, int] = {}
    for r in cur_rows:
        nm = _cname(r[3])
        cat_sum[nm] = cat_sum.get(nm, Decimal("0")) + _dec(r[1])
        cat_cnt[nm] = cat_cnt.get(nm, 0) + 1
    cat_items = sorted(cat_sum.items(), key=lambda x: x[1], reverse=True)

    # —— 近 N 月序列 / 每月分类 ——
    month_labels: list[str] = []
    cursor = window_start_local
    for _ in range(months):
        month_labels.append(f"{cursor:%Y-%m}")
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    month_totals = {k: Decimal("0") for k in month_labels}
    month_cats: dict[str, set[str]] = {k: set() for k in month_labels}
    month_cat_amt: dict[str, dict[str, Decimal]] = {k: {} for k in month_labels}
    for r in month_rows:
        mk = f"{r[0]:%Y-%m}"
        if mk in month_totals:
            month_totals[mk] += _dec(r[1])
            nm = _cname(r[3])
            month_cats[mk].add(nm)
            month_cat_amt[mk][nm] = month_cat_amt[mk].get(nm, Decimal("0")) + _dec(r[1])

    # —— 周期性支出（近 N 月中出现 ≥ N-1 个月的分类）——
    all_cats = set().union(*month_cats.values()) if month_cats else set()
    recurring: list[dict] = []
    for nm in all_cats:
        appear = sum(1 for k in month_labels if nm in month_cats[k])
        if appear >= max(2, months - 1):
            amts = [month_cat_amt[k].get(nm, Decimal("0")) for k in month_labels]
            avg = sum(amts, Decimal("0")) / months
            recurring.append({
                "name": nm, "appear_months": appear,
                "avg_monthly": _money(avg), "currency": base_currency,
                "months": [_money(a) for a in amts],
            })
    recurring.sort(key=lambda x: Decimal(x["avg_monthly"]), reverse=True)

    # —— 异常波动（本月 vs 上月，分类维度）——
    anomalies: list[dict] = []
    if len(month_labels) >= 2:
        cur_m, prev_m = month_labels[-1], month_labels[-2]
        union = set(month_cat_amt[cur_m]) | set(month_cat_amt[prev_m])
        for nm in union:
            c = month_cat_amt[cur_m].get(nm, Decimal("0"))
            p = month_cat_amt[prev_m].get(nm, Decimal("0"))
            if c <= 0:
                continue
            if p <= 0:
                anomalies.append({"name": nm, "current": _money(c), "previous": _money(p),
                                  "change_pct": None, "kind": "new", "currency": base_currency})
            else:
                chg = (c - p) / p * 100
                if chg >= 50:
                    anomalies.append({"name": nm, "current": _money(c), "previous": _money(p),
                                      "change_pct": round(float(chg), 1), "kind": "up",
                                      "currency": base_currency})
        anomalies.sort(key=lambda x: Decimal(x["current"]), reverse=True)

    # —— 大额消费（本期单笔 Top 5）——
    large = sorted(cur_rows, key=lambda r: _dec(r[1]), reverse=True)[:5]
    large_items = [{
        "title": r[4] or "（无标题）", "amount": _money(_dec(r[1])), "currency": r[2],
        "occurred_at": local_iso(r[0]) if r[0] else None,
        "category": _cname(r[3]), "counterparty": r[5],
    } for r in large]

    # —— 高频分类（本期笔数 Top）——
    freq_items = [{"name": n, "count": c} for n, c in
                  sorted(cat_cnt.items(), key=lambda x: x[1], reverse=True)[:5]]

    panels = [
        {"key": "category", "title": "分类占比", "kind": "pie",
         "data": [{"name": n, "value": _money(v)} for n, v in cat_items[:8]]},
        {"key": "trend", "title": f"月度支出趋势（近 {months} 个月）", "kind": "line",
         "data": {"labels": [f"{k[5:]}月" for k in month_labels],
                  "series": [{"name": "支出",
                              "values": [_money(month_totals[k]) for k in month_labels]}]}},
    ]

    conclusions: list[str] = []
    if total > 0 and cat_items:
        top_name, top_val = cat_items[0]
        conclusions.append(
            f"本期支出合计 {_money(total)} {base_currency}，「{top_name}」占比最高（{_pct(top_val, total)}）。"
        )
    else:
        conclusions.append("本期暂无已确认支出。")
    if len(month_labels) >= 2:
        prev_v, cur_v = month_totals[month_labels[-2]], month_totals[month_labels[-1]]
        if prev_v > 0:
            chg = (cur_v - prev_v) / prev_v * 100
            conclusions.append(f"本月支出较上月{'上升' if chg >= 0 else '下降'} {abs(chg):.1f}%。")
    if anomalies:
        conclusions.append(f"有 {len(anomalies)} 个分类支出环比明显上升（≥50%），建议核对是否有非经常性大额支出。")
    if recurring:
        conclusions.append(f"识别到 {len(recurring)} 类周期性固定支出（每月出现），可用于预算与储蓄规划。")

    return {
        "period": period,
        "period_label": period_label,
        "base_currency": base_currency,
        "data_updated_at": utc_iso(datetime.now(timezone.utc)),
        "source_count": count,
        "months": months,
        "caliber": ("仅统计已确认消费类支出（不含贷款偿还、信用卡还款、转账手续费等非消费性支出）；"
                    "多币种按币种分组、不做相加；周期性识别与环比按家庭时区切月。"),
        "stats": [
            {"label": "本期支出", "value": f"{_money_stat(total)} {base_currency}"},
            {"label": "支出笔数", "value": f"{count} 笔"},
            {"label": "笔均金额",
             "value": f"{_money(total / count if count else Decimal('0'))} {base_currency}"},
            {"label": "最多分类", "value": cat_items[0][0] if cat_items else "—"},
        ],
        "panels": panels,
        "habits": {
            "large_expenses": large_items,
            "anomalies": anomalies,
            "recurring": recurring,
            "frequent": freq_items,
        },
        "conclusions": conclusions,
    }
