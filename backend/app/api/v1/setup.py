"""首次启动引导 API（04 §1–§3、18 §2.2）。

初始化接口仅在系统未初始化时开放；初始化完成后关闭公开初始化接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.api.deps import DbSession, get_ctx, get_optional_user
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode, success
from app.core.security import assess_password_strength
from app.db.models.identity import User
from app.services import audit as audit_service
from app.services import setup as setup_service
from sqlalchemy import select

router = APIRouter(prefix="/setup", tags=["首次启动"])


async def ensure_not_initialized(db: DbSession) -> None:
    if await setup_service.is_initialized(db):
        raise AppError(ErrorCode.SETUP_ALREADY_DONE, status_code=409)


Guard = Depends(ensure_not_initialized)


@router.get("/status", summary="系统初始化状态")
async def status(db: DbSession) -> dict:
    initialized = await setup_service.is_initialized(db)
    return success({
        "initialized": initialized,
        "next": "login" if initialized else "setup",
    })


@router.get("/check", summary="安装检测")
async def check(db: DbSession, user: CurrentUser | None = Depends(get_optional_user)) -> dict:
    """安装检测。

    这个接口**故意允许匿名访问**：数据库连不上、迁移失败时用户根本登不进去，
    如果要求登录，就成了「登不上就看不了为什么登不上」的死锁。登录页也挂了入口。

    但初始化完成之后，返回里的版本号、迁移版本、数据库类型、连接延迟
    就成了「对号入座找已知漏洞」的指纹，所以匿名访客只看得到每项是否正常，
    细节留给已登录用户。
    """
    data = await setup_service.install_check(db)
    if data.get("initialized") and user is None:
        # 不给版本号/迁移号/数据库类型/延迟，但也不要留一片空白——
        # 匿名访客需要看懂「哪项正常」，只是不需要知道具体版本。
        neutral = {
            "backend": "服务运行正常",
            "database": "连接正常",
            "file": "存储目录可读写",
            "migration": "迁移已应用",
        }
        data["checks"] = [
            {**c, "detail": neutral.get(c["key"], "正常") if c["status"] == "pass" else "检查未通过"}
            for c in data["checks"]
        ]
        data["app_version"] = None
        data["env"] = None
        data["database_type"] = None
        data["redacted"] = True
    return success(data)


@router.post("/database/test", summary="测试数据库连接", dependencies=[Guard])
async def database_test(db: DbSession, payload: dict = Body(...)) -> dict:
    result = await setup_service.test_database(payload)
    if not result.get("ok"):
        return success(result, message=result.get("message", "连接失败"))
    return success(result, message="连接成功")


@router.post("/database", summary="保存数据库配置", dependencies=[Guard])
async def database_save(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                        payload: dict = Body(...)) -> dict:
    result = await setup_service.save_database_config(db, payload)
    await audit_service.record_audit(
        db, action="setup.database_saved", action_label="保存数据库配置",
        object_type="deployment", after={"mode": result.get("mode")}, ctx=ctx,
    )
    await db.commit()
    return success(result, message=result.get("message", "已保存"))


@router.post("/admin", summary="创建管理员", dependencies=[Guard])
async def create_admin(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                       payload: dict = Body(...)) -> dict:
    admin = await setup_service.create_admin(db, payload)
    await audit_service.record_audit(
        db, action="setup.admin_created", action_label="创建管理员",
        object_type="user", object_id=admin["id"],
        after={"username": admin["username"], "display_name": admin["display_name"]},
        ctx=ctx,
    )
    await db.commit()
    return success(admin, message="管理员创建成功")


@router.get("/password-strength", summary="密码强度评估", dependencies=[Guard])
async def password_strength(password: str = "") -> dict:
    return success(assess_password_strength(password))


@router.post("/household", summary="创建家庭基础信息", dependencies=[Guard])
async def create_household(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                           payload: dict = Body(...)) -> dict:
    admin = (
        await db.execute(select(User).where(User.is_system_admin.is_(True)).order_by(User.created_at.desc()))
    ).scalars().first()
    if admin is None:
        raise AppError(ErrorCode.SETUP_REQUIRED, "请先创建管理员账号", status_code=409)
    household = await setup_service.create_household(db, payload, admin)
    await audit_service.record_audit(
        db, action="setup.household_created", action_label="创建家庭基础信息",
        object_type="household", object_id=household["id"],
        after={"name": household["name"], "base_currency": household["base_currency"]},
        ctx=ctx,
    )
    await db.commit()
    return success(household, message="家庭创建成功，已生成默认分类")


@router.post("/finance", summary="保存财务口径", dependencies=[Guard])
async def finance(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                  payload: dict = Body(...)) -> dict:
    household = await setup_service.current_household(db)
    if household is None:
        raise AppError(ErrorCode.SETUP_REQUIRED, "请先创建家庭", status_code=409)
    diff = await setup_service.save_finance_settings(db, household.id, payload)
    await audit_service.record_audit(
        db, action="setup.finance_saved", action_label="保存财务口径",
        object_type="household_setting", object_id=household.id,
        before=diff["before"], after=diff["after"], ctx=ctx,
    )
    await db.commit()
    return success(diff["after"], message="财务口径已保存")


@router.post("/local-ai", summary="保存本地 AI 配置", dependencies=[Guard])
async def save_local_ai(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                        payload: dict = Body(...)) -> dict:
    household = await setup_service.current_household(db)
    result = await setup_service.save_local_ai(db, household.id if household else None, payload)
    await audit_service.record_audit(
        db, action="setup.local_ai_saved", action_label="保存本地 AI 配置",
        object_type="local_ai", after={"status": result["status"],
                                       "base_url": payload.get("base_url")}, ctx=ctx,
    )
    await db.commit()
    return success(result, message="本地 AI 配置已保存")


@router.post("/ocr", summary="保存百度 OCR 配置", dependencies=[Guard])
async def save_ocr(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                   payload: dict = Body(...)) -> dict:
    household = await setup_service.current_household(db)
    result = await setup_service.save_ocr(db, household.id if household else None, payload)
    await audit_service.record_audit(
        db, action="setup.ocr_saved", action_label="保存百度 OCR 配置",
        object_type="ocr", after={"status": result["status"], "region": payload.get("region")},
        ctx=ctx,
    )
    await db.commit()
    return success(result, message="百度 OCR 配置已保存（密钥已加密存储）")


@router.post("/notification", summary="保存通知渠道配置", dependencies=[Guard])
async def save_notification(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                            payload: dict = Body(...)) -> dict:
    household = await setup_service.current_household(db)
    result = await setup_service.save_notification(db, household.id if household else None, payload)
    await audit_service.record_audit(
        db, action="setup.notification_saved", action_label="保存通知渠道配置",
        object_type="notification", after={"status": result["status"]}, ctx=ctx,
    )
    await db.commit()
    return success(result, message="通知渠道配置已保存")


@router.post("/third-party-ai", summary="保存第三方 AI 配置", dependencies=[Guard])
async def save_third_party(db: DbSession, ctx: RequestContext = Depends(get_ctx),
                           payload: dict = Body(...)) -> dict:
    household = await setup_service.current_household(db)
    result = await setup_service.save_third_party_ai(db, household.id if household else None, payload)
    await audit_service.record_audit(
        db, action="setup.third_party_ai_saved", action_label="保存第三方 AI 配置",
        object_type="third_party_ai", after={"status": result["status"], "enabled": False}, ctx=ctx,
    )
    await db.commit()
    return success(result, message=result["message"])


@router.get("/summary", summary="初始化摘要")
async def summary(db: DbSession, user: CurrentUser | None = Depends(get_optional_user)) -> dict:
    """初始化摘要。

    未初始化时向导要用它展示「管理员/家庭叫什么」，所以必须公开；
    **但初始化完成之后，这份数据（管理员账号、家庭名、各服务配置状态）
    就成了未认证访客也能读的侦察信息**——实测直接 GET 就能拿到用户名。
    因此已初始化时只对登录用户返回完整内容，匿名访问降级为不含账号与家庭名的版本。
    """
    data = await setup_service.setup_summary(db)
    if data.get("initialized") and user is None:
        data = {**data, "admin": None, "household": None, "redacted": True}
    return success(data)


@router.post("/complete", summary="完成初始化", dependencies=[Guard])
async def complete(db: DbSession, ctx: RequestContext = Depends(get_ctx)) -> dict:
    if not await setup_service.is_initialized(db):
        household = await setup_service.current_household(db)
        await setup_service.ensure_privacy(db, household.id if household else None)
        await setup_service.mark_initialized(db, None)
        await audit_service.record_audit(
            db, action="setup.completed", action_label="完成系统初始化",
            object_type="system", after={"initialized": True}, ctx=ctx,
        )
        await db.commit()
    return success(await setup_service.setup_summary(db), message="系统初始化完成，公开初始化接口已关闭")
