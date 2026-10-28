"""页面级 AI 分析：按钮直接产出该页面的解读，不再跳转通用 AI 助手。

背景（用户反馈）：「AI 摘要」「AI 现金流分析」这类按钮点开后只是打开 AI 助手，
等于把「点按钮 → 再问一遍」的活儿又推回给用户。这里按页面范围（scope）就地生成：
1. **事实层**：确定性统计（余额、收支、结论），全部来自库内数据，模型不得编造；
2. **解读层**：把事实串成「结论 / 关注 / 建议」三行，模型不可用时仍展示事实。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.context import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.timeutil import local_iso, local_now
from app.db.models.business import BusinessEvent
from app.services.stats import EXPENSE_EVENT_TYPES, INCOME_EVENT_TYPES

# scope → (页面名, 这段分析要回答的问题)
SCOPES: dict[str, tuple[str, str]] = {
    "dashboard": ("脉枢台", "家庭整体财务：净资产、收支结余、负债压力与待办"),
    "cashflow": ("现金流", "账户余额结构、近半年每月流入流出、结余是否稳定"),
    "accounts": ("账户", "账户余额结构、闲置资金与信贷占用"),
    "assets": ("资产", "资产结构与估值变化"),
    "liabilities": ("负债", "负债规模、月供压力与利率成本"),
    "investments": ("投资", "持仓盈亏与集中度"),
    "leases": ("租赁", "租金收入、空置与到期租约"),
    "credit_cards": ("信用卡", "待还金额、免息期与还款安排"),
    "spending": ("消费习惯", "支出结构、大额与异常消费"),
    "insights": ("综合脉诊", "各维度结论汇总与优先级"),
    "lease": ("租约体检", "关键信息是否齐全、合同条款有没有缺陷、押金与收租是否落地"),
}


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value) -> str:
    return f"{_dec(value):.2f}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


async def _sum_events(
    db: AsyncSession, household_id: str, currency: str,
    event_types: tuple[str, ...], start: datetime, end: datetime,
) -> Decimal:
    value = (await db.execute(
        select(func.coalesce(func.sum(BusinessEvent.amount), 0)).where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.status == "confirmed",
            BusinessEvent.event_type.in_(event_types),
            BusinessEvent.currency == currency,
            BusinessEvent.occurred_at >= start,
            BusinessEvent.occurred_at < end,
        )
    )).scalar_one()
    return _dec(value)


async def _account_snapshot(
    db: AsyncSession, user: CurrentUser, currency: str,
) -> tuple[int, list[dict], Decimal]:
    """账户数量、按类型分组的余额（仅基础币种），以及余额合计。"""
    from app.services import accounts as account_service

    rows = await account_service.list_accounts(db, user)
    groups: dict[str, dict] = {}
    total = Decimal("0")
    count = 0
    for account, balance_value in rows:
        # 跨币种不相加：只统计基础币种的账户，外币单独在账户页看
        if (account.currency or "CNY") != currency:
            continue
        balance = _dec(balance_value)
        count += 1
        total += balance
        key = enum_zh.zh("account_type", account.account_type) or account.account_type or "其他"
        group = groups.setdefault(key, {"type": key, "balance": Decimal("0"), "count": 0})
        group["balance"] += balance
        group["count"] += 1
    ordered = sorted(groups.values(), key=lambda g: abs(g["balance"]), reverse=True)
    for g in ordered:
        g["balance"] = _money(g["balance"])
    return count, ordered, total


async def _monthly_flow(
    db: AsyncSession, household_id: str, currency: str, months: int = 6,
) -> list[dict]:
    """近 N 个月（含本月）的收入 / 支出 / 结余，按月正序。"""
    now = local_now()
    out: list[dict] = []
    for i in range(months - 1, -1, -1):
        year, month = _shift_month(now.year, now.month, -i)
        start = datetime(year, month, 1)
        end = datetime(*_shift_month(year, month, 1), 1)
        income = await _sum_events(db, household_id, currency, INCOME_EVENT_TYPES, start, end)
        expense = await _sum_events(db, household_id, currency, EXPENSE_EVENT_TYPES, start, end)
        out.append({
            "month": f"{year}-{month:02d}",
            "income": _money(income),
            "expense": _money(expense),
            "net": _money(income - expense),
        })
    return out


def _fact(label: str, value: str, hint: str = "") -> dict:
    return {"label": label, "value": value, "hint": hint}


async def _collect_facts(
    db: AsyncSession, user: CurrentUser, scope: str, period: str,
    target_id: str | None = None,
) -> tuple[list[dict], list[str], list[dict]]:
    """返回（展示用指标, 给模型的事实条目, 规则检查结果）。"""
    if scope == "lease":
        return await _lease_facts(db, user, target_id)

    from app.services import insights as insights_service

    ov = await insights_service.insight_overview(db, user, period)
    currency = ov.get("base_currency") or "CNY"
    facts: list[dict] = []
    lines: list[str] = []
    checks: list[dict] = []

    for c in ov.get("conclusions", [])[:8]:
        text = c.get("highlight") or c.get("summary") or ""
        if text:
            lines.append(f"- {c['title']}：{text}")
        # 结论里的数值就是最该展示的指标（净资产/收入/支出/负债…），直接拿来做卡片
        if c.get("highlight"):
            facts.append(_fact(c["title"], c["highlight"]))

    # 账户与现金流是「现金流 / 账户」页特有的补充指标：
    # 脉枢台已经有净资产等整体指标，不再重复列账户结构。
    if scope in ("cashflow", "accounts"):
        # 这两页只展示账户与月度流水，净资产/资产结构放在脉枢台与资产页看
        facts = []
        count, groups, total = await _account_snapshot(db, user, currency)
        facts.append(_fact("账户数", f"{count} 个"))
        facts.append(_fact("账户余额合计", f"{_money(total)} {currency}"))
        lines.append(f"- 共有 {count} 个{currency}账户，余额合计 {_money(total)} {currency}。")
        if groups:
            top = "、".join(f"{g['type']} {g['balance']}" for g in groups[:4])
            facts.append(_fact("余额结构", top))
            lines.append(f"- 账户余额结构：{top}。")

        flow = await _monthly_flow(db, user.household_id or "", currency, months=6)
        if flow:
            this_month = flow[-1]
            facts.append(_fact(
                f"{this_month['month']} 流入", f"{this_month['income']} {currency}"))
            facts.append(_fact(
                f"{this_month['month']} 流出", f"{this_month['expense']} {currency}"))
            facts.append(_fact(
                f"{this_month['month']} 结余", f"{this_month['net']} {currency}"))
            seq = "；".join(f"{f['month']} 收入 {f['income']}、支出 {f['expense']}、结余 {f['net']}"
                            for f in flow)
            lines.append(f"- 近 {len(flow)} 个月现金流：{seq}。")
            nets = [_dec(f["net"]) for f in flow]
            negative = sum(1 for n in nets if n < 0)
            lines.append(f"- 其中 {negative} 个月结余为负，"
                         f"月度结余均值 {_money(sum(nets) / len(nets))} {currency}。")

    # 流动资金与还款覆盖：脉枢台 / 现金流 / 账户三个页面都展示（与还款直接相关）
    if scope in ("dashboard", "cashflow", "accounts"):
        await _append_liquidity_facts(db, user, currency, facts, lines, checks)
        await _append_employment_facts(db, user, currency, facts, lines, checks)

    return facts, lines, checks


async def _append_liquidity_facts(
    db: AsyncSession, user: CurrentUser, currency: str,
    facts: list[dict], lines: list[str], checks: list[dict],
) -> None:
    """流动资金 + 偿债覆盖分析（cashflow / accounts scope）。

    流动资金 = 现金类账户余额 + 投资账户现金，**不扣**信用卡待还与负债；
    能不能还上由「偿债覆盖」回答：流动资金 ÷（信用卡待还 + 未来 30 天负债应还）。
    """
    from app.services import stats as stats_service

    snap = await stats_service.dashboard_snapshot(db, user, "current_month", currency)
    liquidity = snap.get("liquidity") or {}
    liq_value = _dec(liquidity.get("value", 0))
    cash_accounts = liquidity.get("cash_accounts", liquidity.get("account_cash", "0"))

    facts.append(_fact("流动资金", f"{_money(liq_value)} {currency}",
                       "现金类账户 + 投资账户现金，不含信用卡待还"))
    lines.append(f"- 流动资金 {_money(liq_value)} {currency}"
                 f"（现金类账户 {cash_accounts} + 投资现金 {liquidity.get('invest_cash', '0')}；不含信用卡待还）。")

    # 生存跑道（应急储备月数）：收入中断时现有流动资金能维持几个月
    runway_raw = liquidity.get("runway_months")
    monthly_expense = _dec(liquidity.get("monthly_expense", 0))
    if runway_raw is not None:
        runway = _dec(runway_raw)
        level = liquidity.get("runway_level")
        facts.append(_fact("生存跑道", f"{runway:.1f} 个月",
                           f"流动资金 ÷ 近 3 个月月均支出 {_money(monthly_expense)}"))
        if level == "bad":
            lines.append(
                f"- 生存跑道仅 {runway:.1f} 个月（月均支出 {_money(monthly_expense)} {currency}），"
                "低于 3 个月安全线，抗风险能力弱。")
            checks.append({"level": "warn", "title": "应急储备不足",
                           "detail": f"流动资金仅够 {runway:.1f} 个月支出，建议至少储备 6 个月"})
        elif level == "warn":
            lines.append(
                f"- 生存跑道 {runway:.1f} 个月，介于 3–6 个月之间，抗风险能力偏紧。")
            checks.append({"level": "info", "title": "应急储备偏紧",
                           "detail": f"流动资金可支撑 {runway:.1f} 个月，建议补足到 6 个月"})
        else:
            lines.append(f"- 生存跑道 {runway:.1f} 个月，应急储备充足。")
    else:
        lines.append("- 尚无足够支出记录，暂无法测算生存跑道。")
        if liq_value <= 0:
            checks.append({"level": "warn", "title": "流动资金见底",
                           "detail": "现金类账户与投资现金合计为负或为零，需优先安排资金调度"})

    # 资产负债率
    nw = snap.get("net_worth") or {}
    ratio_raw = nw.get("debt_ratio")
    if ratio_raw is not None:
        ratio = _dec(ratio_raw)
        ratio_level = nw.get("debt_ratio_level")
        facts.append(_fact("资产负债率", f"{ratio:.1f}%",
                           "负债剩余本金 ÷ 总资产；≤40% 健康、40–60% 关注、>60% 偏高"))
        if ratio_level == "bad":
            lines.append(f"- 资产负债率 {ratio:.1f}%，超过 60%，杠杆偏高。")
            checks.append({"level": "warn", "title": "资产负债率偏高",
                           "detail": f"负债占总资产 {ratio:.1f}%，建议控制新增负债"})
        elif ratio_level == "warn":
            lines.append(f"- 资产负债率 {ratio:.1f}%，处于 40–60% 的关注区间。")
        else:
            lines.append(f"- 资产负债率 {ratio:.1f}%，杠杆水平健康。")

    # 偿债能力：流动资金不扣待还，用覆盖倍数判断够不够
    debt = snap.get("debt_service") or {}
    credit_due = _dec(debt.get("credit_card_due", 0))
    liab_due = _dec(debt.get("liability_due_30d", 0))
    total_due = _dec(debt.get("total_due", 0))

    if total_due > 0:
        if credit_due > 0:
            facts.append(_fact("信用卡待还", f"{_money(credit_due)} {currency}",
                               "信用类账户欠款合计（不冲减流动资金）"))
        if liab_due > 0:
            facts.append(_fact("未来 30 天负债应还", f"{_money(liab_due)} {currency}",
                               "各负债月供合计"))
        ratio_raw = debt.get("coverage_ratio")
        coverage = _dec(debt.get("coverage_value", 0))
        ratio = _dec(ratio_raw) if ratio_raw is not None else None
        ratio_text = f"{ratio:.2f}" if ratio is not None else "—"
        level = debt.get("coverage_level")
        facts.append(_fact("偿债覆盖", f"{ratio_text} 倍",
                           f"流动资金 ÷ 待还合计 {_money(total_due)}"))
        lines.append(
            f"- 待还合计 {_money(total_due)} {currency}"
            f"（信用卡 {_money(credit_due)} + 负债月供 {_money(liab_due)}），"
            f"流动资金可覆盖 {ratio_text} 倍。")
        if level == "bad":
            lines.append(
                f"- 流动资金不足以覆盖待还合计，缺口约 {_money(-coverage)} {currency}，"
                "需提前备款或调整还款安排。")
            checks.append({"level": "warn", "title": "偿债覆盖不足",
                           "detail": f"流动资金 {_money(liq_value)} < 待还合计 {_money(total_due)}"})
        elif level == "warn":
            lines.append(
                f"- 偿债覆盖偏紧（{ratio_text} 倍，1–1.5 倍区间），还完款后手头只剩约 "
                f"{_money(coverage)} {currency}，建议留出缓冲。")
            checks.append({"level": "info", "title": "偿债覆盖偏紧",
                           "detail": f"覆盖 {ratio_text} 倍，建议保持 1.5 倍以上"})
        else:
            lines.append(f"- 偿债覆盖从容（{ratio_text} 倍），还完款后仍余约 {_money(coverage)} {currency}。")
    else:
        lines.append("- 目前没有信用卡待还与负债月供，流动资金无还款压力。")


async def _append_employment_facts(
    db: AsyncSession, user: CurrentUser, currency: str,
    facts: list[dict], lines: list[str], checks: list[dict],
) -> None:
    """工作档案分析（在职状态 → 现金流风险预警；社保公积金作为隐性资产提示）。"""
    from app.services import employment as employment_service

    profiles = await employment_service.list_profiles(db, user)
    if not profiles:
        return

    status_count: dict[str, int] = {}
    for p in profiles:
        status_count[p.employment_status] = status_count.get(p.employment_status, 0) + 1

    labels = {s: enum_zh.zh("employment_status", s) for s in status_count}
    summary = "、".join(f"{labels[s]} {status_count[s]} 人" for s in status_count)
    facts.append(_fact("在职状态", summary, "工作档案登记的家庭成员在职情况"))

    unemployed = status_count.get("unemployed", 0)
    if unemployed > 0:
        lines.append(
            f"- 有 {unemployed} 名成员处于「失业」状态，工资性收入可能中断，"
            "现金流风险上升，需关注应急储备。")
        checks.append({"level": "warn", "title": "存在失业成员",
                       "detail": f"{unemployed} 名成员失业，建议确认替代收入来源或补足应急储备"})
    else:
        lines.append(f"- 家庭成员在职状态：{summary}，工资性收入相对稳定。")

    if status_count.get("probation"):
        lines.append("- 有成员处于试用期，短期内收入稳定性偏低。")

    si_total = Decimal("0")
    hf_total = Decimal("0")
    for p in profiles:
        si_total += _dec(p.social_insurance_base or 0)
        hf_total += _dec(p.housing_fund_base or 0)
    if si_total > 0 or hf_total > 0:
        parts = []
        if si_total > 0:
            parts.append(f"社保基数合计 {_money(si_total)}")
        if hf_total > 0:
            parts.append(f"公积金基数合计 {_money(hf_total)}")
        facts.append(_fact("社保公积金基数", " + ".join(parts),
                           "隐性福利，提示真实薪酬包（不参与净资产计算）"))
        lines.append(
            f"- 社保公积金基数：{'、'.join(parts)}，是工资之外的隐性福利，"
            "不计入净资产，但可作「真实薪酬包」参考。")


async def _lease_facts(
    db: AsyncSession, user: CurrentUser, lease_id: str | None,
) -> tuple[list[dict], list[str], list[dict]]:
    """租约体检：先做确定性核对（缺项、条款矛盾、押金与收租是否落地），再交给模型点评。

    检查全部基于库内数据，模型不可用时也能给出结论；模型只负责把问题串成人话。
    """
    from app.db.models.business import Asset
    from app.db.models.leases import LeaseDeposit, LeaseReceivable
    from app.services import asset_extensions as ext_service
    from app.services import leases as lease_service

    if not lease_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "租约体检需要指定租约",
                       field_errors={"target_id": "请传入租约 id"})
    lease = await lease_service.get_lease(db, user, lease_id)
    asset = (await db.execute(
        select(Asset).where(Asset.id == lease.real_estate_id))).scalars().first()
    receivables = list((await db.execute(
        select(LeaseReceivable).where(
            LeaseReceivable.lease_id == lease.id,
            LeaseReceivable.deleted_at.is_(None),
        )
    )).scalars().all())
    deposits = list((await db.execute(
        select(LeaseDeposit).where(
            LeaseDeposit.lease_id == lease.id,
            LeaseDeposit.deleted_at.is_(None),
        )
    )).scalars().all())
    try:
        utilities = await ext_service.list_utility_accounts(db, user, lease.real_estate_id)
    except Exception:  # noqa: BLE001 房产已删除/无权限时不阻断体检
        utilities = []

    checks: list[dict] = []

    def add(level: str, title: str, detail: str) -> None:
        checks.append({"level": level, "title": title, "detail": detail})

    rent = _dec(lease.monthly_rent)
    deposit_amount = _dec(lease.deposit_amount)
    held = [d for d in deposits if d.status == "held"]
    pending = [r for r in receivables if r.status == "pending"]
    today = local_now()

    if rent <= 0:
        add("error", "月租未填写", "月租为 0，收租计划、逾期判断和收租率都无法成立。")
    if not lease.start_date:
        add("warn", "缺少起租日", "没有起租日，无法判断当前是否在租期内。")
    if not lease.end_date:
        add("warn", "缺少到期日", "没有到期日，续租提醒与到期预警无法生成。")
    if lease.start_date and lease.end_date and lease.end_date <= lease.start_date:
        add("error", "租期顺序异常", "到期日不晚于起租日，合同期限条款不成立。")
    if not (lease.tenant_contact or "").strip():
        add("warn", "缺少租客联系方式", "催收、退租验收和紧急联络都会找不到人。")
    if deposit_amount <= 0:
        add("warn", "未约定押金", "没有押金，欠费与破损赔偿缺少抵扣来源。")
    elif rent > 0 and deposit_amount < rent:
        add("info", "押金低于一个月租金",
            f"押金 {deposit_amount:.2f} 低于月租 {rent:.2f}，违约与欠费覆盖不足。")
    if not lease.receiving_account_id:
        add("warn", "未关联收款账户", "登记实收时无法自动带出到账账户。")
    if not lease.property_repair_bearer:
        add("info", "未约定维修承担方", "维修费用由谁承担没写明，退租时容易扯皮。")
    if not receivables:
        add("warn", "没有收租计划", "还没生成任何一期应收，下一期收租日显示为空。")
    elif not pending:
        add("info", "收租计划已全部结清", "所有应收都已确认收款，记得按新周期继续生成。")
    if not held and lease.deposit_received:
        add("info", "押金只做了标记", "勾选了已收押金但没登记到账账户，账户余额里没有这笔钱。")
    if not held and not lease.deposit_received:
        add("warn", "押金未收", "押金既没有登记到账，也没有标记已收。")
    if lease.end_date and lease.end_date < today and lease.status == "active":
        add("warn", "合同已到期仍在生效", "到期日已过，但没有续租或退租，租约状态需要更新。")
    if lease.start_date and lease.end_date:
        days = (lease.end_date - lease.start_date).days
        if days < 30:
            add("warn", "租期过短", f"整份租期只有 {days} 天，注意是否符合短租约定。")
        elif days > 3650:
            add("info", "租期超过 10 年", "超长租期建议写明调价机制与提前解约条款。")
    if not utilities:
        add("info", "未登记水电煤户号", "退租结算水电煤欠费时缺少户号依据。")

    facts = [
        _fact("租客", lease.tenant_name),
        _fact("房产", asset.name if asset is not None else "未关联房产"),
        _fact("月租", f"{rent:.2f} {lease.currency}"),
        _fact("押金", f"{deposit_amount:.2f} {lease.currency}"),
        _fact("租期", f"{local_iso(lease.start_date)[:10] if lease.start_date else '—'}"
                      f" ~ {local_iso(lease.end_date)[:10] if lease.end_date else '—'}"),
        _fact("待收期数", f"{len(pending)} 期 / "
                          f"{sum((_dec(r.amount) for r in pending), Decimal('0')):.2f} {lease.currency}"),
        _fact("押金状态", "已入账" if held else ("仅标记已收" if lease.deposit_received else "未收")),
        _fact("水电煤户号", f"{len(utilities)} 个"),
    ]

    lines = [f"- [{c['title']}] {c['detail']}" for c in checks]
    lines.append(f"- 租约状态：{enum_zh.zh('lease_status', lease.status)}；"
                 f"租客 {lease.tenant_name}；月租 {rent:.2f}；押金 {deposit_amount:.2f}。")
    if not checks:
        lines.append("- 关键信息与常见条款均已覆盖，未发现明显缺项。")
    return facts, lines, checks


def _build_prompt(scope: str, lines: list[str]) -> str:
    title, focus = SCOPES[scope]
    if scope == "lease":
        body = "\n".join(lines) if lines else "- 暂未发现明显问题"
        return (
            "你是家庭租房合同审核助手，现在要根据系统对「一份租约」的检查结果做点评。\n"
            f"关注点：{focus}。\n\n以下是系统核对出的问题与事实（只能引用这些，不要编造）：\n"
            f"{body}\n\n"
            "请严格按下面三行输出，每行以给定词开头，不要标题、不要多余内容：\n"
            "结论：这份租约整体是否规范，一句话；\n"
            "关注：最可能带来纠纷或损失的一处；\n"
            "建议：一条具体的修改或补录动作。"
        )
    body = "\n".join(lines) if lines else "- （当前没有可用的统计数据）"
    return (
        f"你是家庭记账系统的分析助手，现在要针对「{title}」页面做解读，"
        f"关注点是：{focus}。\n\n以下是系统统计出的事实（只能引用这些数字，不要编造）：\n"
        f"{body}\n\n"
        "请严格按下面三行输出，每行以给定词开头，不要标题、不要多余内容：\n"
        "结论：一句话说清当前状况；\n"
        "关注：最需要留意的一个问题；\n"
        "建议：一条今天就能执行的具体动作。"
    )


def _parse_bullets(answer: str) -> list[dict]:
    """把「结论 / 关注 / 建议」三行拆成结构化条目。"""
    marks = {"结论": "结论", "关注": "关注", "建议": "建议"}
    bullets: list[dict] = []
    for raw in (answer or "").splitlines():
        text = raw.strip().lstrip("-•* ").strip()
        if not text:
            continue
        for key, label in marks.items():
            if text.startswith(f"{key}：") or text.startswith(f"{key}:"):
                body = text[len(key) + 1:].strip()
                if body:
                    bullets.append({"label": label, "text": body})
                break
    return bullets


def _plain_summary(facts: list[dict], checks: list[dict]) -> str | None:
    """把关键事实串成一段「给家人看」的人话。

    与模型解读互补：模型不可用时这段话照样输出，模型可用时它作为开场白，
    避免一上来就是一堆指标。语气平实、不制造焦虑。
    """
    values = {f["label"]: f["value"] for f in facts}
    parts: list[str] = []

    net = values.get("净资产")
    if net:
        parts.append(f"家里净资产 {net}")
    liq = values.get("流动资金")
    if liq:
        parts.append(f"能马上动用的流动资金 {liq}")
    runway = values.get("生存跑道")
    if runway:
        parts.append(f"按现在的花钱速度够撑 {runway}")
    cover = values.get("还款覆盖")
    if cover:
        parts.append(f"把近 30 天的还款扣掉还剩 {cover}")
    ratio = values.get("资产负债率")
    if ratio:
        parts.append(f"负债率 {ratio}")

    if not parts:
        return None

    text = "，".join(parts) + "。"
    if checks:
        text += "需要留意的是：" + "、".join(c["title"] for c in checks) + "。"
    else:
        text += "整体没有发现明显缺口。"
    return text


async def analyze(
    db: AsyncSession, user: CurrentUser, ctx, scope: str = "dashboard",
    period: str = "current_month", target_id: str | None = None,
) -> dict:
    """按页面范围生成 AI 解读；模型不可用时返回事实 + 明确原因，前端照样能展示。"""
    if scope not in SCOPES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "不支持的分析范围",
                       field_errors={"scope": f"可选值：{', '.join(SCOPES)}"})

    title, focus = SCOPES[scope]
    if scope == "lease" and not target_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "租约体检需要指定租约",
                       field_errors={"target_id": "请传入租约 id"})
    facts, lines, checks = await _collect_facts(db, user, scope, period, target_id)
    now = local_now()
    base = {
        "scope": scope,
        "scope_label": title,
        "focus": focus,
        "facts": facts,
        "checks": checks,
        "plain_summary": _plain_summary(facts, checks),
        "period": period,
        "generated_at": local_iso(now),
    }

    if not lines:
        return {**base, "state": "unavailable", "summary": None, "bullets": [],
                "hint": "当前没有可分析的数据。" if checks else "当前没有可分析的统计数据，先记几笔再来看看。",
                "model_type": ""}

    from app.services import ai as ai_service

    try:
        result = await ai_service._model_answer(  # noqa: SLF001
            db, user, _build_prompt(scope, lines), title, ctx)
    except Exception as exc:  # noqa: BLE001
        return {**base, "state": "unavailable", "summary": None, "bullets": [],
                "hint": f"模型暂不可用（{str(exc)[:120]}）。上方指标仍为确定性统计结果。",
                "model_type": ""}

    if not result or not result.get("answer"):
        return {**base, "state": "unavailable", "summary": None, "bullets": [],
                "hint": "本地与第三方模型均未连接，可到「设置 → 本地 AI」检查。",
                "model_type": ""}

    answer = str(result["answer"]).strip()
    bullets = _parse_bullets(answer)
    return {
        **base,
        "state": "ready",
        "summary": None if bullets else answer,
        "bullets": bullets,
        "hint": "",
        "model_type": result.get("model_type", ""),
        "provider": result.get("provider"),
    }
