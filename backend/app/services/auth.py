"""认证基座：登录、会话、令牌轮换、改密（04 §4–§7、18 §2.2）。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.security import (
    assess_password_strength,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    new_session_id,
    verify_password,
)
from app.db.models.identity import HouseholdMember, User, UserSession
from app.services.permissions import load_role_actions


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 返回的时间为 naive，统一按 UTC 处理后再比较。"""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _membership(db: AsyncSession, user_id: str) -> HouseholdMember | None:
    return (
        await db.execute(
            select(HouseholdMember).where(
                HouseholdMember.user_id == user_id,
                HouseholdMember.status == "active",
            )
        )
    ).scalars().first()


async def register_failure(db: AsyncSession, user: User) -> None:
    """记一次密码错误；达到阈值就锁定一段时间。

    登录与「锁屏解锁」共用同一套计数——否则锁屏解锁就成了不计数、
    不限次的密码校验接口，攻击者拿到 token 后可以在这里慢慢试。

    **必须立刻提交**：异常抛出后事务会回滚，不提交等于没记，锁定永远不生效。
    """
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= settings.login_max_failures:
        user.locked_until = _now() + timedelta(minutes=settings.login_lock_minutes)
        user.failed_login_count = 0
    await db.commit()


def ensure_not_locked(user: User | None) -> None:
    """账号在锁定期内直接拒绝，不再校验密码。"""
    if user and _aware(user.locked_until) and _aware(user.locked_until) > _now():
        raise AppError(ErrorCode.ACCOUNT_LOCKED, status_code=423)


def clear_failures(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None


async def authenticate(db: AsyncSession, username: str, password: str) -> User:
    """校验账号密码。密码错误不区分账号不存在/密码错误；连续失败触发短暂锁定。"""
    user = (await db.execute(select(User).where(User.username == username))).scalars().first()

    ensure_not_locked(user)

    if user is None or not verify_password(password, user.password_hash):
        if user is not None:
            await register_failure(db, user)
        raise AppError(ErrorCode.LOGIN_FAILED, status_code=401)

    if user.status != "active":
        raise AppError(ErrorCode.FORBIDDEN, "账号已停用，请联系管理员", status_code=403)

    clear_failures(user)
    user.last_login_at = _now()
    return user


async def issue_session(
    db: AsyncSession, user: User, remember_device: bool, ctx: RequestContext | None = None
) -> dict:
    membership = await _membership(db, user.id)
    household_id = membership.household_id if membership else None
    role = membership.role if membership else ("admin" if user.is_system_admin else "member")

    # 会话主键即令牌中的 sid，二者必须一致，否则会话无法被校验/撤销
    session_id = new_session_id()
    access = create_access_token(user.id, session_id, household_id, role)
    # 勾选「记住此设备」→ 30 天（滑动续期）；未勾选 → 1 天，避免公用设备长期留登录态
    refresh, expires_at = create_refresh_token(
        user.id, session_id, days=None if remember_device else 1
    )

    session = UserSession(
        id=session_id,
        user_id=user.id,
        refresh_token_hash=_hash_token(refresh),
        device=(ctx.user_agent[:120] if ctx and ctx.user_agent else None),
        ip_summary=ctx.ip_summary if ctx else None,
        user_agent=ctx.user_agent[:255] if ctx and ctx.user_agent else None,
        expires_at=expires_at,
        last_seen_at=_now(),
        remember_device=remember_device,
    )
    db.add(session)
    await db.flush()

    actions = await load_role_actions(db, role, household_id)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "session_id": session.id,
        "user": serialize_user(user, household_id, role, actions),
    }


async def refresh_session(db: AsyncSession, refresh_token: str, ctx: RequestContext | None = None) -> dict:
    try:
        payload = decode_token(refresh_token)
    except Exception:
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)
    if payload.get("typ") != "refresh":
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)

    session = (
        await db.execute(select(UserSession).where(UserSession.id == payload.get("sid")))
    ).scalars().first()
    if session is None or session.revoked_at is not None:
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)
    if session.refresh_token_hash != _hash_token(refresh_token):
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)

    expires_at = _aware(session.expires_at)
    if expires_at <= _now():
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)

    user = (await db.execute(select(User).where(User.id == session.user_id))).scalars().first()
    if user is None or user.status != "active":
        raise AppError(ErrorCode.SESSION_EXPIRED, status_code=401)

    membership = await _membership(db, user.id)
    household_id = membership.household_id if membership else None
    role = membership.role if membership else ("admin" if user.is_system_admin else "member")

    # 轮换刷新令牌：保持与会话一致的时长（记住设备 30 天 / 否则 1 天）
    new_refresh, new_expires = create_refresh_token(
        user.id, session.id, days=None if session.remember_device else 1
    )
    session.refresh_token_hash = _hash_token(new_refresh)
    session.expires_at = new_expires
    session.last_seen_at = _now()

    access = create_access_token(user.id, session.id, household_id, role)
    actions = await load_role_actions(db, role, household_id)
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "session_id": session.id,
        "user": serialize_user(user, household_id, role, actions),
    }


async def revoke_session(db: AsyncSession, session_id: str, user_id: str | None = None) -> None:
    session = (await db.execute(select(UserSession).where(UserSession.id == session_id))).scalars().first()
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
    if user_id and session.user_id != user_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    session.revoked_at = _now()


async def revoke_all_sessions(db: AsyncSession, user_id: str) -> int:
    rows = (
        await db.execute(
            select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        )
    ).scalars().all()
    for s in rows:
        s.revoked_at = _now()
    return len(rows)


async def list_sessions(db: AsyncSession, user_id: str) -> list[UserSession]:
    return list(
        (
            await db.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id)
                .order_by(UserSession.created_at.desc())
            )
        ).scalars().all()
    )


async def change_password(db: AsyncSession, user: User, old_password: str, new_password: str) -> None:
    if not verify_password(old_password, user.password_hash):
        raise AppError(ErrorCode.VALIDATION_ERROR, "原密码不正确", field_errors={"old_password": "原密码不正确"})
    strength = assess_password_strength(new_password)
    if not strength["acceptable"]:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "新密码强度不足",
            field_errors={"new_password": "；".join(strength["warnings"]) or "新密码强度不足"},
        )
    user.password_hash = hash_password(new_password)


async def reset_password_admin(db: AsyncSession, username: str, recovery_email: str | None, new_password: str) -> User:
    """管理员本机恢复流程（04 §5）：不伪造外部找回能力。"""
    user = (await db.execute(select(User).where(User.username == username))).scalars().first()
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "账号不存在", status_code=404)
    if not user.is_system_admin:
        raise AppError(ErrorCode.FORBIDDEN, "仅系统管理员支持本机恢复流程", status_code=403)
    # 恢复邮箱是唯一的线上校验凭证：未配置时必须走服务器本机离线脚本，
    # 否则任何人只要知道管理员用户名就能重置密码。
    if not user.recovery_email:
        raise AppError(
            ErrorCode.STATE_INVALID,
            "该账号未配置恢复邮箱，无法在线重置；请在服务器本机执行 python scripts/reset_admin_password.py",
            status_code=409,
        )
    if (recovery_email or "").strip().lower() != user.recovery_email.strip().lower():
        raise AppError(ErrorCode.VALIDATION_ERROR, "恢复邮箱校验失败",
                       field_errors={"recovery_email": "恢复邮箱不匹配"})
    strength = assess_password_strength(new_password)
    if not strength["acceptable"]:
        raise AppError(ErrorCode.VALIDATION_ERROR, "新密码强度不足",
                       field_errors={"new_password": "；".join(strength["warnings"])})
    user.password_hash = hash_password(new_password)
    await revoke_all_sessions(db, user.id)
    return user


def serialize_user(user: User, household_id: str | None, role: str, actions: set[str]) -> dict:
    from app.services.permissions import ROLE_ZH

    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "avatar": user.avatar,
        "email": user.email,
        "phone": user.phone,
        "is_system_admin": user.is_system_admin,
        "status": user.status,
        "household_id": household_id,
        "role": role,
        "role_label": ROLE_ZH.get(role, role),
        "actions": sorted(actions),
        "last_login_at": utc_iso(user.last_login_at) if user.last_login_at else None,
    }


def serialize_session(s: UserSession, current_session_id: str | None = None) -> dict:
    return {
        "id": s.id,
        "device": s.device or "未知设备",
        "ip_summary": s.ip_summary or "—",
        "login_at": utc_iso(s.created_at),
        "last_seen_at": utc_iso(s.last_seen_at) if s.last_seen_at else None,
        "expires_at": utc_iso(s.expires_at),
        "revoked": s.revoked_at is not None,
        "current": s.id == current_session_id,
    }
