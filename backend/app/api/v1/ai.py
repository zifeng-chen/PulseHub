"""AI 能力 API（09、23 §8）。

M0 交付：适配器状态、连接测试、工具白名单契约与评测用例。
模型未连接时不得阻塞记账、台账、统计与历史查询。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.adapters import build_adapters
from app.api.deps import CurrentUserDep, Ctx, DbSession
from app.core.errors import AppError, ErrorCode, list_payload, success
from app.db.models.config import AIEvaluationCase
from app.services import audit as audit_service
from app.services.permissions import require_action

router = APIRouter(prefix="/ai", tags=["本地 AI"])

# AI 工具白名单契约（23 §8）
AI_TOOLS = [
    {"name": "get_dashboard_snapshot", "zh": "读取脉枢台快照", "permission": "view",
     "scope": "当前家庭统计快照", "can_suggest": False, "can_draft": False,
     "forbidden": "不得直接修改任何指标"},
    {"name": "analyze_assets", "zh": "资产分析", "permission": "view",
     "scope": "已确认资产与估值", "can_suggest": False, "can_draft": False,
     "forbidden": "不得自动改估值、处置资产"},
    {"name": "analyze_income", "zh": "收入分析", "permission": "view",
     "scope": "已确认收入事件", "can_suggest": False, "can_draft": False,
     "forbidden": "不得把借款、押金计入收入"},
    {"name": "analyze_expenses", "zh": "消费分析", "permission": "view",
     "scope": "已确认支出事件", "can_suggest": True, "can_draft": True,
     "forbidden": "不得自动确认流水"},
    {"name": "analyze_liabilities", "zh": "负债分析", "permission": "view",
     "scope": "负债、还款与利息", "can_suggest": False, "can_draft": False,
     "forbidden": "不得发起支付或修改本金"},
    {"name": "analyze_investments", "zh": "投资分析", "permission": "view",
     "scope": "投资账户、持仓、交易与估值", "can_suggest": False, "can_draft": False,
     "forbidden": "不得下单或自动估值"},
    {"name": "analyze_leases", "zh": "租赁分析", "permission": "view",
     "scope": "租约、应收、实收与押金", "can_suggest": True, "can_draft": True,
     "forbidden": "不得自动催收，不得混淆押金收入"},
    {"name": "search_events", "zh": "检索业务事件", "permission": "view",
     "scope": "家庭范围内事件", "can_suggest": False, "can_draft": False,
     "forbidden": "不得越权读取其他家庭数据"},
    {"name": "suggest_category", "zh": "建议分类", "permission": "create_draft",
     "scope": "分类字典与历史模式", "can_suggest": True, "can_draft": True,
     "forbidden": "不得直接写入正式台账"},
    {"name": "suggest_record_from_document", "zh": "单据结构化建议", "permission": "create_draft",
     "scope": "OCR 结果与上传材料", "can_suggest": True, "can_draft": True,
     "forbidden": "低置信度内容不得直接入账"},
    {"name": "suggest_reminder", "zh": "提醒建议", "permission": "create_draft",
     "scope": "已确认数据与用户确认的建议", "can_suggest": True, "can_draft": True,
     "forbidden": "不得创建后台任务或自动高影响发送"},
    {"name": "create_confirmable_draft", "zh": "创建待确认草稿", "permission": "create_draft",
     "scope": "草稿层", "can_suggest": True, "can_draft": True,
     "forbidden": "不得跳过用户确认"},
    {"name": "apply_confirmed_suggestion", "zh": "应用已确认建议", "permission": "confirm",
     "scope": "用户已确认的建议", "can_suggest": False, "can_draft": False,
     "forbidden": "模型不得直接写业务表，必须由领域服务在用户确认后执行"},
]


@router.get("/tools", summary="AI 工具白名单契约")
async def tools(user: CurrentUserDep) -> dict:
    return list_payload(AI_TOOLS, len(AI_TOOLS), 1, len(AI_TOOLS))


@router.get("/providers", summary="AI / OCR / 通知适配器状态")
async def providers(db: DbSession, user: CurrentUserDep) -> dict:
    adapters = build_adapters(db, user.household_id)
    items = [await a.status() for a in adapters.values()]
    return list_payload([i.to_dict() for i in items], len(items), 1, len(items))


@router.post("/providers/{key}/test", summary="能力连接测试")
async def test_provider(db: DbSession, ctx: Ctx, user: CurrentUserDep, key: str) -> dict:
    require_action(user, "configure")
    adapters = build_adapters(db, user.household_id)
    adapter = adapters.get(key)
    if adapter is None:
        raise AppError(ErrorCode.NOT_FOUND, "未知的能力适配器", status_code=404)
    status = await adapter.test()
    await audit_service.record_audit(
        db, action=f"provider.test.{key}", action_label=f"测试{adapter.label}连接",
        object_type="provider", object_id=key,
        after={"status": status.status},
        result="success" if status.status == "connected" else "failure",
        external_provider=key, user=user, ctx=ctx,
    )
    await db.commit()
    return success(status.to_dict(), message=status.status_label)


@router.post("/chat", summary="AI 对话（确定性优先 → 本地 → 第三方）")
async def chat(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = None) -> dict:
    from app.services import ai as ai_service

    payload = payload or {}
    result = await ai_service.ai_chat(
        db, user, payload.get("question", ""), payload.get("context"), ctx=ctx,
        model_type=payload.get("model_type") or "auto",
    )
    await db.commit()
    return success(result, message=result["state_label"])


@router.get("/evaluation/cases", summary="AI 固定评测用例")
async def evaluation_cases(db: DbSession, user: CurrentUserDep) -> dict:
    rows = list((await db.execute(select(AIEvaluationCase))).scalars().all())
    items = [{"id": r.id, "name": r.name, "category": r.category,
              "question": r.question, "enabled": r.enabled} for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


# ---------------- 对话会话（09 §3：新建对话 / 历史对话） ----------------

@router.post("/conversations", summary="新建对话")
async def create_conversation(db: DbSession, user: CurrentUserDep, payload: dict = None) -> dict:
    from app.services import ai_conversations as conv_service

    payload = payload or {}
    conv = await conv_service.create_conversation(
        db, user, title=payload.get("title"), context=payload.get("context"))
    await db.commit()
    return success(conv_service.serialize_conversation(conv), message="已新建对话")


@router.get("/conversations", summary="历史对话列表")
async def list_conversations(db: DbSession, user: CurrentUserDep) -> dict:
    from app.services import ai_conversations as conv_service

    rows = await conv_service.list_conversations(db, user)
    items = [conv_service.serialize_conversation(c) for c in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.get("/conversations/{conversation_id}", summary="对话详情（消息列表）")
async def get_conversation(db: DbSession, user: CurrentUserDep, conversation_id: str) -> dict:
    from app.services import ai_conversations as conv_service

    conv, msgs = await conv_service.get_messages(db, user, conversation_id)
    return success({
        "conversation": conv_service.serialize_conversation(conv),
        "messages": [conv_service.serialize_message(m) for m in msgs],
    })


@router.delete("/conversations/{conversation_id}", summary="删除对话")
async def delete_conversation(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                              conversation_id: str) -> dict:
    require_action(user, "delete")
    from app.services import ai_conversations as conv_service

    await conv_service.delete_conversation(db, user, conversation_id)
    await db.commit()
    return success({"id": conversation_id}, message="对话已删除")


@router.post("/messages", summary="发送消息（无会话则新建）")
async def send_message(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = None) -> dict:
    from app.services import ai_conversations as conv_service

    payload = payload or {}
    result = await conv_service.send_message(
        db, user, payload.get("conversation_id"),
        payload.get("question", ""), payload.get("context"), ctx=ctx,
        model_type=payload.get("model_type") or "auto",
    )
    await db.commit()
    return success(result, message="已回复")
