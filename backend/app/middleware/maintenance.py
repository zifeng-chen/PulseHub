"""维护模式中间件（24 §9）。

维护模式下普通用户不能创建、确认、修改或删除业务数据，只能查看必要状态；
管理员仍可执行维护操作。写方法被拦截并返回 MAINTENANCE_MODE(503)。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.errors import ErrorCode, failure

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 维护模式下仍允许的路径前缀
ALLOW_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/setup",
    "/api/v1/system",
    "/health",
    "/docs",
    "/openapi",
)


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        state = getattr(request.app.state, "maintenance", None)
        if (
            state
            and state.get("active")
            and request.method in WRITE_METHODS
            and not request.url.path.startswith(ALLOW_PREFIXES)
        ):
            request_id = getattr(request.state, "request_id", "")
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": {
                        "code": ErrorCode.MAINTENANCE_MODE,
                        "message": "系统处于维护模式，暂时无法写入业务数据",
                        "field_errors": {},
                        "retryable": True,
                        "request_id": request_id,
                    },
                },
            )
        return await call_next(request)
