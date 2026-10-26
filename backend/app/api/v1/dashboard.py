"""脉枢台 API（05 §2、18 §2.4）。M0 返回口径与空状态，金额随业务模块填充。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, DbSession
from app.core.errors import success
from app.services import stats as stats_service
from app.services import workbench as workbench_service

router = APIRouter(prefix="/dashboard", tags=["脉枢台"])


@router.get("/snapshot", summary="脉枢台统计快照")
async def snapshot(
    db: DbSession,
    user: CurrentUserDep,
    period: str = Query("current_month"),
    currency: str | None = Query(None),
) -> dict:
    data = await stats_service.dashboard_snapshot(db, user, period, currency)
    return success(data, message="统计完成")


@router.get("/workbench", summary="家庭台账工作台（完整度 + 待办队列，不含合计金额）")
async def workbench(db: DbSession, user: CurrentUserDep) -> dict:
    data = await workbench_service.ledger_workbench(db, user)
    return success(data, message="台账状态已更新")
