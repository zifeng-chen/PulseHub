"""按来源 IP 的滑动窗口限流（24 §11）。

**它防的不是「密码错」，而是「挨个试」**：

- 账号锁定（`services/auth.py`）数的是「某个用户名错了几次」，所以攻击者
  只要每次换一个用户名，每个只试 1 次，就永远触发不了锁定，却能筛出
  哪些用户名真实存在（用户名枚举）；
- 这里数的是「某个来源一分钟发了多少请求」，换用户名也没用。

同时它兜住「脚本刷接口」：统计类接口一次几十毫秒，狂刷能把 NAS 的 CPU 打满。

**两条实现约束**（改的时候别破坏）：

1. **不能因为限流自己把服务拖垮**：桶数量有上限，超了就清理，再超就放行
   而不是拒绝——内存耗尽比限流失效严重得多；
2. **阈值每次请求都从 settings 读**，不缓存进实例，否则测试和运行时改配置不生效。
"""

from __future__ import annotations

import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.errors import ErrorCode, failure
from app.core.netutil import parse_trusted_proxies, resolve_client_ip

# 登录类接口用更严的阈值：这些是唯一「猜对了就能进来」的入口
LOGIN_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/verify-password",
})

WINDOW_SECONDS = 60.0


class RateLimiter:
    """进程内滑动窗口计数器。

    单容器单 worker 足够；进程重启即清零（对爆破防护无影响）。
    若将来扩到多 worker，每个进程各算一份，实际阈值会被放大 N 倍，
    届时需要换成共享存储（Redis 或数据库）——不要以为这里已经是全局计数。
    """

    __slots__ = ("_buckets", "_last_cleanup", "max_buckets")

    def __init__(self, max_buckets: int = 20000) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._last_cleanup = 0.0
        self.max_buckets = max_buckets

    def allow(self, key: str, limit: int, now: float, window: float = WINDOW_SECONDS) -> tuple[bool, float]:
        """返回 (是否放行, 建议等待秒数)。"""
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self.max_buckets:
                self._cleanup(now, window)
                if len(self._buckets) >= self.max_buckets:
                    # 内存优先：宁可这一条不限流，也不要 OOM 把整站拖死
                    return True, 0.0
            bucket = self._buckets[key] = deque()

        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            return False, max(0.0, bucket[0] + window - now)

        bucket.append(now)
        self._maybe_cleanup(now, window)
        return True, 0.0

    def _maybe_cleanup(self, now: float, window: float) -> None:
        if now - self._last_cleanup < 300:
            return
        self._cleanup(now, window)

    def _cleanup(self, now: float, window: float) -> None:
        cutoff = now - window
        stale = [k for k, v in self._buckets.items() if not v or v[-1] <= cutoff]
        for key in stale:
            self._buckets.pop(key, None)
        self._last_cleanup = now

    def reset(self) -> None:
        """测试用：清空所有计数。"""
        self._buckets.clear()
        self._last_cleanup = 0.0

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)


# 进程级单例：中间件与测试共用同一份计数
limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        # 只管业务接口；健康检查不限流（容器 healthcheck 每 30 秒一次，
        # 而且被限流会导致容器被判成不健康而重启）
        if not path.startswith("/api/") or path.endswith("/health"):
            return await call_next(request)

        ip = resolve_client_ip(
            request.client.host if request.client else "",
            request.headers.get("x-forwarded-for"),
            parse_trusted_proxies(settings.trusted_proxies),
        ) or "unknown"

        is_login = path in LOGIN_PATHS
        limit = (
            settings.rate_limit_login_per_minute
            if is_login
            else settings.rate_limit_api_per_minute
        )
        allowed, retry_after = limiter.allow(
            f"{'login' if is_login else 'api'}:{ip}", limit, time.monotonic()
        )
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content=failure(
                    ErrorCode.RATE_LIMITED,
                    "请求过于频繁，请稍后再试",
                    retryable=True,
                    request=request,
                ),
            )
            response.headers["Retry-After"] = str(int(retry_after) + 1)
            return response

        return await call_next(request)
