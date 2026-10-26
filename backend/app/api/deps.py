"""FastAPI 依赖：请求上下文、当前用户、权限、初始化校验。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.money import set_amount_precision
from app.core.security import decode_token
from app.db.base import get_db
from app.db.models.config import HouseholdSetting
from app.db.models.identity import HouseholdMember, User, UserSession
from app.services.permissions import load_role_actions

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_ctx(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", ""),
        ip_summary=getattr(request.state, "ip_summary", ""),
        user_agent=request.headers.get("User-Agent", ""),
        source_page=getattr(request.state, "source_page", None),
        source_method=request.headers.get("X-Source-Method", "manual"),
    )


Ctx = Annotated[RequestContext, Depends(get_ctx)]


async def _resolve_user(db: AsyncSession, token: str) -> CurrentUser:
    try:
        payload = decode_token(token)
    except Exception:
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)
    if payload.get("typ") != "access":
        raise AppError(ErrorCode.UNAUTHORIZED, status_code=401)

    session = (
        await db.execute(select(UserSession).where(UserSession.id == payload.get("sid")))
    ).scalars().first()
    if session is None or session.revoked_at is not None:
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)

    user = (await db.execute(select(User).where(User.id == payload.get("sub")))).scalars().first()
    if user is None or user.status != "active":
        raise AppError(ErrorCode.UNAUTHORIZED, status_code=401)

    membership = (
        await db.execute(
            select(HouseholdMember).where(
                HouseholdMember.user_id == user.id, HouseholdMember.status == "active"
            )
        )
    ).scalars().first()
    household_id = membership.household_id if membership else None
    role = membership.role if membership else ("admin" if user.is_system_admin else "member")
    actions = await load_role_actions(db, role, household_id)

    # 金额精度按家庭配置注入本请求上下文（08 §4「金额精度」，默认两位小数）
    precision = None
    if household_id:
        precision = (
            await db.execute(
                select(HouseholdSetting.amount_precision).where(
                    HouseholdSetting.household_id == household_id,
                    HouseholdSetting.deleted_at.is_(None),
                )
            )
        ).scalars().first()
    set_amount_precision(precision)

    return CurrentUser(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_system_admin=user.is_system_admin,
        household_id=household_id,
        role=role,
        session_id=session.id,
        actions=actions,
    )


async def get_current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(ErrorCode.UNAUTHORIZED, status_code=401)
    return await _resolve_user(db, authorization.split(" ", 1)[1].strip())


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def get_optional_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        return await _resolve_user(db, authorization.split(" ", 1)[1].strip())
    except AppError:
        return None


def require_action(action: str):
    """动作级权限校验（23 §6 十类动作）。"""

    async def _checker(user: CurrentUserDep) -> CurrentUser:
        if action not in user.action_set:
            raise AppError(ErrorCode.FORBIDDEN, status_code=403)
        return user

    return _checker
