"""工作档案服务（家庭成员的在职工作，只记录「与钱相关」的维度）。

- 在职状态（employed/probation/unemployed/retired/freelance）供 AI 现金流风险预警；
- 社保公积金基数作为「隐性资产」提示真实薪酬包，不参与金额计算；
- 每个成员最多一份在职档案（多份历史工作不在此管理范围）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id
from app.core.money import fmt_amount
from app.core.timeutil import local_now, to_local_naive
from app.db.models.business import EmploymentProfile
from app.services import audit as audit_service

EMPLOYMENT_EDITABLE = (
    "employer", "employment_status", "social_insurance_base", "housing_fund_base",
    "start_date", "end_date", "note",
)


def serialize(item: EmploymentProfile, *, member_name: str | None = None) -> dict:
    return {
        "id": item.id,
        "member_id": item.member_id,
        "member_name": member_name or "",
        "employer": item.employer,
        "employment_status": item.employment_status,
        "employment_status_label": enum_zh.zh("employment_status", item.employment_status),
        "social_insurance_base": fmt_amount(item.social_insurance_base) if item.social_insurance_base is not None else None,
        "housing_fund_base": fmt_amount(item.housing_fund_base) if item.housing_fund_base is not None else None,
        "start_date": item.start_date.isoformat() if item.start_date else None,
        "end_date": item.end_date.isoformat() if item.end_date else None,
        "note": item.note,
        "version": item.version,
    }


async def list_profiles(db: AsyncSession, user: CurrentUser) -> list[EmploymentProfile]:
    return list((await db.execute(
        select(EmploymentProfile).where(
            EmploymentProfile.household_id == user.household_id,
            EmploymentProfile.deleted_at.is_(None),
        ).order_by(EmploymentProfile.created_at)
    )).scalars().all())


async def get_profile(db: AsyncSession, user: CurrentUser, profile_id: str) -> EmploymentProfile:
    item = (await db.execute(
        select(EmploymentProfile).where(
            EmploymentProfile.id == profile_id,
            EmploymentProfile.household_id == user.household_id,
            EmploymentProfile.deleted_at.is_(None),
        )
    )).scalars().first()
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "工作档案不存在")
    return item


def _validate(payload: dict, *, partial: bool) -> tuple[dict, dict]:
    field_errors: dict[str, str] = {}
    data: dict = {}

    if "member_id" in payload or not partial:
        member_id = payload.get("member_id")
        if not member_id:
            field_errors["member_id"] = "请选择家庭成员"
        else:
            data["member_id"] = member_id

    if "employer" in payload:
        employer = (payload.get("employer") or "").strip()
        if len(employer) > 128:
            field_errors["employer"] = "雇主名称过长"
        else:
            data["employer"] = employer or None

    if "employment_status" in payload and payload["employment_status"] not in (None, ""):
        if payload["employment_status"] not in enum_zh.EMPLOYMENT_STATUS:
            field_errors["employment_status"] = "在职状态不合法"
        else:
            data["employment_status"] = payload["employment_status"]

    for num_field in ("social_insurance_base", "housing_fund_base"):
        if num_field in payload and payload[num_field] not in (None, ""):
            try:
                value = Decimal(str(payload[num_field]))
                if not value.is_finite() or value < 0:
                    raise ValueError("bad")
            except Exception:  # noqa: BLE001
                field_errors[num_field] = "基数必须是大于等于 0 的数字"
            else:
                data[num_field] = value

    for date_field in ("start_date", "end_date"):
        if date_field in payload:
            raw = payload.get(date_field)
            if raw in (None, ""):
                data[date_field] = None
            else:
                try:
                    data[date_field] = to_local_naive(
                        datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    )
                except ValueError:
                    field_errors[date_field] = "日期格式应为 ISO 8601"

    if "note" in payload:
        data["note"] = payload["note"] or None

    return data, field_errors


async def create_profile(
    db: AsyncSession, user: CurrentUser, payload: dict, ctx: RequestContext | None = None,
) -> EmploymentProfile:
    data, field_errors = _validate(payload, partial=False)
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "工作档案校验失败", field_errors=field_errors)

    # 每个成员最多一份在职档案，避免重复
    existing = (await db.execute(
        select(EmploymentProfile).where(
            EmploymentProfile.household_id == user.household_id,
            EmploymentProfile.member_id == data["member_id"],
            EmploymentProfile.deleted_at.is_(None),
        )
    )).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.STATE_INVALID, "该成员已有工作档案，请直接编辑", status_code=409)

    item = EmploymentProfile(
        id=new_id(),
        household_id=user.household_id or "",
        member_id=data["member_id"],
        employer=data.get("employer"),
        employment_status=data.get("employment_status", "employed"),
        social_insurance_base=data.get("social_insurance_base"),
        housing_fund_base=data.get("housing_fund_base"),
        start_date=data.get("start_date") or local_now(),
        end_date=data.get("end_date"),
        note=data.get("note"),
        version=1,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(item)
    await db.flush()
    await audit_service.record_audit(
        db, action="employment.create", action_label="新增工作档案",
        object_type="employment_profile", object_id=item.id,
        after={"member_id": item.member_id, "employer": item.employer,
               "employment_status": item.employment_status},
        user=user, ctx=ctx,
    )
    return item


async def update_profile(
    db: AsyncSession, user: CurrentUser, profile_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> EmploymentProfile:
    item = await get_profile(db, user, profile_id)
    data, field_errors = _validate(payload, partial=True)
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "工作档案校验失败", field_errors=field_errors)

    before = {f: getattr(item, f) for f in EMPLOYMENT_EDITABLE}
    for field, value in data.items():
        setattr(item, field, value)
    item.version += 1
    item.updated_by = user.user_id

    await audit_service.record_audit(
        db, action="employment.update", action_label="编辑工作档案",
        object_type="employment_profile", object_id=item.id,
        before=before, after={f: getattr(item, f) for f in EMPLOYMENT_EDITABLE},
        user=user, ctx=ctx,
    )
    return item


async def delete_profile(
    db: AsyncSession, user: CurrentUser, profile_id: str, ctx: RequestContext | None = None,
) -> None:
    item = await get_profile(db, user, profile_id)
    item.deleted_at = datetime.now(timezone.utc)
    item.version += 1
    item.updated_by = user.user_id
    await db.flush()
    await audit_service.record_audit(
        db, action="employment.delete", action_label="删除工作档案",
        object_type="employment_profile", object_id=item.id, user=user, ctx=ctx,
    )


async def member_profiles_map(db: AsyncSession, household_id: str) -> dict[str, EmploymentProfile]:
    """按 member_id 返回工作档案映射（供 AI 分析/统计批量取用）。"""
    rows = (await db.execute(
        select(EmploymentProfile).where(
            EmploymentProfile.household_id == household_id,
            EmploymentProfile.deleted_at.is_(None),
        )
    )).scalars().all()
    return {r.member_id: r for r in rows}
