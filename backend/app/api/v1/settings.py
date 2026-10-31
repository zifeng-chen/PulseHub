"""设置 API（08 全规格、18 §2.2/§2.6）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, Query
from sqlalchemy import func, select

from app.adapters import build_adapters
from app.api.deps import CurrentUserDep, Ctx, DbSession
from app.core.crypto import decrypt_secret, encrypt_secret, mask
from app.core.errors import AppError, ErrorCode, list_payload, success
from app.core.ids import new_id
from app.core.security import hash_password, validate_outbound_url
from app.db.models.config import (
    BeikeConfig,
    HouseholdSetting,
    LocalAIConfig,
    NotificationChannel,
    OcrConfig,
    PrivacyPolicy,
    ThirdPartyAIProvider,
    UserPreference,
)
from app.db.models.identity import HouseholdMember, User
from app.db.models.business import EmploymentProfile
from app.services import audit as audit_service
from app.services import auth as auth_service
from app.services import backup as backup_service
from app.services import beike as beike_service
from app.services import catalog as catalog_service
from app.services import confirm as confirm_service
from app.services import setup as setup_service
from app.services.permissions import MEMBER_ROLES, MEMBER_STATUSES, require_action

router = APIRouter(prefix="/settings", tags=["设置"])


async def _setting(db: DbSession, household_id: str | None) -> HouseholdSetting:
    row = (
        await db.execute(select(HouseholdSetting).where(HouseholdSetting.household_id == household_id))
    ).scalars().first()
    if row is None:
        row = HouseholdSetting(id=new_id(), household_id=household_id or "")
        db.add(row)
        await db.flush()
    return row


# ---------------- 首页概览 ----------------

@router.get("/overview", summary="设置首页")
async def overview(db: DbSession, user: CurrentUserDep) -> dict:
    adapters = build_adapters(db, user.household_id)
    statuses = {k: (await a.status()).to_dict() for k, a in adapters.items()}
    cats = await catalog_service.list_categories(db, user.household_id or "")
    household = await setup_service.current_household(db)
    prefs = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user.user_id))
    ).scalars().first()
    employment_count = int((await db.execute(
        select(func.count(EmploymentProfile.id)).where(
            EmploymentProfile.household_id == user.household_id,
            EmploymentProfile.deleted_at.is_(None),
        )
    )).scalar_one())

    configured = sum(1 for s in statuses.values() if s["status"] in ("connected", "disabled"))
    completeness = int(configured / max(len(statuses), 1) * 100)

    return success({
        "completeness": completeness,
        "household": None if household is None else {
            "name": household.name, "base_currency": household.base_currency, "timezone": household.timezone,
        },
        "admin": {"display_name": user.display_name, "username": user.username, "role_label": user.role},
        "providers": statuses,
        "category_count": len(cats),
        "theme": prefs.theme if prefs else "system",
        "cards": [
            {"key": "profile", "title": "个人资料与密码", "route": "/settings/profile",
             "value": user.display_name},
            {"key": "household", "title": "家庭与成员", "route": "/settings/household"},
            {"key": "employment", "title": "工作与收入", "route": "/settings/employment",
             "value": f"{employment_count} 份档案"},
            {"key": "categories", "title": "分类管理", "route": "/settings/categories",
             "value": f"{len(cats)} 个分类"},
            {"key": "finance", "title": "财务口径", "route": "/settings/finance"},
            {"key": "notifications", "title": "提醒通知", "route": "/settings/notifications",
             "status": statuses["notification"]["status_label"]},
            {"key": "ocr", "title": "百度 OCR", "route": "/settings/ocr",
             "status": statuses["ocr"]["status_label"]},
            {"key": "local-ai", "title": "本地 AI", "route": "/settings/local-ai",
             "status": statuses["local_ai"]["status_label"]},
            {"key": "ai-providers", "title": "第三方 AI 接口", "route": "/settings/ai-providers",
             "status": statuses["third_party_ai"]["status_label"]},
            {"key": "privacy", "title": "隐私与安全", "route": "/settings/privacy"},
            {"key": "backup", "title": "数据备份与恢复", "route": "/settings/backup"},
            {"key": "system", "title": "系统状态与审计", "route": "/settings/system"},
        ],
    })


# ---------------- 家庭与成员 ----------------

@router.get("/household", summary="家庭信息与成员")
async def household_info(db: DbSession, user: CurrentUserDep) -> dict:
    household = await setup_service.current_household(db)
    members = list(
        (
            await db.execute(
                select(HouseholdMember).where(HouseholdMember.household_id == (user.household_id or ""))
            )
        ).scalars().all()
    )
    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    return success({
        "household": None if household is None else {
            "id": household.id, "name": household.name, "description": household.description,
            "base_currency": household.base_currency, "timezone": household.timezone,
            "is_multi_member": household.is_multi_member, "status": household.status,
        },
        "members": [
            {
                "id": m.id, "user_id": m.user_id, "role": m.role,
                "role_label": {"owner": "家庭所有者", "admin": "系统管理员",
                               "member": "家庭成员", "readonly": "只读成员"}.get(m.role, m.role),
                "status": m.status, "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                "display_name": users[m.user_id].display_name if m.user_id in users else "—",
                "username": users[m.user_id].username if m.user_id in users else "—",
                "last_login_at": (users[m.user_id].last_login_at.isoformat()
                                  if m.user_id in users and users[m.user_id].last_login_at else None),
            }
            for m in members
        ],
        "roles": [{"code": k, "label": v} for k, v in
                  (("owner", "家庭所有者"), ("admin", "系统管理员"),
                   ("member", "家庭成员"), ("readonly", "只读成员"))],
    })


def _validate_timezone(name: str) -> str:
    """校验家庭时区：必须与业务时间落库时区（FAM_LOCAL_TZ）的 UTC 偏移一致。

    业务时间按本地墙钟存储，报表期间又按家庭时区切分；两者偏移不同会让「本月」
    这类统计在月初/月末错位，因此这里直接拦下偏移不一致的时区并提示。
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.core.timeutil import local_tz

    try:
        candidate = ZoneInfo(name)
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "时区名称不合法",
                       field_errors={"timezone": "请选择有效的时区"})

    probe = datetime(2026, 1, 1, tzinfo=timezone.utc)
    if candidate.utcoffset(probe) != local_tz().utcoffset(probe):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "家庭时区必须与服务器业务时区保持相同的 UTC 偏移",
            field_errors={"timezone": (
                f"业务时间按 {local_tz().key if hasattr(local_tz(), 'key') else '本地时区'} 墙钟记录，"
                f"与 {name} 的时差不同会导致跨天/跨月统计错位；请选择同一偏移的时区"
            )},
        )
    return name


@router.put("/household", summary="更新家庭信息")
async def update_household(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                           payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    household = await setup_service.current_household(db)
    if household is None:
        raise AppError(ErrorCode.NOT_FOUND, "家庭不存在", status_code=404)
    before = {"name": household.name, "description": household.description,
              "timezone": household.timezone, "is_multi_member": household.is_multi_member,
              "base_currency": household.base_currency}
    if "base_currency" in payload and payload["base_currency"] != household.base_currency:
        raise AppError(ErrorCode.VALIDATION_ERROR, "修改基础币种属于高影响操作，需通过二次确认流程",
                       field_errors={"base_currency": "请使用基础币种变更流程"})
    if "name" in payload and payload["name"]:
        household.name = payload["name"].strip()
    if "description" in payload:
        household.description = payload["description"]
    if "timezone" in payload and payload["timezone"]:
        # 业务时间按「本地墙钟」存储（app/core/timeutil.py），统计期间边界也按家庭时区切分。
        # 两者必须同为 UTC 偏移一致的时区，否则会出现「数据按 +8 存、报表按别的时区切」的错位。
        household.timezone = _validate_timezone(payload["timezone"])
    if "is_multi_member" in payload:
        household.is_multi_member = bool(payload["is_multi_member"])
    after = {"name": household.name, "description": household.description,
             "timezone": household.timezone, "is_multi_member": household.is_multi_member,
             "base_currency": household.base_currency}
    await audit_service.record_audit(
        db, action="household.update", action_label="更新家庭信息",
        object_type="household", object_id=household.id, before=before, after=after,
        user=user, ctx=ctx,
    )
    await db.commit()
    return success(after, message="家庭信息已更新")


@router.post("/members", summary="新增家庭成员")
async def create_member(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    username = (payload.get("username") or "").strip()
    display_name = (payload.get("display_name") or "").strip()
    password = payload.get("password") or ""
    role = payload.get("role") or "member"
    if not username or not display_name or not password:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请填写完整成员信息",
                       field_errors={"username": "请填写登录账号", "display_name": "请填写成员名称",
                                     "password": "请填写初始密码"})
    if role not in MEMBER_ROLES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "角色不合法", field_errors={"role": "角色不合法"})
    exists = (await db.execute(select(User).where(User.username == username))).scalars().first()
    if exists:
        raise AppError(ErrorCode.VALIDATION_ERROR, "登录账号已存在", field_errors={"username": "该账号已被占用"})

    member_user = User(
        id=new_id(), username=username, display_name=display_name,
        password_hash=hash_password(password), status="active",
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(member_user)
    db.add(UserPreference(id=new_id(), user_id=member_user.id, theme="system", language="zh-CN"))
    await db.flush()
    membership = HouseholdMember(
        id=new_id(), household_id=user.household_id or "", user_id=member_user.id,
        role=role, status="active", created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(membership)
    await audit_service.record_audit(
        db, action="member.create", action_label="新增家庭成员",
        object_type="member", object_id=membership.id,
        after={"username": username, "role": role}, user=user, ctx=ctx,
    )
    await db.commit()
    return success({"id": membership.id, "user_id": member_user.id, "role": role},
                   message="成员已创建")


@router.patch("/members/{member_id}", summary="更新成员角色或状态")
async def update_member(db: DbSession, ctx: Ctx, user: CurrentUserDep, member_id: str,
                        payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    member = (await db.execute(select(HouseholdMember).where(HouseholdMember.id == member_id))).scalars().first()
    if member is None or member.household_id != user.household_id:
        raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
    before = {"role": member.role, "status": member.status}
    # 角色/状态必须按枚举校验：新增成员接口校验了，改成员却没校验，
    # 于是可以写入任意字符串（例如把 role 改成 owner）——既是脏数据也是提权口子。
    if "role" in payload and payload["role"]:
        role = str(payload["role"])
        if role not in MEMBER_ROLES:
            raise AppError(ErrorCode.VALIDATION_ERROR, "角色不合法", field_errors={"role": "角色不合法"})
        member.role = role
    if "status" in payload and payload["status"]:
        status = str(payload["status"])
        if status not in MEMBER_STATUSES:
            raise AppError(ErrorCode.VALIDATION_ERROR, "状态不合法",
                           field_errors={"status": "状态不合法"})
        if status == "disabled":
            confirm_service.consume_token(payload.get("confirm_token"), user,
                                          "member.disable", "member", member_id)
        member.status = status
    after = {"role": member.role, "status": member.status}
    if before["status"] != "disabled" and member.status == "disabled":
        await auth_service.revoke_all_sessions(db, member.user_id)
    await audit_service.record_audit(
        db, action="member.update", action_label="更新成员",
        object_type="member", object_id=member.id, before=before, after=after,
        reason=payload.get("reason"), user=user, ctx=ctx,
    )
    await db.commit()
    return success(after, message="成员已更新")


# ---------------- 分类管理 ----------------

@router.get("/categories", summary="分类列表")
async def categories(db: DbSession, user: CurrentUserDep, domain: str | None = Query(None)) -> dict:
    rows = await catalog_service.list_categories(db, user.household_id or "", domain)
    items = [catalog_service.serialize_category(c) for c in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/categories", summary="新增分类")
async def create_category(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    category = await catalog_service.create_category(db, user, payload, ctx)
    await db.commit()
    return success(catalog_service.serialize_category(category), message="分类已创建")


@router.patch("/categories/{category_id}", summary="编辑分类")
async def update_category(db: DbSession, ctx: Ctx, user: CurrentUserDep, category_id: str,
                          payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    category = await catalog_service.update_category(db, user, category_id, payload, ctx)
    await db.commit()
    return success(catalog_service.serialize_category(category), message="分类已更新")


@router.delete("/categories/{category_id}", summary="删除分类（需二次确认）")
async def delete_category(db: DbSession, ctx: Ctx, user: CurrentUserDep, category_id: str,
                          confirm_token: str = Query(None)) -> dict:
    require_action(user, "configure")
    confirm_service.consume_token(confirm_token, user, "category.delete", "category", category_id)
    await catalog_service.assert_category_unused(db, category_id)
    from datetime import datetime, timezone

    from app.db.models.config import Category

    category = (await db.execute(select(Category).where(Category.id == category_id))).scalars().first()
    if category is None or category.household_id != user.household_id:
        raise AppError(ErrorCode.NOT_FOUND, "分类不存在", status_code=404)
    category.deleted_at = datetime.now(timezone.utc)
    await audit_service.record_audit(
        db, action="category.delete", action_label="删除分类",
        object_type="category", object_id=category_id,
        before={"name": category.name}, user=user, ctx=ctx, reason="分类未被引用，执行软删除",
    )
    await db.commit()
    return success({"deleted": category_id}, message="分类已删除（软删除并保留审计）")


# ---------------- 财务口径 / 个人偏好 ----------------

@router.get("/finance", summary="财务口径")
async def finance(db: DbSession, user: CurrentUserDep) -> dict:
    setting = await _setting(db, user.household_id)
    household = await setup_service.current_household(db)
    return success({
        "base_currency": household.base_currency if household else "CNY",
        "timezone": household.timezone if household else "Asia/Shanghai",
        "default_period": setting.default_period,
        "asset_purchase_separate": setting.asset_purchase_separate,
        "investment_valuation_in_net_worth": setting.investment_valuation_in_net_worth,
        "bill_display_mode": setting.bill_display_mode,
        "default_date_range": setting.default_date_range,
        "amount_precision": setting.amount_precision,
        "options": {
            "periods": [{"value": "current_month", "label": "本月"},
                        {"value": "last_month", "label": "上月"},
                        {"value": "last_3_months", "label": "近三个月"},
                        {"value": "last_6_months", "label": "近六个月"},
                        {"value": "current_year", "label": "本年"}],
            "bill_display_modes": [{"value": "day_of_month", "label": "每月固定日期"},
                                   {"value": "days_before_due", "label": "到期前天数"}],
        },
    })


@router.put("/finance", summary="更新财务口径")
async def update_finance(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    household = await setup_service.current_household(db)
    if household is None:
        raise AppError(ErrorCode.NOT_FOUND, "家庭不存在", status_code=404)
    if "base_currency" in payload and payload["base_currency"] != household.base_currency:
        confirm_service.consume_token(payload.get("confirm_token"), user,
                                      "finance.base_currency", "household", household.id)
        before_ccy = household.base_currency
        household.base_currency = payload["base_currency"].upper()
        setting = await _setting(db, household.id)
        setting.base_currency = household.base_currency
        await audit_service.record_audit(
            db, action="finance.change_base_currency", action_label="修改基础币种",
            object_type="household", object_id=household.id,
            before={"base_currency": before_ccy}, after={"base_currency": household.base_currency},
            reason=payload.get("reason"), user=user, ctx=ctx,
        )
    diff = await setup_service.save_finance_settings(db, household.id, payload)
    await audit_service.record_audit(
        db, action="finance.update", action_label="更新财务口径",
        object_type="household_setting", object_id=household.id,
        before=diff["before"], after=diff["after"], user=user, ctx=ctx,
    )
    await db.commit()
    return success(diff["after"], message="财务口径已更新")


@router.get("/preferences", summary="个人偏好")
async def get_preferences(db: DbSession, user: CurrentUserDep) -> dict:
    prefs = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user.user_id))
    ).scalars().first()
    if prefs is None:
        prefs = UserPreference(id=new_id(), user_id=user.user_id)
        db.add(prefs)
        await db.commit()
    return success({"theme": prefs.theme, "language": prefs.language,
                    "reduced_motion": prefs.reduced_motion, "density": prefs.density})


@router.put("/preferences", summary="更新个人偏好（含主题）")
async def update_preferences(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    prefs = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user.user_id))
    ).scalars().first()
    if prefs is None:
        prefs = UserPreference(id=new_id(), user_id=user.user_id)
        db.add(prefs)
    before = {"theme": prefs.theme, "reduced_motion": prefs.reduced_motion}
    if payload.get("theme") in ("light", "dark", "system"):
        prefs.theme = payload["theme"]
    if "reduced_motion" in payload:
        prefs.reduced_motion = bool(payload["reduced_motion"])
    if payload.get("language"):
        prefs.language = payload["language"]
    after = {"theme": prefs.theme, "reduced_motion": prefs.reduced_motion}
    if before != after:
        await audit_service.record_audit(
            db, action="preference.update", action_label="更新个人偏好",
            object_type="user_preference", object_id=prefs.id,
            before=before, after=after, user=user, ctx=ctx,
        )
    await db.commit()
    return success(after, message="偏好已保存")


# ---------------- 能力配置 ----------------

@router.get("/local-ai", summary="本地 AI 配置")
async def get_local_ai(db: DbSession, user: CurrentUserDep) -> dict:
    cfg = (await db.execute(select(LocalAIConfig))).scalars().first()
    adapter = (await build_adapters(db, user.household_id)["local_ai"].status()).to_dict()
    return success({
        "protocol": cfg.protocol if cfg else "openai_compatible",
        "base_url": cfg.base_url if cfg else None,
        "model": cfg.model if cfg else None,
        "api_key_masked": mask(decrypt_secret(cfg.api_key_cipher)) if cfg and cfg.api_key_cipher else "",
        "timeout": cfg.timeout if cfg else 60,
        "context_length": cfg.context_length if cfg else 8192,
        "stream": cfg.stream if cfg else True,
        "enabled": cfg.enabled if cfg else False,
        "adapter": adapter,
        "protocols": [{"value": "openai_compatible", "label": "OpenAI 兼容接口"},
                      {"value": "ollama", "label": "Ollama"}],
    })


@router.put("/local-ai", summary="保存本地 AI 配置")
async def put_local_ai(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    result = await setup_service.save_local_ai(db, user.household_id, payload)
    await audit_service.record_audit(
        db, action="local_ai.update", action_label="保存本地 AI 配置",
        object_type="local_ai", after={"status": result["status"], "base_url": payload.get("base_url"),
                                       "api_key_masked": mask(payload.get("api_key")) if payload.get("api_key") else ""},
        user=user, ctx=ctx,
    )
    await db.commit()
    return success(result, message="本地 AI 配置已保存")


@router.get("/ocr", summary="百度 OCR 配置")
async def get_ocr(db: DbSession, user: CurrentUserDep) -> dict:
    cfg = (await db.execute(select(OcrConfig))).scalars().first()
    adapter = (await build_adapters(db, user.household_id)["ocr"].status()).to_dict()
    return success({
        "provider": cfg.provider if cfg else "baidu",
        "api_key_masked": mask(decrypt_secret(cfg.api_key_cipher)) if cfg and cfg.api_key_cipher else "",
        "secret_key_masked": mask(decrypt_secret(cfg.secret_key_cipher)) if cfg and cfg.secret_key_cipher else "",
        "region": cfg.region if cfg else None,
        "endpoint": cfg.endpoint if cfg else None,
        "enabled": cfg.enabled if cfg else False,
        "adapter": adapter,
    })


@router.put("/ocr", summary="保存百度 OCR 配置")
async def put_ocr(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    result = await setup_service.save_ocr(db, user.household_id, payload)
    await audit_service.record_audit(
        db, action="ocr.update", action_label="保存百度 OCR 配置",
        object_type="ocr", after={"status": result["status"], "region": payload.get("region")},
        user=user, ctx=ctx,
    )
    await db.commit()
    return success(result, message="百度 OCR 配置已保存（密钥加密存储）")


@router.get("/notifications", summary="通知渠道配置")
async def get_notifications(db: DbSession, user: CurrentUserDep) -> dict:
    channel = (await db.execute(select(NotificationChannel))).scalars().first()
    adapter = (await build_adapters(db, user.household_id)["notification"].status()).to_dict()
    return success({
        "channel_type": "pushplus",
        "channel_label": "PushPlus",
        "token_masked": mask(decrypt_secret(channel.token_cipher)) if channel and channel.token_cipher else "",
        "enabled": channel.enabled if channel else False,
        "last_sent_at": channel.last_sent_at.isoformat() if channel and channel.last_sent_at else None,
        "last_status": channel.last_status if channel else "unconfigured",
        "adapter": adapter,
        "reminder_types": [
            {"key": "repayment", "label": "还款提醒", "enabled": True},
            {"key": "credit_card_bill", "label": "信用卡账单", "enabled": True},
            {"key": "rent_due", "label": "租金到期", "enabled": True},
            {"key": "lease_expire", "label": "租约到期", "enabled": True},
            {"key": "asset_maintenance", "label": "资产维护", "enabled": False},
            {"key": "ai_suggestion", "label": "AI 建议", "enabled": False},
        ],
        "auto": _notification_auto(channel),
        "note": "已开启自动发送：后台每 30 分钟检查一次，按「提前天数 / 每天次数 / 每周期次数」发送；提醒页仍可手动补发。",
    })


@router.put("/notifications", summary="保存通知渠道配置")
async def put_notifications(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    result = await setup_service.save_notification(db, user.household_id, payload)
    await audit_service.record_audit(
        db, action="notification.update", action_label="保存通知渠道配置",
        object_type="notification", after={"status": result["status"], "enabled": bool(payload.get("enabled"))},
        user=user, ctx=ctx,
    )
    await db.commit()
    return success(result, message="通知渠道配置已保存")


def _notification_auto(channel) -> dict:
    """读出自动发送规则（与定时任务共用同一份解析逻辑）。"""
    from app.services.due_notify import _auto_config  # noqa: PLC2701 - 同一份规则，避免两处走偏

    return _auto_config(channel)


@router.put("/notifications/auto", summary="保存自动提醒规则（提前天数 / 每天次数 / 每周期次数）")
async def put_notification_auto(
    db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...),
) -> dict:
    require_action(user, "configure")
    channel = (await db.execute(select(NotificationChannel))).scalars().first()
    if channel is None:
        channel = NotificationChannel(id=new_id(), household_id=user.household_id,
                                      channel_type="pushplus", enabled=False)
        db.add(channel)

    try:
        raw = json.loads(channel.type_switches or "{}")
        if not isinstance(raw, dict):
            raw = {}
    except Exception:  # noqa: BLE001
        raw = {}

    days_before = payload.get("days_before", 3)
    times_per_day = payload.get("times_per_day", 1)
    max_per_period = payload.get("max_per_period", 3)
    hours = payload.get("hours", [9])
    try:
        days_before = max(0, min(30, int(days_before)))
        times_per_day = max(1, min(6, int(times_per_day)))
        max_per_period = max(1, min(30, int(max_per_period)))
        hours = sorted({max(0, min(23, int(h))) for h in (hours or [9])})
    except Exception:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION_ERROR, "自动提醒参数不合法", status_code=422)

    raw["auto"] = {
        "enabled": bool(payload.get("enabled", True)),
        "days_before": days_before,
        "times_per_day": times_per_day,
        "max_per_period": max_per_period,
        "hours": hours or [9],
    }
    channel.type_switches = json.dumps(raw, ensure_ascii=False)
    channel.updated_by = user.user_id

    await audit_service.record_audit(
        db, action="notification.auto.update", action_label="保存自动提醒规则",
        object_type="notification", after=raw["auto"], user=user, ctx=ctx,
    )
    await db.commit()
    return success(raw["auto"], message="自动提醒规则已保存")


@router.get("/beike", summary="贝壳开放平台配置")
async def get_beike(db: DbSession, user: CurrentUserDep) -> dict:
    cfg = await beike_service.get_config(db, user.household_id)
    adapter = (await beike_service.status(db, user.household_id)).to_dict()
    return success({
        "app_key_masked": mask(decrypt_secret(cfg.app_key_cipher)) if cfg and cfg.app_key_cipher else "",
        "app_secret_masked": mask(decrypt_secret(cfg.app_secret_cipher)) if cfg and cfg.app_secret_cipher else "",
        "base_url": cfg.base_url if cfg else "https://gw-open.ke.com",
        "default_city": cfg.default_city if cfg else None,
        "default_city_code": cfg.default_city_code if cfg else None,
        "enabled": cfg.enabled if cfg else False,
        "adapter": adapter,
        "note": "贝壳开放平台（open.ke.com）：OAuth2.0 鉴权，用于房产估价（壳e估 /api/assessPrice）。",
    })


@router.put("/beike", summary="保存贝壳开放平台配置")
async def put_beike(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    cfg = await beike_service.get_config(db, user.household_id)
    if cfg is None:
        cfg = BeikeConfig(id=new_id(), household_id=user.household_id)
        db.add(cfg)

    base_url = (payload.get("base_url") or "https://gw-open.ke.com").strip()
    if not base_url.startswith(("http://", "https://")):
        raise AppError(ErrorCode.VALIDATION_ERROR, "网关地址必须以 http(s):// 开头",
                       field_errors={"base_url": "请填写完整地址"}, status_code=422)
    try:
        validate_outbound_url(base_url)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc),
                       field_errors={"base_url": str(exc)}, status_code=422)

    changed = False
    if payload.get("app_key"):
        cfg.app_key_cipher = encrypt_secret(payload["app_key"])
        changed = True
    if payload.get("app_secret"):
        cfg.app_secret_cipher = encrypt_secret(payload["app_secret"])
        changed = True
    cfg.base_url = base_url
    cfg.default_city = payload.get("default_city") or None
    cfg.default_city_code = payload.get("default_city_code") or None
    cfg.enabled = bool(payload.get("enabled", False))
    if changed or not cfg.last_status:
        cfg.last_status = "configured" if (cfg.app_key_cipher and cfg.app_secret_cipher) else "unconfigured"
        beike_service._TOKEN_CACHE.clear()  # 换密钥后旧的 token 不能再用

    await audit_service.record_audit(
        db, action="beike.update", action_label="保存贝壳开放平台配置",
        object_type="beike", after={"enabled": cfg.enabled, "city": cfg.default_city},
        user=user, ctx=ctx,
    )
    await db.commit()
    return success({"enabled": cfg.enabled, "status": cfg.last_status},
                   message="贝壳开放平台配置已保存（密钥加密存储）")


@router.post("/beike/test", summary="测试贝壳开放平台连接")
async def test_beike(db: DbSession, ctx: Ctx, user: CurrentUserDep) -> dict:
    require_action(user, "configure")
    status = await beike_service.test(db, user.household_id)
    await audit_service.record_audit(
        db, action="beike.test", action_label="测试贝壳开放平台",
        object_type="beike", after={"status": status.status},
        result="success" if status.status == "connected" else "failure",
        external_provider="beike", user=user, ctx=ctx,
    )
    await db.commit()
    return success(status.to_dict(), message=status.status_label)


@router.get("/ai-providers", summary="第三方 AI 提供商列表")
async def list_providers(db: DbSession, user: CurrentUserDep) -> dict:
    # 配置项（含 base_url 与掩码密钥）仅管理员可读，普通成员不应越权查看（M-3）
    require_action(user, "configure")
    rows = list((await db.execute(
        select(ThirdPartyAIProvider).where(
            ThirdPartyAIProvider.household_id == user.household_id,
            ThirdPartyAIProvider.deleted_at.is_(None),
        )
    )).scalars().all())
    items = [
        {
            "id": r.id, "name": r.name, "interface_type": r.interface_type,
            "base_url": r.base_url, "api_key_masked": mask(decrypt_secret(r.api_key_cipher)) if r.api_key_cipher else "",
            "default_model": r.default_model, "fallback_model": r.fallback_model,
            "enabled": r.enabled, "priority": r.priority, "last_status": r.last_status,
            "allow_attachment": r.allow_attachment, "allow_sensitive": r.allow_sensitive,
            "timeout": r.timeout, "stream": r.stream,
        }
        for r in rows
    ]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/ai-providers", summary="新增第三方 AI 提供商")
async def create_provider(db: DbSession, ctx: Ctx, user: CurrentUserDep,
                          payload: dict = Body(...)) -> dict:
    """新增一个第三方 AI 接口。创建后默认停用，需再走「启用」的二次确认。"""
    require_action(user, "configure")

    name = (payload.get("name") or "").strip()
    base_url = (payload.get("base_url") or "").strip().rstrip("/")
    api_key = (payload.get("api_key") or "").strip()
    field_errors: dict[str, str] = {}
    if not name:
        field_errors["name"] = "请填写提供商名称（例如 DeepSeek / 阿里百炼）"
    if not base_url:
        field_errors["base_url"] = "请填写接口地址（Base URL）"
    elif not base_url.startswith(("https://", "http://")):
        field_errors["base_url"] = "接口地址需以 http:// 或 https:// 开头"
    else:
        try:
            validate_outbound_url(base_url)
        except ValueError as exc:
            field_errors["base_url"] = str(exc)
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "配置不完整", field_errors=field_errors)

    duplicate = (
        await db.execute(
            select(ThirdPartyAIProvider).where(
                ThirdPartyAIProvider.household_id == user.household_id,
                ThirdPartyAIProvider.name == name,
                ThirdPartyAIProvider.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if duplicate is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "同名提供商已存在",
                       field_errors={"name": "该名称已被使用"})

    from app.core.crypto import encrypt_secret

    provider = ThirdPartyAIProvider(
        id=new_id(),
        household_id=user.household_id,
        name=name,
        interface_type=(payload.get("interface_type") or "openai_compatible"),
        base_url=base_url,
        api_key_cipher=encrypt_secret(api_key) if api_key else None,
        default_model=(payload.get("default_model") or "").strip() or None,
        fallback_model=(payload.get("fallback_model") or "").strip() or None,
        timeout=int(payload.get("timeout") or 60),
        stream=bool(payload.get("stream", True)),
        # 敏感字段一律不外发：脱敏在 service 层强制，这里不接受「允许敏感」的入参
        allow_attachment=False,
        allow_sensitive=False,
        enabled=False,
        priority=int(payload.get("priority") or 100),
        last_status="unconfigured",
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(provider)
    await audit_service.record_audit(
        db, action="ai_provider.create", action_label="新增第三方 AI 提供商",
        object_type="ai_provider", object_id=provider.id,
        after={"name": name, "base_url": base_url, "default_model": provider.default_model},
        external_provider=name, user=user, ctx=ctx,
    )
    await db.commit()
    return success({"id": provider.id, "name": name, "enabled": False},
                   message="已新增，建议先测试连接，再启用")


@router.put("/ai-providers/{provider_id}", summary="更新第三方 AI（启用需二次确认）")
async def update_provider(db: DbSession, ctx: Ctx, user: CurrentUserDep, provider_id: str,
                          payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    provider = (
        await db.execute(
            select(ThirdPartyAIProvider).where(
                ThirdPartyAIProvider.id == provider_id,
                ThirdPartyAIProvider.household_id == user.household_id,
            )
        )
    ).scalars().first()
    if provider is None:
        raise AppError(ErrorCode.NOT_FOUND, "提供商不存在", status_code=404)
    before = {"enabled": provider.enabled, "priority": provider.priority,
              "default_model": provider.default_model}
    if payload.get("enabled") is True:
        # 启用第三方 AI 属于高影响动作：必须二次确认 + 隐私总开关允许
        confirm_service.consume_token(payload.get("confirm_token"), user,
                                      "provider.enable", "ai_provider", provider_id)
        policy = (await db.execute(select(PrivacyPolicy).where(
            PrivacyPolicy.household_id == user.household_id))).scalars().first()
        if policy is None or not policy.cloud_egress_enabled:
            raise AppError(ErrorCode.FORBIDDEN,
                           "云端外发总开关未开启，无法启用第三方 AI 接口", status_code=403)
        provider.enabled = True
    elif payload.get("enabled") is False:
        provider.enabled = False
    if "priority" in payload:
        provider.priority = int(payload["priority"])
    if payload.get("default_model"):
        provider.default_model = payload["default_model"]
    if payload.get("api_key"):
        from app.core.crypto import encrypt_secret

        provider.api_key_cipher = encrypt_secret(payload["api_key"])
    after = {"enabled": provider.enabled, "priority": provider.priority,
             "default_model": provider.default_model}
    await audit_service.record_audit(
        db, action="ai_provider.update", action_label="更新第三方 AI 提供商",
        object_type="ai_provider", object_id=provider_id, before=before, after=after,
        external_provider=provider.name, reason=payload.get("reason"), user=user, ctx=ctx,
    )
    await db.commit()
    return success(after, message="第三方 AI 提供商已更新")


@router.get("/ai-providers/{provider_id}/egress-preview", summary="外发字段预览")
async def egress_preview(db: DbSession, user: CurrentUserDep, provider_id: str) -> dict:
    require_action(user, "configure")
    provider = (
        await db.execute(
            select(ThirdPartyAIProvider).where(
                ThirdPartyAIProvider.id == provider_id,
                ThirdPartyAIProvider.household_id == user.household_id,
            )
        )
    ).scalars().first()
    if provider is None:
        raise AppError(ErrorCode.NOT_FOUND, "提供商不存在", status_code=404)
    return success({
        "provider": provider.name,
        "fields": [
            {"field": "question", "label": "用户提问", "desensitized": False},
            {"field": "statistics", "label": "确定性统计结果（不含敏感字段）", "desensitized": True},
            {"field": "context", "label": "页面上下文（期间/币种/对象ID）", "desensitized": True},
        ],
        "excluded": ["密码", "Token", "Secret", "完整证件号", "完整银行卡号"],
        "enabled": provider.enabled,
    })


@router.delete("/ai-providers/{provider_id}", summary="撤销第三方 AI 配置（需二次确认）")
async def delete_provider(db: DbSession, ctx: Ctx, user: CurrentUserDep, provider_id: str,
                          reason: str = Query(None), confirm_token: str = Query(None)) -> dict:
    from datetime import datetime, timezone

    require_action(user, "configure")
    confirm_service.consume_token(confirm_token, user, "provider.delete", "ai_provider", provider_id)
    provider = (
        await db.execute(
            select(ThirdPartyAIProvider).where(
                ThirdPartyAIProvider.id == provider_id,
                ThirdPartyAIProvider.household_id == user.household_id,
            )
        )
    ).scalars().first()
    if provider is None:
        raise AppError(ErrorCode.NOT_FOUND, "提供商不存在", status_code=404)
    # 软删除，与全系统业务对象一致：保留审计与外发记录可追溯（M-6）
    provider.deleted_at = datetime.now(timezone.utc)
    provider.enabled = False
    provider.updated_by = user.user_id
    await audit_service.record_audit(
        db, action="ai_provider.revoke", action_label="撤销第三方 AI 配置",
        object_type="ai_provider", object_id=provider_id,
        before={"name": provider.name, "enabled": provider.enabled},
        reason=reason, user=user, ctx=ctx,
    )
    await db.commit()
    return success({"revoked": provider_id}, message="第三方 AI 配置已撤销")


# ---------------- 隐私与安全 ----------------

@router.get("/privacy", summary="隐私与安全")
async def get_privacy(db: DbSession, user: CurrentUserDep) -> dict:
    policy = (
        await db.execute(select(PrivacyPolicy).where(PrivacyPolicy.household_id == user.household_id))
    ).scalars().first()
    if policy is None:
        policy = PrivacyPolicy(id=new_id(), household_id=user.household_id)
        db.add(policy)
        await db.commit()
    return success({
        "file_retention_days": policy.file_retention_days,
        "ai_session_retention_days": policy.ai_session_retention_days,
        "mask_sensitive": policy.mask_sensitive,
        "cloud_egress_enabled": policy.cloud_egress_enabled,
        "external_call_audit": policy.external_call_audit,
    })


@router.put("/privacy", summary="更新隐私与安全")
async def put_privacy(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    policy = (
        await db.execute(select(PrivacyPolicy).where(PrivacyPolicy.household_id == user.household_id))
    ).scalars().first()
    if policy is None:
        policy = PrivacyPolicy(id=new_id(), household_id=user.household_id)
        db.add(policy)
    before = {"cloud_egress_enabled": policy.cloud_egress_enabled,
              "mask_sensitive": policy.mask_sensitive,
              "file_retention_days": policy.file_retention_days,
              "ai_session_retention_days": policy.ai_session_retention_days}
    if "cloud_egress_enabled" in payload and bool(payload["cloud_egress_enabled"]) != policy.cloud_egress_enabled:
        confirm_service.consume_token(payload.get("confirm_token"), user,
                                      "privacy.cloud_egress", "privacy", policy.id)
        policy.cloud_egress_enabled = bool(payload["cloud_egress_enabled"])
    for field in ("mask_sensitive", "external_call_audit"):
        if field in payload:
            setattr(policy, field, bool(payload[field]))
    for field in ("file_retention_days", "ai_session_retention_days"):
        if field in payload:
            setattr(policy, field, int(payload[field]))
    after = {"cloud_egress_enabled": policy.cloud_egress_enabled,
             "mask_sensitive": policy.mask_sensitive,
             "file_retention_days": policy.file_retention_days,
             "ai_session_retention_days": policy.ai_session_retention_days}
    await audit_service.record_audit(
        db, action="privacy.update", action_label="更新隐私与安全",
        object_type="privacy", object_id=policy.id, before=before, after=after,
        user=user, ctx=ctx,
    )
    await db.commit()
    return success(after, message="隐私与安全设置已更新")


# ---------------- 备份与恢复 ----------------

@router.get("/backup", summary="备份列表与状态")
async def list_backup(db: DbSession, user: CurrentUserDep) -> dict:
    rows = await backup_service.list_backups(db, user.household_id)
    items = [backup_service.serialize_backup(r) for r in rows]
    return list_payload(items, len(items), 1, len(items) or 1)


@router.post("/backup/create", summary="创建备份（需二次确认）")
async def create_backup(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(default={})) -> dict:
    require_action(user, "configure")
    confirm_service.consume_token(payload.get("confirm_token"), user, "backup.create", "backup", None)
    record = await backup_service.create_backup(db, user, ctx)
    await db.commit()
    return success(backup_service.serialize_backup(record), message="备份已创建")


@router.post("/backup/{backup_id}/verify", summary="验证备份完整性")
async def verify_backup(db: DbSession, ctx: Ctx, user: CurrentUserDep, backup_id: str) -> dict:
    require_action(user, "configure")
    result = await backup_service.verify_backup(db, user, backup_id, ctx)
    await db.commit()
    return success(result, message="完整性验证完成" if result["integrity"] else "完整性验证失败")


@router.get("/backup/{backup_id}/restore-preview", summary="恢复预览")
async def restore_preview(db: DbSession, user: CurrentUserDep, backup_id: str) -> dict:
    require_action(user, "configure")
    return success(await backup_service.restore_preview(db, user, backup_id))


@router.post("/backup/{backup_id}/restore", summary="执行恢复（需二次确认短语）")
async def restore_execute(db: DbSession, ctx: Ctx, user: CurrentUserDep, backup_id: str,
                          payload: dict = Body(...)) -> dict:
    require_action(user, "configure")
    record = await backup_service.restore_execute(db, user, backup_id,
                                                  payload.get("confirm_phrase", ""), ctx)
    await db.commit()
    return success(backup_service.serialize_restore(record),
                   message="恢复请求已提交，系统将进入维护锁定后执行")


# ---------------- 二次确认令牌 ----------------

@router.post("/confirm-tokens", summary="申请二次确认令牌")
async def issue_confirm_token(db: DbSession, user: CurrentUserDep, payload: dict = Body(...)) -> dict:
    action = payload.get("action")
    object_type = payload.get("object_type")
    if not action or not object_type:
        raise AppError(ErrorCode.VALIDATION_ERROR, "缺少确认动作或对象类型")
    return success(confirm_service.issue_token(user, action, object_type, payload.get("object_id")),
                   message="请确认后再执行")


# ---------------- 系统状态与审计入口 ----------------

@router.get("/system", summary="系统状态")
async def settings_system(db: DbSession, user: CurrentUserDep) -> dict:
    data = await catalog_service.system_status(db, user.household_id)
    # 审计记录属于 audit_view 权限范围（owner/admin）。这里原先把最近 10 条顺手塞进系统状态，
    # 等于让 member / readonly 绕过权限看到「谁改了什么」——要么该给，要么不该给，不能靠附带。
    if "audit_view" in user.action_set:
        audit_rows, total = await audit_service.list_audit_logs(
            db, household_id=user.household_id, page=1, page_size=10
        )
        data["recent_audit"] = [audit_service.serialize_audit(r) for r in audit_rows]
        data["audit_total"] = total
    else:
        data["recent_audit"] = []
        data["audit_total"] = None
    return success(data)
