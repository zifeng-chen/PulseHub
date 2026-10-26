"""AI 对话会话服务（09 §3）：新建对话 / 历史列表 / 发消息持久化。

- 会话与消息落库（ai_conversations / ai_messages），跨设备一致；
- 发消息：写用户消息 → 调用既有 ai_chat 生成回复 → 写助手消息；
- 严格保持 AI 边界：只读分析 + 建议，不写业务表，不绕过用户确认。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.db.models.config import AIConversation, AIMessage

TITLE_MAX = 24


def serialize_conversation(c: AIConversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "context": c.context,
        "message_count": c.message_count or 0,
        "last_message_at": utc_iso(c.last_message_at) if c.last_message_at else None,
        "created_at": utc_iso(c.created_at),
    }


def serialize_message(m: AIMessage) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "state": m.state,
        "model_type": m.model_type,
        "sources": json.loads(m.sources) if m.sources else [],
        "trust": json.loads(m.trust) if m.trust else None,
        "created_at": utc_iso(m.created_at),
    }


async def _get_conversation(db: AsyncSession, user: CurrentUser, conversation_id: str) -> AIConversation:
    conv = (await db.execute(
        select(AIConversation).where(
            AIConversation.id == conversation_id,
            AIConversation.household_id == user.household_id,
            AIConversation.deleted_at.is_(None),
        )
    )).scalars().first()
    if conv is None:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
    return conv


async def create_conversation(
    db: AsyncSession, user: CurrentUser, *, title: str | None = None, context: str | None = None
) -> AIConversation:
    conv = AIConversation(
        id=new_id(),
        household_id=user.household_id or "",
        user_id=user.user_id,
        title=(title or "新对话")[:128],
        context=context or None,
        message_count=0,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(conv)
    await db.flush()
    return conv


async def list_conversations(db: AsyncSession, user: CurrentUser, limit: int = 50) -> list[AIConversation]:
    return list((await db.execute(
        select(AIConversation).where(
            AIConversation.household_id == user.household_id,
            AIConversation.user_id == user.user_id,
            AIConversation.deleted_at.is_(None),
        ).order_by(AIConversation.updated_at.desc()).limit(limit)
    )).scalars().all())


async def get_messages(
    db: AsyncSession, user: CurrentUser, conversation_id: str
) -> tuple[AIConversation, list[AIMessage]]:
    conv = await _get_conversation(db, user, conversation_id)
    msgs = list((await db.execute(
        select(AIMessage).where(
            AIMessage.conversation_id == conversation_id,
            AIMessage.deleted_at.is_(None),
        ).order_by(AIMessage.created_at.asc())
    )).scalars().all())
    return conv, msgs


async def delete_conversation(db: AsyncSession, user: CurrentUser, conversation_id: str) -> None:
    conv = await _get_conversation(db, user, conversation_id)
    conv.deleted_at = datetime.now(timezone.utc)
    conv.updated_by = user.user_id
    await db.flush()


async def send_message(
    db: AsyncSession, user: CurrentUser, conversation_id: str | None,
    question: str, context: str | None = None, ctx: RequestContext | None = None,
    model_type: str = "auto",
) -> dict:
    """发消息：无会话则新建；写入用户消息与助手回复，返回本轮两条消息。"""
    from app.services import ai as ai_service

    question = (question or "").strip()
    if not question:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请输入问题", status_code=422)

    if conversation_id:
        conv = await _get_conversation(db, user, conversation_id)
    else:
        conv = await create_conversation(
            db, user, title=question[:TITLE_MAX], context=context
        )

    # 同一会话最近 8 条消息作为上下文（含助手上一轮回答），追问才能接上
    history_rows = list((await db.execute(
        select(AIMessage).where(AIMessage.conversation_id == conv.id)
        .order_by(AIMessage.created_at.desc()).limit(8)
    )).scalars().all())
    history = [{"role": m.role, "content": m.content} for m in reversed(history_rows)]

    user_msg = AIMessage(
        id=new_id(), household_id=user.household_id or "",
        conversation_id=conv.id, role="user", content=question,
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(user_msg)
    await db.flush()

    # 复用既有链路：确定性优先 → 本地 → 第三方 → 降级
    result = await ai_service.ai_chat(
        db, user, question, context, ctx=ctx, model_type=model_type, history=history,
    )
    trust = result.get("trust") or {}

    assistant_msg = AIMessage(
        id=new_id(), household_id=user.household_id or "",
        conversation_id=conv.id, role="assistant",
        content=result.get("answer") or result.get("message") or "",
        state=result.get("state"),
        model_type=trust.get("model_type"),
        sources=json.dumps(result.get("sources") or [], ensure_ascii=False),
        trust=json.dumps(trust, ensure_ascii=False) if trust else None,
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(assistant_msg)

    now = datetime.now(timezone.utc)
    conv.message_count = (conv.message_count or 0) + 2
    conv.last_message_at = now
    if not conv.context and context:
        conv.context = context
    conv.updated_by = user.user_id
    await db.flush()

    return {
        "conversation": serialize_conversation(conv),
        "user_message": serialize_message(user_msg),
        "assistant_message": serialize_message(assistant_msg),
        "answer": result.get("answer"),
        "state": result.get("state"),
        "message": result.get("message"),
    }
