"""审计 API（08 §10、18 §2.4）。审计记录只允许追加。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, DbSession
from app.core.errors import AppError, ErrorCode, list_payload, success
from app.services import audit as audit_service
from app.services.permissions import require_action

router = APIRouter(prefix="/audit", tags=["审计"])


@router.get("", summary="审计列表")
async def list_logs(
    db: DbSession,
    user: CurrentUserDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str | None = Query(None),
    object_type: str | None = Query(None),
) -> dict:
    require_action(user, "audit_view")
    rows, total = await audit_service.list_audit_logs(
        db, household_id=user.household_id, page=page, page_size=page_size,
        action=action, object_type=object_type,
    )
    return list_payload([audit_service.serialize_audit(r) for r in rows], total, page, page_size)


@router.get("/{log_id}", summary="审计详情（含字段级差异）")
async def detail(db: DbSession, user: CurrentUserDep, log_id: str) -> dict:
    require_action(user, "audit_view")
    log, changes = await audit_service.audit_detail(db, log_id, household_id=user.household_id)
    if log is None:
        raise AppError(ErrorCode.NOT_FOUND, "审计记录不存在", status_code=404)
    payload = audit_service.serialize_audit(log)
    payload["changes"] = [audit_service.serialize_change(c) for c in changes]
    return success(payload)
