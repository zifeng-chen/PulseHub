"""真实客户端 IP 解析（反代场景）。

**为什么要单独一层**：本项目的请求链路是「浏览器 → nginx（前端容器）→ 后端」，
后端看到的 `request.client.host` 永远是 nginx 容器的地址（如 172.27.0.x）。
直接用它做审计和限流会出两个问题：

- 审计日志里所有请求看起来来自同一台机器，失去溯源价值；
- 按它限流等于全家人共用一个计数器，一个人刷爆就全家被锁。

**为什么不能无脑信 `X-Forwarded-For`**：这个头是客户端可以随便写的。只有确认
「请求确实来自我们自己的代理」时才能采信，否则任何人加一行头就能伪装成别的地址、
绕过限流。所以这里的规则是：

1. 直接对端不在 `FAM_TRUSTED_PROXIES`（默认 Docker 内网 + 回环）→ 用它，忽略 XFF；
2. 直接对端是可信代理 → 取 XFF 的**最后一项**。nginx 用
   `$proxy_add_x_forwarded_for` 把真实对端追加在末尾，前面都是客户端自己填的，
   取前面的等于让攻击者自己报 IP。
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

# 默认只信任「本机 + Docker 默认桥接网段」。故意不含 192.168.0.0/16：
# 局域网内的机器可以直接连 18000 端口，如果把整个私网都当代理，
# 它们就能伪造 X-Forwarded-For 绕过限流。
DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1,172.16.0.0/12"


@lru_cache(maxsize=8)
def parse_trusted_proxies(raw: str | None) -> tuple:
    """把 `a,b,cidr` 解析成网络列表。非法项忽略（不能让写错一个就整个失效）。"""
    networks = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _to_ip(value: str):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def is_trusted_proxy(addr: str, networks: tuple) -> bool:
    ip = _to_ip(addr)
    if ip is None:
        return False
    return any(ip in net for net in networks)


def resolve_client_ip(
    client_host: str | None, forwarded_for: str | None, trusted: tuple
) -> str:
    """返回真实客户端 IP；拿不到就返回空串。"""
    direct = (client_host or "").strip()
    if not direct:
        return ""
    if not forwarded_for or not is_trusted_proxy(direct, trusted):
        return direct

    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    if not hops:
        return direct
    candidate = hops[-1]
    # 末尾一项必须是合法 IP，否则宁可退回直接对端，也不要写脏值进审计
    return candidate if _to_ip(candidate) is not None else direct


def mask_ip(ip: str) -> str:
    """审计用的「IP 摘要」：只保留网段，不落完整地址。"""
    host = (ip or "").strip()
    if not host:
        return "—"
    if host == "unknown":
        return host
    if ":" in host:  # IPv6 保留前三段
        return ":".join(host.split(":")[:3]) + "::"
    parts = host.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.x"
    return host
