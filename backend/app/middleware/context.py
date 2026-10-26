"""请求上下文中间件：请求 ID、耗时、IP 摘要（08 §10、24 §11）。"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.ids import new_request_id

logger = logging.getLogger("fam.request")


def _ip_summary(request: Request) -> str:
    """IP 摘要：仅保留网段，符合"IP 摘要"而非完整地址的审计要求。"""
    client = request.client
    if client is None or not client.host:
        return "—"
    host = client.host
    if ":" in host:  # IPv6
        return ":".join(host.split(":")[:3]) + "::"
    parts = host.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.x"
    return host


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or new_request_id()
        request.state.request_id = request_id
        request.state.ip_summary = _ip_summary(request)
        request.state.source_page = request.headers.get("X-Source-Page")
        started = time.perf_counter()

        response = await call_next(request)

        # 安全响应头：API 也可能被直接访问（18000 端口），不能只依赖前端 Nginx
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            # 财务数据不落浏览器/代理缓存
            response.headers.setdefault("Cache-Control", "no-store")

        duration_ms = int((time.perf_counter() - started) * 1000)
        request.state.duration_ms = duration_ms
        response.headers["X-Request-Id"] = request_id
        if not request.url.path.startswith(("/health", "/docs", "/openapi")):
            logger.info(
                "%s %s -> %s (%sms)",
                request.method, request.url.path, response.status_code, duration_ms,
                extra={"extra_fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "ip": request.state.ip_summary,
                }},
            )
        return response
