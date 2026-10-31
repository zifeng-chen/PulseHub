"""贵金属行情服务：每日金价 + 回收价（估值用）。

数据源（公开、免密钥）：`https://tmini.net/api/gold-price`
返回三组数据：
- metals：上海黄金交易所 AU9999、黄金 T+D、伦敦金、白银/铂金/钯金（元/克 或 美元/盎司）
- banks：银行投资金条报价
- recycle：**回收价**（足金 / 22K / 18K / 14K、铂金、钯金、白银）

设计要点（为什么这么写）：
- 进程内缓存（默认 30 分钟），避免每次打开页面都打外部接口；
- **有旧数据就立刻返回，刷新放后台**：行情是「锦上添花」的数据，不该让页面
  等一个可能连不上的外部接口；此前是同步等超时才回退旧值，外网不通时每次进
  贵金属页都要卡满超时时间；
- **失败负缓存**：一次失败后 5 分钟内直接返回不可用，不重复打外部接口——否则
  缓存过期后每次点进来都要再等一次超时；
- 超时收紧到 2.5s：外部行情慢到 2.5s 以上时，用户已经在等页面了；
- 只做只读行情，不写入任何业务表；估值由调用方按需使用。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SOURCE_URL = "https://tmini.net/api/gold-price"
SOURCE_LABEL = "tmini 金价行情（含上金所 AU9999 与回收价）"
CACHE_TTL_SECONDS = 30 * 60
# 外部行情慢到这个程度，用户已经在页面里干等了；宁可先给旧数据/不可用
TIMEOUT_SECONDS = 2.5
# 失败后冷却：这段时间内直接返回不可用，避免每次请求都再等一次超时
FAIL_COOLDOWN_SECONDS = 5 * 60

# 缓存：{"payload": dict, "fetched_at": float, "failed_at": float}
_CACHE: dict[str, Any] = {"payload": None, "fetched_at": 0.0, "failed_at": 0.0}
# 后台刷新任务（同一时刻只允许一个，避免客户端同时刷新时打爆外部接口）
_REFRESH_TASK: asyncio.Task | None = None


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _pick_metal(metals: list[dict], *names: str) -> dict | None:
    """按名称优先级取一个品种（名称里可能带下划线/空格差异）。"""
    normalized = [
        {"name": (m.get("name") or "").replace("_", "").replace(" ", ""), "raw": m}
        for m in metals if isinstance(m, dict)
    ]
    for name in names:
        key = name.replace("_", "").replace(" ", "")
        for item in normalized:
            if key and key in item["name"]:
                return item["raw"]
    return None


def _build_payload(raw: dict) -> dict:
    """把外部接口结构整理成本系统使用的稳定结构（金额一律字符串，避免浮点误差）。"""
    metals: list[dict] = raw.get("metals") or []
    recycle: list[dict] = raw.get("recycle") or []
    banks: list[dict] = raw.get("banks") or []

    au9999 = _pick_metal(metals, "黄金_9999", "黄金9999")
    silver = _pick_metal(metals, "白银价格", "白银")
    platinum = _pick_metal(metals, "铂金价格", "铂金")
    palladium = _pick_metal(metals, "钯金价格", "钯金")
    london = _pick_metal(metals, "伦敦金")

    def quote(item: dict | None) -> dict | None:
        if not item:
            return None
        price = _dec(item.get("sell_price"))
        if price is None:
            return None
        return {
            "price": str(price),
            "unit": item.get("unit") or "元/克",
            "high": str(_dec(item.get("high_price"))) if _dec(item.get("high_price")) is not None else None,
            "low": str(_dec(item.get("low_price"))) if _dec(item.get("low_price")) is not None else None,
            "updated": item.get("updated"),
        }

    # 回收价：按「纯度」归类，供估值时按纯度选用
    recycle_items: list[dict] = []
    for row in recycle:
        if not isinstance(row, dict):
            continue
        price = _dec(row.get("price"))
        if price is None:
            continue
        recycle_items.append({
            "type": row.get("type") or "",
            "purity": row.get("purity") or "",
            "price": str(price),
            "unit": row.get("unit") or "元/克",
            "updated": row.get("updated"),
        })

    def recycle_price(*keywords: str) -> str | None:
        for keyword in keywords:
            for row in recycle_items:
                if keyword in row["type"]:
                    return row["price"]
        return None

    bank_items = [
        {"bank": b.get("bank") or "", "price": str(_dec(b.get("price")))}
        for b in banks
        if isinstance(b, dict) and _dec(b.get("price")) is not None
    ]

    return {
        "date": raw.get("date"),
        "gold": quote(au9999),            # 上金所 AU9999，元/克 —— 估值基准
        "silver": quote(silver),
        "platinum": quote(platinum),
        "palladium": quote(palladium),
        "london": quote(london),
        "recycle": recycle_items,
        # 常用回收价快捷字段（前端展示与估值兜底）
        "recycle_gold": recycle_price("黄金回收", "足金"),
        "recycle_22k": recycle_price("22k"),
        "recycle_18k": recycle_price("18k"),
        "recycle_platinum": recycle_price("铂金"),
        "recycle_palladium": recycle_price("钯金"),
        "recycle_silver": recycle_price("白银"),
        "bank_bars": bank_items,
    }


async def _request_source() -> dict:
    """请求外部行情。单独抽出来便于测试替换，也把超时集中在一处。"""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        resp = await client.get(SOURCE_URL, params={"type": "json"},
                                headers={"User-Agent": "PulseHub/2.1 (+family finance)"})
        resp.raise_for_status()
        return resp.json()


def _unavailable(detail: str) -> dict:
    return {
        "available": False, "stale": False, "source": SOURCE_LABEL, "source_url": SOURCE_URL,
        "gold": None, "recycle": [], "detail": detail, "cached": False,
    }


def _in_fail_cooldown(now: float) -> bool:
    failed_at = float(_CACHE.get("failed_at") or 0)
    return failed_at > 0 and (now - failed_at) < FAIL_COOLDOWN_SECONDS


async def _fetch_remote(force: bool) -> dict:
    """真正打外部接口。失败时写失败时间戳，并尽量回退旧数据。"""
    now = time.monotonic()
    cached = _CACHE.get("payload")
    try:
        raw = await _request_source()
        payload = _build_payload(raw if isinstance(raw, dict) else {})
        if payload.get("gold") is None and not payload.get("recycle"):
            raise ValueError("行情数据为空")
        payload.update({
            "available": True,
            "source": SOURCE_LABEL,
            "source_url": SOURCE_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
        })
        _CACHE["payload"] = payload
        _CACHE["fetched_at"] = now
        _CACHE["failed_at"] = 0.0
        return {**payload, "cached": False}
    except Exception as exc:  # noqa: BLE001 - 行情失败不能影响记账与估值主流程
        _CACHE["failed_at"] = time.monotonic()
        logger.warning("金价行情获取失败：%s", exc)
        if cached:
            return {**cached, "available": False, "stale": True,
                    "detail": f"行情接口暂不可用（{exc.__class__.__name__}），显示上次成功获取的数据",
                    "cached": True}
        return _unavailable(f"行情接口暂不可用（{exc.__class__.__name__}）")


async def _refresh_in_background() -> None:
    try:
        await _fetch_remote(force=True)
    except Exception:  # noqa: BLE001 - 后台任务不能把异常抛到事件循环
        logger.debug("后台刷新行情失败", exc_info=True)


def _schedule_refresh() -> None:
    """后台刷新（失败冷却期内不重试）。拿不到事件循环就跳过，不影响本次返回。"""
    global _REFRESH_TASK
    if _REFRESH_TASK is not None and not _REFRESH_TASK.done():
        return
    if _in_fail_cooldown(time.monotonic()):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _REFRESH_TASK = loop.create_task(_refresh_in_background())


async def fetch_prices(force: bool = False) -> dict:
    """获取行情（带缓存）。

    取数顺序：
    1. 缓存新鲜 → 直接返回；
    2. 有旧数据 → **立刻返回旧数据**（stale），后台刷新，不让页面等外部接口；
    3. 刚失败过（5 分钟内）→ 直接返回不可用，不重复等超时；
    4. 以上都不满足（首次无缓存）→ 同步请求，最多等 TIMEOUT_SECONDS。

    `force=True` 是用户主动点刷新：绕过缓存与冷却，同步请求。
    """
    now = time.monotonic()
    cached = _CACHE.get("payload")
    fetched_at = float(_CACHE.get("fetched_at") or 0)

    if not force and cached and (now - fetched_at) < CACHE_TTL_SECONDS:
        return {**cached, "cached": True}

    # 测试环境不访问外网：由用例通过 set_cached_payload 注入固定行情
    from app.core.config import settings
    if settings.app_env == "testing" and cached is None:
        return {
            "available": False, "stale": False, "source": SOURCE_LABEL, "source_url": SOURCE_URL,
            "gold": None, "recycle": [], "cached": False,
            "detail": "测试环境跳过外部行情请求",
        }

    if not force and cached:
        _schedule_refresh()
        return {
            **cached,
            "available": bool(cached.get("available")),
            "stale": True,
            "cached": True,
            "refreshing": True,
            "detail": "正在后台刷新行情，本次显示上次成功获取的数据",
        }

    if not force and _in_fail_cooldown(now):
        return _unavailable("行情接口暂时不可用，稍后会自动重试")

    return await _fetch_remote(force=force)


def set_cached_payload(payload: dict | None) -> None:
    """测试注入：写入固定行情（payload=None 表示清空）。顺带清掉失败冷却。"""
    _CACHE["payload"] = payload
    _CACHE["fetched_at"] = time.monotonic()
    _CACHE["failed_at"] = 0.0


async def gold_price_per_gram() -> Decimal | None:
    """上金所 AU9999 金价（元/克）；不可用时返回 None。"""
    prices = await fetch_prices()
    gold = prices.get("gold") or {}
    return _dec(gold.get("price"))


async def recycle_price_for(purity: Decimal | None, metal: str = "gold") -> Decimal | None:
    """按纯度返回收价（元/克）。

    回收价只对足金/22K/18K/14K 有公开报价：按纯度就近匹配，超出范围按比例折算，
    没有回收价时回落到金价；仍不可用时返回 None。
    """
    prices = await fetch_prices()
    if metal in ("silver", "platinum", "palladium"):
        key = {"silver": "recycle_silver", "platinum": "recycle_platinum",
               "palladium": "recycle_palladium"}[metal]
        return _dec(prices.get(key))

    ratio = float(purity) if purity is not None else 0.999
    if ratio > 1:  # 允许 999 / 9999 这种千分位写法
        ratio = ratio / (1000 if ratio < 1000 else 10000)
    for threshold, key in ((0.99, "recycle_gold"), (0.916, "recycle_22k"),
                           (0.75, "recycle_18k"), (0.585, "recycle_14k")):
        if ratio >= threshold:
            value = _dec(prices.get(key))
            if value is not None:
                return value
    gold = _dec((prices.get("gold") or {}).get("price"))
    if gold is None:
        return None
    # 低纯度没有公开回收价：按金价 × 纯度估算
    return (gold * Decimal(str(round(ratio, 4)))).quantize(Decimal("0.01"))
