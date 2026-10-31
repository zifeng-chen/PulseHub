"""认证 API（04 §4–§5、18 §2.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Body
from sqlalchemy import select

from app.api.deps import CurrentUserDep, Ctx, DbSession
from app.core.errors import AppError, ErrorCode, list_payload, success
from app.db.models.identity import User
from app.services import audit as audit_service
from app.services import auth as auth_service
from app.services import setup as setup_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", summary="登录")
async def login(db: DbSession, ctx: Ctx, payload: dict = Body(...)) -> dict:
    if not await setup_service.is_initialized(db):
        raise AppError(ErrorCode.SETUP_REQUIRED, "系统尚未初始化，请先完成首次启动引导", status_code=409)

    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    remember = bool(payload.get("remember_device"))
    if not username or not password:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请填写账号和密码",
                       field_errors={"username": "请填写账号", "password": "请填写密码"})

    user = await auth_service.authenticate(db, username, password)
    result = await auth_service.issue_session(db, user, remember, ctx)
    await audit_service.record_audit(
        db, action="auth.login", action_label="用户登录",
        object_type="user", object_id=user.id,
        user=None, ctx=ctx, household_id=result["user"]["household_id"],
    )
    await db.commit()
    return success(result, message="登录成功")


@router.post("/refresh", summary="刷新会话")
async def refresh(db: DbSession, ctx: Ctx, payload: dict = Body(...)) -> dict:
    token = payload.get("refresh_token") or ""
    if not token:
        raise AppError(ErrorCode.VALIDATION_ERROR, "缺少刷新令牌", field_errors={"refresh_token": "缺少刷新令牌"})
    result = await auth_service.refresh_session(db, token, ctx)
    await db.commit()
    return success(result, message="会话已刷新")


@router.post("/logout", summary="退出登录")
async def logout(db: DbSession, ctx: Ctx, user: CurrentUserDep) -> dict:
    if user.session_id:
        await auth_service.revoke_session(db, user.session_id, user.user_id)
    await audit_service.record_audit(
        db, action="auth.logout", action_label="退出登录",
        object_type="user", object_id=user.user_id, user=user, ctx=ctx,
    )
    await db.commit()
    return success({"logged_out": True}, message="已退出登录")


@router.get("/me", summary="当前用户")
async def me(db: DbSession, user: CurrentUserDep) -> dict:
    db_user = (await db.execute(select(User).where(User.id == user.user_id))).scalars().first()
    if db_user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, status_code=401)
    return success(auth_service.serialize_user(db_user, user.household_id, user.role, user.action_set))


@router.post("/verify-password", summary="校验当前用户密码（锁屏解锁）")
async def verify_password_endpoint(db: DbSession, user: CurrentUserDep,
                                   payload: dict = Body(...)) -> dict:
    """只校验密码，不签发新会话：用于锁定屏幕后的解锁。

    与登录共用同一套失败计数与锁定：这个接口等于「不用重新登录的密码校验」，
    如果它不计数，拿着 token 就能在这里无限次试密码。
    """
    from app.core.security import verify_password
    from app.db.models.identity import User
    from app.services import auth as auth_service
    from sqlalchemy import select

    password = str(payload.get("password") or "")
    if not password:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请输入密码",
                       field_errors={"password": "请输入密码"}, status_code=422)
    row = (await db.execute(select(User).where(User.id == user.user_id))).scalars().first()
    auth_service.ensure_not_locked(row)
    if row is None or not verify_password(password, row.password_hash):
        if row is not None:
            await auth_service.register_failure(db, row)
        raise AppError(ErrorCode.FORBIDDEN, "密码不正确，请重试", status_code=401)
    auth_service.clear_failures(row)
    await db.commit()
    return success({"verified": True}, message="已解锁")


# 个人资料可选的预设头像：只存 key，不存 base64（avatar 字段仅 255 字符），
# 前端按 key 渲染渐变底色 + 首字，避免图片鉴权与体积问题。
AVATAR_PRESETS = ("aurora", "sunset", "forest", "ocean", "berry", "slate")


@router.patch("/profile", summary="修改个人资料（昵称 / 邮箱 / 手机 / 头像）")
async def update_profile(
    db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...),
) -> dict:
    db_user = (await db.execute(select(User).where(User.id == user.user_id))).scalars().first()
    if db_user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, status_code=401)

    errors: dict[str, str] = {}
    raw_name = payload.get("display_name")
    name = (raw_name or "").strip() if raw_name is not None else None
    if name is not None:
        if not name:
            errors["display_name"] = "昵称不能为空"
        elif len(name) > 64:
            errors["display_name"] = "昵称不能超过 64 个字符"

    email = payload.get("email")
    email = (email or "").strip() or None if email is not None else None
    if email and ("@" not in email or len(email) > 128 or " " in email):
        errors["email"] = "邮箱格式不正确"

    phone = payload.get("phone")
    phone = (phone or "").strip() or None if phone is not None else None
    if phone and (len(phone) > 32 or not any(ch.isdigit() for ch in phone)):
        errors["phone"] = "手机号格式不正确"

    avatar = payload.get("avatar")
    avatar = (avatar or "").strip() if avatar is not None else None
    if avatar:
        key = avatar.split(":", 1)[-1]
        if avatar.startswith("preset:") and key in AVATAR_PRESETS:
            pass
        else:
            errors["avatar"] = "请选择提供的头像样式"

    if errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请检查填写内容", field_errors=errors)

    if name is not None:
        db_user.display_name = name
    if email is not None:
        db_user.email = email
    if phone is not None:
        db_user.phone = phone
    if avatar is not None:
        db_user.avatar = avatar or None
    db_user.updated_by = user.user_id

    await audit_service.record_audit(
        db, action="auth.profile.update", action_label="修改个人资料",
        object_type="user", object_id=user.user_id, result="success",
        after={"display_name": db_user.display_name, "avatar": db_user.avatar},
        user=user, ctx=ctx,
    )
    await db.commit()
    return success({
        "user_id": db_user.id,
        "username": db_user.username,
        "display_name": db_user.display_name,
        "email": db_user.email,
        "phone": db_user.phone,
        "avatar": db_user.avatar,
    }, message="个人资料已更新")


@router.post("/change-password", summary="修改密码")
async def change_password(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    db_user = (await db.execute(select(User).where(User.id == user.user_id))).scalars().first()
    if db_user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, status_code=401)
    old = payload.get("old_password") or ""
    new = payload.get("new_password") or ""
    await auth_service.change_password(db, db_user, old, new)
    revoked = await auth_service.revoke_all_sessions(db, user.user_id)
    await audit_service.record_audit(
        db, action="auth.change_password", action_label="修改密码",
        object_type="user", object_id=user.user_id, result="success",
        user=user, ctx=ctx,
    )
    await db.commit()
    return success({"revoked_sessions": revoked}, message="密码已修改，请重新登录")


@router.post("/reset-password", summary="管理员本机恢复密码")
async def reset_password(db: DbSession, ctx: Ctx, payload: dict = Body(...)) -> dict:
    if await setup_service.is_initialized(db):
        # 已初始化后只允许系统管理员账号走恢复流程；
        # 是否允许重置由恢复邮箱决定（未配置时必须走服务器本机离线脚本）。
        user = (
            await db.execute(select(User).where(User.username == (payload.get("username") or "").strip()))
        ).scalars().first()
        if user is None or not user.is_system_admin:
            raise AppError(ErrorCode.FORBIDDEN, "仅系统管理员支持本机恢复流程", status_code=403)
    target = await auth_service.reset_password_admin(
        db,
        (payload.get("username") or "").strip(),
        payload.get("recovery_email"),
        payload.get("new_password") or "",
    )
    await audit_service.record_audit(
        db, action="auth.reset_password", action_label="重置密码（本机恢复）",
        object_type="user", object_id=target.id, result="success", ctx=ctx,
        household_id=None,
    )
    await db.commit()
    return success({"username": target.username}, message="密码已重置，旧会话已失效，请重新登录")


@router.get("/sessions", summary="会话列表")
async def sessions(db: DbSession, user: CurrentUserDep) -> dict:
    rows = await auth_service.list_sessions(db, user.user_id)
    items = [auth_service.serialize_session(s, user.session_id) for s in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.delete("/sessions/{session_id}", summary="撤销单个会话")
async def revoke(db: DbSession, ctx: Ctx, user: CurrentUserDep, session_id: str) -> dict:
    await auth_service.revoke_session(db, session_id, user.user_id)
    await audit_service.record_audit(
        db, action="auth.session_revoke", action_label="撤销会话",
        object_type="session", object_id=session_id, user=user, ctx=ctx,
    )
    await db.commit()
    return success({"revoked": session_id}, message="会话已撤销")


@router.post("/sessions/revoke-all", summary="撤销全部会话")
async def revoke_all(db: DbSession, ctx: Ctx, user: CurrentUserDep) -> dict:
    count = await auth_service.revoke_all_sessions(db, user.user_id)
    await audit_service.record_audit(
        db, action="auth.session_revoke_all", action_label="撤销全部会话",
        object_type="session", after={"count": count}, user=user, ctx=ctx,
    )
    await db.commit()
    return success({"revoked": count}, message=f"已撤销 {count} 个会话")
