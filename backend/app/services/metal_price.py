"""贵金属行情服务：每日金价 + 回收价（估值用）。

数据源（公开、免密钥）：`https://tmini.net/api/gold-price`
返回三组数据：
- metals：上海黄金交易所 AU9999、黄金 T+D、伦敦金、白银/铂金/钯金（元/克 或 美元/盎司）
- banks：银行投资金条报价
- recycle：**回收价**（足金 / 22K / 18K / 14K、铂金、钯金、白银）

设计要点：
- 进程内缓存（默认 30 分钟），避免每次打开页面都打外部接口；
- 外部不可达时返回 `available=false` + 上次成功缓存（若有），不阻断业务；
- 只做只读行情，不写入任何业务表；估值由调用方按需使用。
"""

from __future__ import annotations

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
TIMEOUT_SECONDS = 8.0

# 缓存：{"payload": dict, "fetched_at": float}
_CACHE: dict[str, Any] = {"payload": None, "fetched_at": 0.0}


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


async def fetch_prices(force: bool = False) -> dict:
    """获取最新行情（带缓存）。永不抛错：失败时返回 available=false 与上次成功结果。"""
    now = time.monotonic()
    cached = _CACHE.get("payload")
    if not force and cached and (now - float(_CACHE.get("fetched_at") or 0)) < CACHE_TTL_SECONDS:
        return {**cached, "cached": True}

    # 测试环境不访问外网：由用例通过 set_cached_payload 注入固定行情
    from app.core.config import settings
    if settings.app_env == "testing" and cached is None:
        return {
            "available": False, "stale": False, "source": SOURCE_LABEL, "source_url": SOURCE_URL,
            "gold": None, "recycle": [], "cached": False,
            "detail": "测试环境跳过外部行情请求",
        }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(SOURCE_URL, params={"type": "json"},
                                    headers={"User-Agent": "PulseHub/2.1 (+family finance)"})
            resp.raise_for_status()
            raw = resp.json()
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
        return {**payload, "cached": False}
    except Exception as exc:  # noqa: BLE001 - 行情失败不能影响记账与估值主流程
        logger.warning("金价行情获取失败：%s", exc)
        if cached:
            return {**cached, "available": False, "stale": True,
                    "detail": f"行情接口暂不可用（{exc.__class__.__name__}），显示上次成功获取的数据", "cached": True}
        return {
            "available": False,
            "stale": False,
            "source": SOURCE_LABEL,
            "source_url": SOURCE_URL,
            "gold": None,
            "recycle": [],
            "detail": f"行情接口暂不可用（{exc.__class__.__name__}）",
            "cached": False,
        }


def set_cached_payload(payload: dict | None) -> None:
    """测试注入：写入固定行情（payload=None 表示清空）。"""
    _CACHE["payload"] = payload
    _CACHE["fetched_at"] = time.monotonic()


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
