"""贵金属行情的取数策略：超时、失败负缓存、旧数据先返回。

这块曾经让「贵金属估值」页每次进来卡 8 秒——外部接口连不上时同步等满超时才回退。
这里把三条约束钉住：超时上限、失败后不重复打外部接口、有旧数据不阻塞。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services import metal_price


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """每个用例从干净状态开始，并且把环境切到非 testing（否则会跳过外部请求）。"""
    monkeypatch.setattr(settings, "app_env", "development")
    metal_price._CACHE.update({"payload": None, "fetched_at": 0.0, "failed_at": 0.0})
    metal_price._REFRESH_TASK = None
    yield
    metal_price._CACHE.update({"payload": None, "fetched_at": 0.0, "failed_at": 0.0})
    metal_price._REFRESH_TASK = None


GOOD_RAW = {
    "date": "2026-10-31",
    "metals": [{"name": "黄金_9999", "sell_price": "912.50", "unit": "元/克"}],
    "recycle": [{"type": "黄金回收", "purity": "999", "price": "898.00", "unit": "元/克"}],
    "banks": [],
}


def test_timeout_is_tight():
    """超时必须收紧：8 秒会让页面像卡死，2.5 秒是可接受的等待上限。"""
    assert metal_price.TIMEOUT_SECONDS <= 3.0


async def test_first_call_failure_returns_unavailable_and_marks_failure(monkeypatch):
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(metal_price, "_request_source", boom)
    result = await metal_price.fetch_prices()

    assert result["available"] is False
    assert "不可用" in result["detail"]
    assert calls["n"] == 1
    assert metal_price._CACHE["failed_at"] > 0


async def test_repeated_calls_within_cooldown_skip_external_request(monkeypatch):
    """失败后 5 分钟内再来请求，不能每次都再等一次超时。"""
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(metal_price, "_request_source", boom)
    await metal_price.fetch_prices()
    assert calls["n"] == 1

    for _ in range(5):
        result = await metal_price.fetch_prices()
        assert result["available"] is False
    assert calls["n"] == 1, "冷却期内不应重复打外部接口"


async def test_cooldown_expires_and_retries(monkeypatch):
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise TimeoutError("connect timeout")

    monkeypatch.setattr(metal_price, "_request_source", boom)
    await metal_price.fetch_prices()
    # 手动把失败时间往前推，模拟冷却结束
    metal_price._CACHE["failed_at"] -= metal_price.FAIL_COOLDOWN_SECONDS + 1
    await metal_price.fetch_prices()
    assert calls["n"] == 2


async def test_stale_payload_returned_immediately_with_background_refresh(monkeypatch):
    """有旧数据时立刻返回旧数据，刷新放后台——不能让页面等外部接口。"""
    metal_price.set_cached_payload({
        "available": True, "stale": False, "gold": {"price": "900.00", "unit": "元/克"},
        "recycle": [], "fetched_at": "2026-10-30T00:00:00+00:00",
    })
    # 让缓存过期
    metal_price._CACHE["fetched_at"] -= metal_price.CACHE_TTL_SECONDS + 1

    calls = {"n": 0}

    async def good():
        calls["n"] += 1
        return GOOD_RAW

    monkeypatch.setattr(metal_price, "_request_source", good)

    result = await metal_price.fetch_prices()
    assert result["stale"] is True and result["refreshing"] is True
    assert result["gold"]["price"] == "900.00", "应先把旧数据给出去"
    assert calls["n"] == 0 or metal_price._REFRESH_TASK is not None

    # 后台任务跑完后，缓存应更新为新行情
    await asyncio.sleep(0)
    if metal_price._REFRESH_TASK is not None:
        await metal_price._REFRESH_TASK
    assert metal_price._CACHE["payload"]["gold"]["price"] == "912.50"
    assert calls["n"] == 1


async def test_force_bypasses_cooldown(monkeypatch):
    """用户点「刷新行情」要真的去打外部接口，不能被冷却挡住。"""
    metal_price._CACHE["failed_at"] = __import__("time").monotonic()  # 刚失败
    calls = {"n": 0}

    async def good():
        calls["n"] += 1
        return GOOD_RAW

    monkeypatch.setattr(metal_price, "_request_source", good)
    result = await metal_price.fetch_prices(force=True)

    assert calls["n"] == 1
    assert result["available"] is True
    assert result["gold"]["price"] == "912.50"
    assert metal_price._CACHE["failed_at"] == 0.0, "成功后应清掉失败标记"


async def test_good_payload_is_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    async def good():
        calls["n"] += 1
        return GOOD_RAW

    monkeypatch.setattr(metal_price, "_request_source", good)
    first = await metal_price.fetch_prices()
    second = await metal_price.fetch_prices()

    assert first["available"] is True and first["cached"] is False
    assert second["cached"] is True
    assert calls["n"] == 1
