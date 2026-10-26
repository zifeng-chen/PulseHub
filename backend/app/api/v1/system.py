"""系统状态与维护模式 API（08 §10、24 §9）。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body
from sqlalchemy import select

from app.api.deps import CurrentUserDep, Ctx, DbSession
from app.core.errors import AppError, ErrorCode, success
from app.core.ids import new_id, utc_iso
from app.db.models.config import MaintenanceWindow
from app.services import audit as audit_service
from app.services import catalog as catalog_service
from app.services import setup as setup_service

router = APIRouter(prefix="/system", tags=["系统"])


async def _refresh_maintenance_state(db: DbSession, app_state) -> dict:
    window = (
        await db.execute(select(MaintenanceWindow).where(MaintenanceWindow.status == "active"))
    ).scalars().first()
    state = {
        "active": window is not None,
        "reason": window.reason if window else None,
        "started_at": utc_iso(window.started_at) if window and window.started_at else None,
    }
    app_state.maintenance = state
    return state


@router.get("/status", summary="系统状态")
async def status(db: DbSession, user: CurrentUserDep) -> dict:
    return success(await catalog_service.system_status(db, user.household_id))


@router.get("/health", summary="健康检查（公开）")
async def health(db: DbSession) -> dict:
    return success(await setup_service.health(db))


@router.post("/maintenance/enter", summary="进入维护模式")
async def enter_maintenance(db: DbSession, ctx: Ctx, user: CurrentUserDep, payload: dict = Body(default={})) -> dict:
    if not user.is_system_admin:
        raise AppError(ErrorCode.FORBIDDEN, "仅系统管理员可进入维护模式", status_code=403)
    window = MaintenanceWindow(
        id=new_id(), reason=payload.get("reason") or "系统维护",
        status="active", started_at=datetime.now(timezone.utc),
        started_by=user.user_id, note=payload.get("note"),
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(window)
    await audit_service.record_audit(
        db, action="system.maintenance_enter", action_label="进入维护模式",
        object_type="maintenance", object_id=window.id,
        after={"reason": window.reason}, user=user, ctx=ctx,
    )
    await db.commit()
    from app.main import app  # 延迟导入避免循环

    return success(await _refresh_maintenance_state(db, app.state), message="已进入维护模式")


@router.post("/maintenance/exit", summary="退出维护模式")
async def exit_maintenance(db: DbSession, ctx: Ctx, user: CurrentUserDep) -> dict:
    if not user.is_system_admin:
        raise AppError(ErrorCode.FORBIDDEN, "仅系统管理员可退出维护模式", status_code=403)
    window = (
        await db.execute(select(MaintenanceWindow).where(MaintenanceWindow.status == "active"))
    ).scalars().first()
    if window is not None:
        window.status = "closed"
        window.ended_at = datetime.now(timezone.utc)
    await audit_service.record_audit(
        db, action="system.maintenance_exit", action_label="退出维护模式",
        object_type="maintenance", after={"active": False}, user=user, ctx=ctx,
    )
    await db.commit()
    from app.main import app

    return success(await _refresh_maintenance_state(db, app.state), message="已退出维护模式")
