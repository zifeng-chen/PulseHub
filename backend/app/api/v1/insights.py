"""脉诊 API（07、18 §4 脉枢台/脉诊）。M6 起返回真实统计结论与数据质量检查。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, Ctx, DbSession
from app.core.errors import list_payload, success
from app.services import insights as insights_service

router = APIRouter(prefix="/insights", tags=["脉诊"])

TOPICS = [
    ("overview", "综合脉诊"), ("assets", "资产分析"), ("income", "收入分析"),
    ("expenses", "消费分析"), ("liabilities", "负债分析"), ("investments", "投资分析"),
    ("leases", "租赁分析"),
]


@router.get("/overview", summary="综合脉诊")
async def overview(
    db: DbSession,
    user: CurrentUserDep,
    period: str = Query("current_month"),
    currency: str | None = Query(None),
) -> dict:
    data = await insights_service.insight_overview(db, user, period, currency)
    return success(data, message="统计完成")


@router.get("/ai-summary", summary="AI 综合摘要（按需生成，不阻塞概览）")
async def ai_summary(db: DbSession, ctx: Ctx, user: CurrentUserDep) -> dict:
    """用模型把确定性结论串成一段摘要。

    此前 overview 里的 ai_summary 被写死成 module_unavailable，
    即使 AI 已接通也一直显示不可用。
    """
    from app.services import ai as ai_service
    from app.services import insights as insights_service

    ov = await insights_service.insight_overview(db, user, "current_month")
    facts = "\n".join(
        f"- {c['title']}：{c['highlight'] or c['summary']}" for c in ov["conclusions"]
    )
    if not facts:
        return success({"state": "unavailable", "summary": None,
                        "hint": "当前没有可总结的财务结论。", "model_type": ""})

    prompt = (
        "请基于下面这份家庭财务事实，写一段 3-4 句的月度摘要："
        "先给整体结论，再指出最需要注意的一点，最后给一条可执行建议。"
        "只使用给定事实，不要编造数字。\n\n" + facts
    )

    try:
        result = await ai_service._model_answer(db, user, prompt, "脉诊概览", ctx)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        return success({
            "state": "unavailable", "summary": None,
            "hint": f"模型暂不可用（{str(exc)[:120]}）。财务结论仍以确定性统计为准。",
            "model_type": "",
        })

    if not result:
        return success({"state": "unavailable", "summary": None,
                        "hint": "本地与第三方模型均未连接。可前往「设置 → 本地 AI」检查。",
                        "model_type": ""})
    return success({
        "state": "ready",
        "summary": result["answer"],
        "hint": "",
        "model_type": result["model_type"],
        "provider": result.get("provider"),
    })


@router.get("/ai-analysis", summary="页面级 AI 分析（就地生成，不跳转对话）")
async def ai_analysis(
    db: DbSession,
    ctx: Ctx,
    user: CurrentUserDep,
    scope: str = Query("dashboard", description="分析范围：dashboard/cashflow/accounts/assets/lease/…"),
    period: str = Query("current_month"),
    target_id: str | None = Query(None, description="对象 id（如 scope=lease 时的租约 id）"),
) -> dict:
    """把「AI 摘要」「AI 现金流分析」这类按钮变成真正产出分析的接口。

    返回里 facts 是确定性统计（模型不可用时也能展示），
    bullets 是模型给出的「结论 / 关注 / 建议」三条。
    """
    from app.services import ai_analysis as ai_analysis_service

    data = await ai_analysis_service.analyze(db, user, ctx, scope, period, target_id)
    return success(data, message="分析完成")


@router.get("/topics", summary="脉诊主题清单")
async def topics(user: CurrentUserDep) -> dict:
    items = [
        {"key": k, "title": t, "enabled": k != "overview", "pending_module": None}
        for k, t in TOPICS
    ]
    return list_payload(items, len(items), 1, len(items))


@router.get("/assets", summary="资产分析")
async def assets(db: DbSession, user: CurrentUserDep,
                 period: str = Query("current_month"), currency: str | None = Query(None)) -> dict:
    return success(await insights_service.topic_insight(db, user, "assets", period, currency))


@router.get("/income", summary="收入分析")
async def income(db: DbSession, user: CurrentUserDep,
                 period: str = Query("current_month"), currency: str | None = Query(None)) -> dict:
    return success(await insights_service.topic_insight(db, user, "income", period, currency))


@router.get("/expenses", summary="消费分析")
async def expenses(db: DbSession, user: CurrentUserDep,
                   period: str = Query("current_month"), currency: str | None = Query(None)) -> dict:
    return success(await insights_service.topic_insight(db, user, "expenses", period, currency))


@router.get("/liabilities", summary="负债分析")
async def liabilities(db: DbSession, user: CurrentUserDep,
                      period: str = Query("current_month"), currency: str | None = Query(None)) -> dict:
    return success(await insights_service.topic_insight(db, user, "liabilities", period, currency))


@router.get("/investments", summary="投资分析")
async def investments(db: DbSession, user: CurrentUserDep,
                      period: str = Query("current_month"), currency: str | None = Query(None)) -> dict:
    return success(await insights_service.topic_insight(db, user, "investments", period, currency))


@router.get("/leases", summary="租赁分析")
async def leases(db: DbSession, user: CurrentUserDep,
                 period: str = Query("current_month"), currency: str | None = Query(None)) -> dict:
    return success(await insights_service.topic_insight(db, user, "leases", period, currency))


@router.get("/data-quality", summary="数据质量（非盘点）")
async def data_quality(db: DbSession, user: CurrentUserDep) -> dict:
    return success(await insights_service.data_quality_report(db, user))


@router.get("/spending-habits", summary="消费习惯分析（趋势/大额/异常/周期性）")
async def spending_habits(
    db: DbSession,
    user: CurrentUserDep,
    period: str = Query("current_month"),
    currency: str | None = Query(None),
    months: int = Query(6, ge=3, le=12),
) -> dict:
    return success(await insights_service.spending_habits(db, user, period, currency, months))
