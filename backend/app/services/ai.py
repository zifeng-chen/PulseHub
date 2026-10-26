"""本地/第三方 AI 服务（09、07 §11、18 §4）。

优先级（09 §5.1）：权限检查 → 确定性工具 → 本地 AI → 可选第三方 AI。
- 确定性问答：净资产/收入/支出/负债/投资/租赁/资产等常见问题，直接用脉诊服务的
  确定性统计作答，**不依赖任何模型**，保证 AI 未配置时助手仍可用。
- 模型问答：仅当本地/第三方 AI 已配置并启用时才发起；注入脱敏后的确定性数据上下文，
  系统提示强制「只分析、不编造、不直接写业务表」。
- 第三方外发：写审计（external_provider）。
- 每次回答都带 AI 可信度展示（07 §11 / 09 §6）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser
from app.core.ids import utc_iso
from app.core.errors import AppError, ErrorCode

CONCISION_TYPES = {
    "fact": "事实", "computed": "计算", "inference": "推断", "suggestion": "建议",
}

# 关键词 → 脉诊主题（用于确定性问答）
_TOPIC_KEYWORDS = [
    ("net_worth", ["净资产", "净值", "身家", "有多少钱", "资产减负债"]),
    ("income", ["收入", "进账", "赚钱", "工资", "收租"]),
    ("expenses", ["支出", "消费", "花了", "开销", "花钱", "成本"]),
    ("liabilities", ["负债", "还款", "房贷", "车贷", "信用卡", "贷款", "利息", "压力"]),
    ("investments", ["投资", "持仓", "股票", "基金", "盈亏", "损益", "市值"]),
    ("leases", ["租赁", "租金", "收租", "押金", "逾期", "房租", "租约"]),
    ("assets", ["资产", "不动产", "房产", "车位", "车辆", "贵金属", "估值"]),
    # 放在最后：避免"余额/账户"抢走上面的专业话题
    ("accounts", ["账户", "几张卡", "几个卡", "卡里", "余额", "存款", "几张信用卡"]),
]


# 分析/建议类意图：必须交给模型，不能拿确定性统计硬答
_ANALYTIC_HINTS = (
    "为什么", "原因", "怎么", "建议", "分析", "对比", "比较", "规划", "是否",
    "能不能", "要不要", "帮我", "看看", "解释", "推荐", "方案", "风险", "优化",
    "趋势", "预测", "值得", "应该", "合理", "评价", "总结", "怎么办", "怎么看",
)
# 纯数据查询特征：只有这类问题才适合用确定性统计直接回答
_DATA_QUERY_HINTS = (
    "多少", "几个", "几笔", "几张", "几条", "余额", "总额", "合计", "总共",
    "是多少", "有没有", "列出", "明细", "占比", "排名",
)


def _should_use_deterministic(question: str, topic: str | None, model_type: str) -> bool:
    """只有「自动模式 + 纯数据查询」才走确定性回答。

    - 用户显式选了本地/第三方模型：一律交给模型（否则切换模型等于没效果）
    - 问题含分析/建议意图（为什么/怎么/建议…）：交给模型，不用统计模板硬答
    """
    if topic is None:
        return False
    if (model_type or "auto").strip().lower() != "auto":
        return False
    q = (question or "").strip()
    if not q:
        return False
    if any(h in q for h in _ANALYTIC_HINTS):
        return False
    if len(q) <= 16:
        return True
    return any(h in q for h in _DATA_QUERY_HINTS)


def _detect_topic(question: str) -> str | None:
    q = question or ""
    for topic, keywords in _TOPIC_KEYWORDS:
        if any(k in q for k in keywords):
            return topic
    return None


async def _deterministic_answer(db: AsyncSession, user: CurrentUser, topic: str) -> dict:
    """确定性问答：直接用脉诊服务的数据作答，返回 answer + sources + summary。"""
    from app.services import insights as insights_service

    if topic == "net_worth":
        ov = await insights_service.insight_overview(db, user, "current_month")
        nw = next(c for c in ov["conclusions"] if c["key"] == "net_worth")
        answer = (
            f"当前净资产 {nw['highlight']}。\n"
            f"{nw['summary']}。\n"
            f"口径：{nw['caliber']}。"
        )
        sources = [c["summary"] for c in ov["conclusions"][:4]]
        return {"answer": answer, "sources": sources, "topic": "净资产"}

    if topic == "accounts":
        # 「我有几个账户 / 卡里还剩多少」这类问题不需要模型，直接按账本回答
        from app.core.money import fmt_amount
        from app.services import accounts as account_service

        rows = await account_service.list_accounts(db, user)
        if not rows:
            return {"answer": "当前还没有登记任何账户，可以在「家庭台账 → 账户」新增。",
                    "sources": [], "topic": "账户"}

        normal_lines: list[str] = []
        credit_lines: list[str] = []
        for acc, bal in rows:
            if acc.credit_limit is not None:
                used = max(Decimal("0"), -bal)
                avail = Decimal(str(acc.credit_limit)) - used
                credit_lines.append(
                    f"- {acc.name}（信用类）：待还 {fmt_amount(used)}，"
                    f"可用 {fmt_amount(avail)}，额度 {fmt_amount(acc.credit_limit)}"
                )
            else:
                normal_lines.append(f"- {acc.name}：余额 {fmt_amount(bal)}")

        head = f"当前共 {len(rows)} 个账户"
        if credit_lines:
            head += f"（其中信用类 {len(credit_lines)} 个）"
        answer = head + "：\n" + "\n".join(normal_lines + credit_lines)
        answer += "\n\n口径：余额 = 期初 + 已确认资金变动；信用类余额为负表示欠款。"
        sources = (normal_lines + credit_lines)[:6]
        return {"answer": answer, "sources": sources, "topic": "账户"}

    if topic == "assets":
        r = await insights_service.topic_insight(db, user, "assets")
    elif topic == "income":
        r = await insights_service.topic_insight(db, user, "income")
    elif topic == "expenses":
        r = await insights_service.topic_insight(db, user, "expenses")
    elif topic == "liabilities":
        r = await insights_service.topic_insight(db, user, "liabilities")
    elif topic == "investments":
        r = await insights_service.topic_insight(db, user, "investments")
    else:
        r = await insights_service.topic_insight(db, user, "leases")

    stat_lines = [f"{s['label']}：{s['value']}" for s in r["stats"]]
    answer = f"{r['title']}（确定性统计）：\n" + "\n".join(stat_lines) + "\n\n" + "\n".join(r["conclusions"])
    return {"answer": answer, "sources": stat_lines, "topic": r["title"]}


async def _trust_block(
    db: AsyncSession, user: CurrentUser, model_type: str, period: str = "current_month"
) -> dict:
    """AI 可信度展示（07 §11）：期间、币种、更新时间、来源数量、确认/草稿数、模型类型、脱敏、结论类型。"""
    from sqlalchemy import func, select

    from app.db.models.business import BusinessEvent
    from app.services.setup import current_household
    from app.services.stats import PERIODS, _counts

    household = await current_household(db)
    base_currency = household.base_currency if household else "CNY"
    counts = await _counts(db, user.household_id or "")
    model_labels = {
        "deterministic": "确定性规则",
        "local": "本地 AI",
        "third_party": "第三方 AI",
        "none": "未连接（未调用模型）",
    }
    return {
        "model_type": model_type,
        "model_type_label": model_labels.get(model_type, model_type),
        "base_currency": base_currency,
        "period": period,
        "period_label": PERIODS.get(period, period),
        "data_updated_at": utc_iso(datetime.now(timezone.utc)),
        "source_count": counts.get("confirmed", 0),
        "confirmed_count": counts.get("confirmed", 0),
        "draft_count": counts.get("draft", 0),
        "masked": True,
        "conclusion_types": CONCISION_TYPES,
    }


# ---------------- 敏感信息脱敏（第三方模型外发前必过）----------------
# 只脱标识类信息，金额/分类/机构保留——否则模型无法做财务分析。
_ID_CARD = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
_BANK_CARD = re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)")
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_VIN = re.compile(r"(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])")
_PLATE = re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][A-Z0-9]{5,6}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def mask_sensitive(text: str) -> str:
    """把证件号、卡号、手机号、VIN、车牌、邮箱替换为掩码。

    顺序有讲究：先身份证（18 位）再银行卡（16–19 位），否则身份证会被当成卡号。
    金额（含小数点的数字）不会被误伤。
    """
    if not text:
        return text
    text = _ID_CARD.sub(lambda m: f"{m.group(1)}********{m.group(2)}", text)
    text = _BANK_CARD.sub(lambda m: f"{m.group(1)} **** **** {m.group(2)}", text)
    text = _PHONE.sub(lambda m: f"{m.group(1)}****{m.group(2)}", text)
    text = _VIN.sub("[VIN 已脱敏]", text)
    text = _PLATE.sub("[车牌已脱敏]", text)
    text = _EMAIL.sub("[邮箱已脱敏]", text)
    return text


async def _finance_snapshot(db: AsyncSession, user: CurrentUser) -> str:
    """给模型注入的结构化事实：账户、资产、负债、投资、租赁与近期流水。

    数据范围是整个家庭账本，不是用户当前所在的页面。
    """
    from sqlalchemy import select

    from app.core import enums as enum_zh
    from app.core.money import fmt_amount
    from app.db.models.business import Asset, BusinessEvent
    from app.db.models.finance import InvestmentAccount, Liability
    from app.db.models.leases import Lease
    from app.services import accounts as account_service
    from app.services import investments as investment_service
    from app.services import liabilities as liability_service
    from app.services import stats as stats_service

    household_id = user.household_id or ""
    snap = await stats_service.dashboard_snapshot(db, user, "current_month")
    nw = snap["net_worth"]

    rows = await account_service.list_accounts(db, user)
    normal: list[str] = []
    credit: list[str] = []
    for acc, bal in rows:
        if acc.credit_limit is not None:
            used = max(Decimal("0"), -bal)
            avail = Decimal(str(acc.credit_limit)) - used
            credit.append(
                f"  · {acc.name}（信用类）：待还 {fmt_amount(used)}，可用 {fmt_amount(avail)}，"
                f"额度 {fmt_amount(acc.credit_limit)}"
            )
        else:
            normal.append(f"  · {acc.name}：余额 {fmt_amount(bal)}")

    lines = [f"账户：共 {len(rows)} 个" + (f"（信用类 {len(credit)} 个）" if credit else "")]
    lines.extend(normal)
    lines.extend(credit)

    # 资产
    assets = list((await db.execute(
        select(Asset).where(Asset.household_id == household_id, Asset.deleted_at.is_(None))
        .order_by(Asset.current_value.desc()).limit(12)
    )).scalars().all())
    lines.append(f"资产：共 {len(assets)} 项（按当前价值）")
    for a in assets:
        lines.append(
            f"  · {a.name}（{enum_zh.zh('asset_type', a.asset_type)}）："
            f"当前价值 {fmt_amount(a.current_value)}"
        )

    # 负债（剩余本金需要按还款记录计算）
    liabilities = list((await db.execute(
        select(Liability).where(Liability.household_id == household_id, Liability.deleted_at.is_(None))
    )).scalars().all())
    outstanding = await liability_service.outstanding_for(db, household_id, [l.id for l in liabilities])
    if liabilities:
        lines.append(f"负债：共 {len(liabilities)} 笔")
        for l in liabilities[:10]:
            out = outstanding.get(l.id, l.opening_outstanding if l.opening_outstanding is not None else l.initial_amount)
            extra = []
            if l.monthly_payment:
                extra.append(f"月供 {fmt_amount(l.monthly_payment)}")
            if l.interest_rate:
                extra.append(f"年利率 {l.interest_rate}%")
            lines.append(
                f"  · {l.name}：剩余本金 {fmt_amount(out)}"
                + (f"，{'，'.join(extra)}" if extra else "")
            )

    # 投资（市值与盈亏）
    accounts = list((await db.execute(
        select(InvestmentAccount).where(
            InvestmentAccount.household_id == household_id,
            InvestmentAccount.deleted_at.is_(None),
        ).limit(8)
    )).scalars().all())
    if accounts:
        lines.append(f"投资账户：共 {len(accounts)} 个")
        for acc in accounts:
            pf = await investment_service.portfolio_of(db, acc)
            lines.append(
                f"  · {acc.name}：总市值 {fmt_amount(pf['total_value'])}，"
                f"未实现盈亏 {fmt_amount(pf['unrealized_pnl'])}，"
                f"已实现盈亏 {fmt_amount(pf['realized_pnl'])}"
            )

    # 租赁
    leases = list((await db.execute(
        select(Lease).where(Lease.household_id == household_id, Lease.deleted_at.is_(None)).limit(8)
    )).scalars().all())
    if leases:
        lines.append(f"租赁：共 {len(leases)} 份租约")
        for l in leases:
            lines.append(
                f"  · 租客 {l.tenant_name}：月租 {fmt_amount(l.monthly_rent)}，"
                f"状态 {enum_zh.zh('lifecycle_status', l.status)}"
            )

    # 近期已确认流水
    events = list((await db.execute(
        select(BusinessEvent).where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.status == "confirmed",
        ).order_by(BusinessEvent.occurred_at.desc()).limit(15)
    )).scalars().all())
    if events:
        lines.append("最近 15 笔已确认流水：")
        for e in events:
            day = e.occurred_at.strftime("%m-%d") if e.occurred_at else "—"
            lines.append(
                f"  · {day} {e.title}：{fmt_amount(e.amount)}"
                f"（{enum_zh.zh('event_type', e.event_type)}）"
            )

    lines.append(
        f"净资产：{nw['net_worth']} = 账户 {nw['accounts']} + 资产 {nw['assets']}"
        f" + 投资 {nw['investments']} − 负债 {nw['total_liabilities']}"
    )
    return "\n".join(lines)


async def _model_answer(
    db: AsyncSession, user: CurrentUser, question: str, context: str | None, ctx,
    model_type: str = "auto", history: list[dict] | None = None,
) -> dict:
    """模型问答。

    model_type：
      - local：只用本地模型（不出网，不脱敏）
      - third_party：只用第三方模型（外发前强制脱敏）
      - auto（默认）：本地优先，本地不可用再走第三方（同样脱敏）
    """
    from app.adapters import build_adapters
    from app.services import audit as audit_service
    from app.services import insights as insights_service

    adapters = build_adapters(db, user.household_id)
    local = adapters["local_ai"]
    third = adapters["third_party_ai"]

    # 构建脱敏后的确定性数据上下文，供模型引用（不包含敏感字段明文）
    try:
        snapshot = await _finance_snapshot(db, user)
        ov = await insights_service.insight_overview(db, user, "current_month")
        conclusions = "\n".join(
            f"- {c['title']}：{c['highlight'] or c['summary']}" for c in ov["conclusions"]
        )
        data_ctx = f"{snapshot}\n\n脉诊结论：\n{conclusions}"
    except Exception:  # noqa: BLE001
        data_ctx = "（当前无可用数据快照）"

    system = (
        "你是「财资脉枢」（PulseHub）家庭财务平台的 AI 助手，服务当前登录的这个家庭。\n"
        "下方数据来自这个家庭自己的账本，是真实、权威的事实。\n"
        "规则：\n"
        "1. 所有金额、数量只能来自「家庭财务数据」，不得自行计算或编造。\n"
        "2. 你就在 FAM 平台内回答，不要建议用户「去 FAM 平台查看」或「联系客服」；"
        "数据里没有的项，直接说「当前数据中没有这一项」。\n"
        "3. 你只能做解释、分析与建议，不能写业务表、不能转账、删除或发送通知。\n"
        "4. 敏感字段（证件号、卡号、车牌、VIN）已被脱敏，回答中不得还原。\n"
        "5. 用简体中文，先给结论，再引用具体数字作为证据，最后给建议；"
        "控制在 200 字以内，不要输出思考过程。\n\n"
        "6. 数据范围永远是整个家庭账本（下方「家庭财务数据」），"
        "不要因为用户当前在某个页面就只看那一类数据；页面名仅用于理解语境。\n\n"
        f"用户当前浏览的页面：{context or '未指定'}（仅作参考，不代表数据范围）\n\n"
        f"家庭财务数据：\n{data_ctx}"
    )
    # 带入同一会话的最近几轮，保证追问（「那上个月呢」）能接上
    past = [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))[:800]}
        for m in (history or [])[-8:]
        if m.get("content")
    ]
    messages = [{"role": "system", "content": system}, *past,
                {"role": "user", "content": question}]

    mode = (model_type or "auto").strip().lower()
    if mode not in ("auto", "local", "third_party"):
        mode = "auto"

    errors: list[str] = []

    if mode in ("auto", "local"):
        local_status = await local.status()
        if local_status.status == "connected":
            try:
                answer = await local.complete(messages)
                return {"answer": answer, "model_type": "local", "provider": None, "masked": False}
            except Exception as exc:  # noqa: BLE001 - 连接/超时统一转为可读原因
                errors.append(f"本地模型调用失败（{local_status.detail}）：{str(exc)[:120]}")
        else:
            errors.append(f"本地模型未连接（{local_status.detail}）")

        if mode == "local":
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "；".join(errors), status_code=503)

    if mode in ("auto", "third_party"):
        third_status = await third.status()
        if third_status.status == "connected":
            # 第三方外发：强制脱敏，避免证件号/卡号/手机号/车牌出网
            safe_messages = [
                {"role": "system", "content": mask_sensitive(system)},
                *[{"role": m["role"], "content": mask_sensitive(m["content"])} for m in past],
                {"role": "user", "content": mask_sensitive(question)},
            ]
            answer, provider = await third.complete(safe_messages)
            await audit_service.record_audit(
                db, action="ai.third_party.complete", action_label="第三方 AI 外发分析",
                object_type="ai", object_id="chat",
                after={"provider": provider, "question": mask_sensitive(question)[:120], "masked": True},
                result="success", external_provider=provider, user=user, ctx=ctx,
            )
            await db.commit()
            return {"answer": answer, "model_type": "third_party", "provider": provider, "masked": True}

        errors.append(f"第三方模型未连接（{third_status.detail}）")
        if mode == "third_party":
            raise AppError(ErrorCode.PROVIDER_NOT_CONFIGURED, "；".join(errors), status_code=409)

    if errors:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "；".join(errors), status_code=503)
    return None


async def ai_chat(
    db: AsyncSession, user: CurrentUser, question: str, context: str | None = None, ctx=None,
    model_type: str = "auto", history: list[dict] | None = None,
) -> dict:
    """AI 对话主入口（09 §5.1）。

    model_type 决定用本地还是第三方模型；只有 auto + 纯数据查询才走确定性回答。
    history 为同一会话的最近若干轮消息，供模型理解追问。
    """
    question = (question or "").strip()
    if not question:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请输入问题", status_code=422)

    # 1) 确定性问答优先（无需模型）
    topic = _detect_topic(question)
    if _should_use_deterministic(question, topic, model_type):
        try:
            det = await _deterministic_answer(db, user, topic)
            trust = await _trust_block(db, user, "deterministic")
            return {
                "state": "ready",
                "state_label": "确定性回答",
                "answer": det["answer"],
                "message": f"基于确定性统计回答（{det['topic']}），无需模型。",
                "sources": det["sources"],
                "context": {"route": None, "entity_id": None, "period": "current_month"},
                "trust": trust,
            }
        except AppError:
            pass  # 确定性失败则落到模型路径

    # 2) 模型问答
    model_error: str | None = None
    try:
        model_result = await _model_answer(db, user, question, context, ctx, model_type, history)
    except AppError as exc:
        model_result = None
        model_error = getattr(exc, "message", None) or str(exc)

    if model_result is not None:
        trust = await _trust_block(db, user, model_result["model_type"])
        trust["provider"] = model_result.get("provider")
        return {
            "state": "ready",
            "state_label": "已连接",
            "answer": model_result["answer"],
            "message": f"由{trust['model_type_label']}生成。",
            "sources": [],
            "context": {"route": None, "entity_id": None, "period": "current_month"},
            "trust": trust,
        }

    # 3) 模型不可用：给出具体原因，而不是笼统的「网络异常」
    reason = model_error or "本地/第三方 AI 未连接。"
    return {
        "state": "model_unavailable",
        "state_label": "模型未连接",
        "answer": None,
        "message": f"{reason}\n"
                   "记账、台账、统计与历史查询不受影响。\n"
                   "财务数据类问题（净资产、收入、支出、负债、投资、租赁、资产）可继续用确定性统计直接回答；"
                   "也可在对话窗口切换到另一个模型，或前往「设置 → 本地 AI / 第三方 AI」完成配置。",
        "sources": [],
        "context": {"route": None, "entity_id": None, "period": "current_month"},
        "trust": await _trust_block(db, user, "none"),
    }
