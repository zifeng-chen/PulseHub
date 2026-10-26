"""v1 路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import ai, audit, auth, dashboard, health, insights, ledger, settings, setup, system

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(setup.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(ledger.router)
api_router.include_router(insights.router)
api_router.include_router(settings.router)
api_router.include_router(ai.router)
api_router.include_router(system.router)
api_router.include_router(audit.router)
