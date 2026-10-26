"""健康检查与元数据（01 §5、18 §2.1）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession
from app.core.errors import success
from app.services import catalog, setup as setup_service

router = APIRouter(tags=["系统"])


@router.get("/health", summary="健康检查")
async def health(db: DbSession) -> dict:
    return success(await setup_service.health(db))


@router.get("/meta/enums", summary="枚举中文映射字典")
async def enums() -> dict:
    return success(catalog.enum_dictionary())


@router.get("/meta/version", summary="版本信息")
async def version(db: DbSession) -> dict:
    from app.core.config import settings

    return success({
        "app_version": settings.app_version,
        "api_version": settings.api_version,
        "env": settings.app_env,
        "migration_version": await setup_service.current_migration_version(db),
    })
