"""请求级聚合缓存。

一次「脉枢台快照」请求里，同一批只读聚合会被不同模块反复用到：

- 账户余额：账户汇总、流动性/偿债覆盖、提醒里的信用卡欠款，各算一次；
- 收支汇总：快照本身一次，提醒里的 AI 建议再算一次；
- 负债剩余本金：还款提醒、额度使用率、一次性到期负债，各算一次。

每次都是一次全表 `group by`。同一请求内数据不会变，所以按「键 + 参数」缓存在
`db.info`，请求结束随 session 一起消失，不需要显式失效。

**使用边界**：只用于只读聚合。写操作（记一笔、还款、调账）之后要立刻拿最新值的
路径必须绕过这里——否则会读到写之前的结果。写完后确实需要最新值的地方，用
`invalidate()` 清掉对应前缀。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_KEY = "aggregate_cache"


def _bucket(db: AsyncSession) -> dict[str, Any]:
    return db.info.setdefault(_CACHE_KEY, {})


async def request_cached(
    db: AsyncSession, key: tuple, loader: Callable[[], Awaitable[Any]]
) -> Any:
    """取缓存，没有就跑一次 loader 并存下来。key 必须把影响结果的参数都带上。"""
    cache = _bucket(db)
    if key not in cache:
        cache[key] = await loader()
    return cache[key]


def invalidate(db: AsyncSession, prefix: str | None = None) -> None:
    """清缓存。prefix 为空时清全部；否则只清 key 第一段等于 prefix 的项。"""
    cache = _bucket(db)
    if prefix is None:
        cache.clear()
        return
    for key in [k for k in cache if isinstance(k, tuple) and k and k[0] == prefix]:
        cache.pop(key, None)
