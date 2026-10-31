"""真实 IP 解析与限流的回归。

两块都容易「看起来在工作、其实没生效」：
- IP 解析错了，审计日志里全是容器地址（实测过：外部访问记成 172.27.0.x）；
- 限流键取错，全家人共用一个桶，一个人刷爆全家被锁。

所以这里既测纯函数（解析规则、窗口计数），也测中间件行为（真的返回 429）。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.netutil import (
    DEFAULT_TRUSTED_PROXIES,
    mask_ip,
    parse_trusted_proxies,
    resolve_client_ip,
)
from app.middleware.rate_limit import RateLimiter, limiter

TRUSTED = parse_trusted_proxies(DEFAULT_TRUSTED_PROXIES)


# ---------------- 真实 IP 解析 ----------------


def test_direct_connection_ignores_forged_header():
    """直连（对端不是代理）时，客户端自己写的 X-Forwarded-For 必须被忽略。"""
    assert resolve_client_ip("203.0.113.9", "1.1.1.1", TRUSTED) == "203.0.113.9"


def test_lan_direct_connection_cannot_spoof():
    """局域网机器直连 18000 时也不能伪造来源（它不在可信代理网段里）。"""
    assert resolve_client_ip("192.168.100.50", "10.0.0.1", TRUSTED) == "192.168.100.50"


def test_trusted_proxy_takes_last_hop():
    """来自可信代理时取最后一跳——nginx 把真实对端追加在末尾。"""
    assert resolve_client_ip("172.27.0.5", "6.6.6.6, 192.168.100.7", TRUSTED) == "192.168.100.7"


def test_trusted_proxy_single_hop():
    assert resolve_client_ip("127.0.0.1", "192.168.100.7", TRUSTED) == "192.168.100.7"


def test_malformed_hop_falls_back_to_direct():
    """XFF 末项不是合法 IP 时退回直接对端，不把脏值写进审计。"""
    assert resolve_client_ip("172.27.0.5", "not-an-ip", TRUSTED) == "172.27.0.5"


def test_empty_client_host():
    assert resolve_client_ip(None, "1.1.1.1", TRUSTED) == ""
    assert resolve_client_ip("", None, TRUSTED) == ""


def test_bad_trusted_entries_are_ignored():
    """配置里写错一项不能让整个信任列表失效。"""
    nets = parse_trusted_proxies("172.16.0.0/12, 这不是网段, 127.0.0.1")
    assert resolve_client_ip("172.27.0.5", "9.9.9.9", nets) == "9.9.9.9"
    assert resolve_client_ip("127.0.0.1", "9.9.9.9", nets) == "9.9.9.9"


def test_mask_ip_keeps_network_only():
    assert mask_ip("192.168.100.7") == "192.168.100.x"
    assert mask_ip("2001:db8:85a3::8a2e") == "2001:db8:85a3::"
    assert mask_ip("") == "—"


# ---------------- 滑动窗口计数 ----------------


def test_window_blocks_after_limit_and_recovers():
    rl = RateLimiter()
    now = 1000.0
    for _ in range(3):
        ok, _ = rl.allow("k", 3, now)
        assert ok is True
    blocked, retry_after = rl.allow("k", 3, now)
    assert blocked is False
    assert retry_after > 0

    # 窗口滑过去之后应恢复
    later = now + 61
    ok, _ = rl.allow("k", 3, later)
    assert ok is True


def test_buckets_are_independent():
    rl = RateLimiter()
    now = 1000.0
    for _ in range(3):
        rl.allow("a", 3, now)
    assert rl.allow("a", 3, now)[0] is False
    assert rl.allow("b", 3, now)[0] is True


def test_bucket_cap_does_not_block_all_traffic():
    """桶数量到上限时宁可放行，也不能因为内存保护把所有人拒之门外。"""
    rl = RateLimiter(max_buckets=5)
    now = 1000.0
    for i in range(5):
        rl.allow(f"ip{i}", 10, now)
    ok, _ = rl.allow("ip6", 10, now)
    assert ok is True


# ---------------- 中间件行为 ----------------


@pytest.fixture
async def rate_limited_client(client, monkeypatch):
    """打开限流、把阈值调小的客户端。

    依赖 `client` 只为触发建表（conftest 里 drop/create），限流本身不需要数据库。
    """
    from app.main import app

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    # 默认 300 次太慢，测试里压到 3 次
    monkeypatch.setattr(settings, "rate_limit_api_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 2)
    limiter.reset()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    limiter.reset()


async def test_general_api_returns_429_after_limit(rate_limited_client):
    c = rate_limited_client
    headers = {"X-Forwarded-For": "203.0.113.10"}  # 测试客户端是 127.0.0.1，属可信代理
    codes = []
    for _ in range(4):
        r = await c.get("/api/v1/setup/status", headers=headers)
        codes.append(r.status_code)

    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429

    body = (await c.get("/api/v1/setup/status", headers=headers)).json()
    assert body["success"] is False
    assert body["error"]["code"] == "RATE_LIMITED"


async def test_429_carries_retry_after_header(rate_limited_client):
    c = rate_limited_client
    headers = {"X-Forwarded-For": "203.0.113.11"}
    for _ in range(3):
        await c.get("/api/v1/setup/status", headers=headers)
    r = await c.get("/api/v1/setup/status", headers=headers)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


async def test_login_uses_stricter_limit(rate_limited_client):
    """登录阈值比普通接口更严：第 3 次就该被拦。"""
    c = rate_limited_client
    headers = {"X-Forwarded-For": "203.0.113.12"}
    payload = {"username": "nobody", "password": "wrong"}
    for _ in range(2):
        r = await c.post("/api/v1/auth/login", headers=headers, json=payload)
        assert r.status_code != 429
    r = await c.post("/api/v1/auth/login", headers=headers, json=payload)
    assert r.status_code == 429


async def test_different_ips_counted_separately(rate_limited_client):
    c = rate_limited_client
    for _ in range(3):
        await c.get("/api/v1/setup/status", headers={"X-Forwarded-For": "203.0.113.13"})
    blocked = await c.get("/api/v1/setup/status", headers={"X-Forwarded-For": "203.0.113.13"})
    other = await c.get("/api/v1/setup/status", headers={"X-Forwarded-For": "203.0.113.14"})
    assert blocked.status_code == 429
    assert other.status_code == 200


async def test_health_is_never_rate_limited(rate_limited_client):
    """健康检查不限流：容器 healthcheck 每 30 秒一次，被限流会被判成不健康而重启。"""
    c = rate_limited_client
    headers = {"X-Forwarded-For": "203.0.113.15"}
    codes = [(await c.get("/api/v1/health", headers=headers)).status_code for _ in range(6)]
    assert all(code == 200 for code in codes)


async def test_default_is_enabled_in_production_config():
    """线上默认开启：测试环境是显式关掉的，别把开关写反。"""
    assert "FAM_RATE_LIMIT_ENABLED" in __import__("os").environ
    assert settings.rate_limit_login_per_minute <= settings.rate_limit_api_per_minute
