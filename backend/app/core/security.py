"""密码与令牌安全（04 §7、10 §7、23 §9）。

- 密码使用强哈希（bcrypt），不可逆保存。
- 访问令牌短时有效；刷新令牌可撤销（会话记录）。
- 密码强度实时评估，含长度、复杂度与常见密码提醒。
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

_COMMON_PASSWORDS = {
    "123456", "12345678", "password", "qwerty", "111111", "123456789",
    "abc123", "admin", "admin123", "letmein", "welcome", "iloveyou",
    "000000", "666666", "888888", "a123456", "123123", "1qaz2wsx",
}


# ---------------- 密码 ----------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def assess_password_strength(password: str) -> dict[str, Any]:
    """返回强度评估，供前端实时显示。"""
    length = len(password)
    has_lower = bool(re.search(r"[a-z]", password))
    has_upper = bool(re.search(r"[A-Z]", password))
    has_digit = bool(re.search(r"\d", password))
    has_symbol = bool(re.search(r"[^A-Za-z0-9]", password))
    variety = sum([has_lower, has_upper, has_digit, has_symbol])

    score = 0
    if length >= 8:
        score += 1
    if length >= 12:
        score += 1
    if length >= 16:
        score += 1
    score += max(0, variety - 1)

    is_common = password.lower() in _COMMON_PASSWORDS
    has_sequence = bool(re.search(r"(012|123|234|345|456|567|678|789|abc|qwe)", password.lower()))

    if is_common:
        score = 0
    elif has_sequence:
        score = max(0, score - 1)

    score = min(score, 4)
    labels = ["很弱", "较弱", "一般", "较强", "很强"]
    warnings: list[str] = []
    if length < 8:
        warnings.append("长度建议不少于 8 位")
    if variety < 3:
        warnings.append("建议同时包含大小写字母、数字和符号")
    if is_common:
        warnings.append("这是常见密码，容易被猜到")
    if has_sequence:
        warnings.append("避免使用连续或顺序字符")

    return {
        "score": score,
        "label": labels[score],
        "length": length,
        "checks": {
            "length": length >= 8,
            "lower": has_lower,
            "upper": has_upper,
            "digit": has_digit,
            "symbol": has_symbol,
        },
        "warnings": warnings,
        "acceptable": length >= 8 and not is_common and score >= 2,
    }


# ---------------- 令牌 ----------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, session_id: str, household_id: str | None,
                        role: str, extra: dict | None = None) -> str:
    payload = {
        "sub": user_id,
        "sid": session_id,
        "hh": household_id,
        "role": role,
        "typ": "access",
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=settings.access_token_minutes)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, session_id: str, days: int | None = None) -> tuple[str, datetime]:
    """生成刷新令牌。

    days 为空时用全局默认（30 天）。「记住此设备」勾选走默认，
    未勾选时传较短天数，避免在公用设备上长期保留登录态。
    """
    expires_at = _now() + timedelta(days=days if days is not None else settings.refresh_token_days)
    payload = {
        "sub": user_id,
        "sid": session_id,
        "typ": "refresh",
        "iat": int(_now().timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def refresh_expiry() -> datetime:
    return _now() + timedelta(days=settings.refresh_token_days)


def new_session_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return _now()


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


# ---------------- 出站 URL 安全（SSRF 防护） ----------------

_PRIVATE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}
_PRIVATE_SUFFIXES = (".local", ".internal", ".lan", ".home", ".localhost")
# 私网/保留网段（RFC 1918 + 链路本地 + 环回 + CGNAT + 组播/保留）
_PRIVATE_NETS = (
    "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
    "172.29.", "172.30.", "172.31.", "192.168.", "169.254.", "100.64.",
)


def is_private_host(host: str) -> bool:
    """判断主机名/IP 是否指向私网或本机，用于拦截 SSRF（M-4）。"""
    host = (host or "").strip().lower()
    if not host:
        return True
    # 去掉端口与 IPv6 方括号
    if host.startswith("["):
        host = host.split("]", 1)[0].lstrip("[")
    elif ":" in host and not host.startswith("["):
        # IPv6 无方括号（少见），保守判私网
        return True
    if ":" in host:
        host = host.split(":", 1)[0]
    if host in _PRIVATE_HOSTS:
        return True
    if host.endswith(_PRIVATE_SUFFIXES):
        return True
    # 纯 IPv4
    if host.replace(".", "").isdigit():
        return host.startswith(_PRIVATE_NETS) or host.startswith("0.") or host.startswith("127.")
    return False


def validate_outbound_url(url: str) -> None:
    """校验出站 URL 必须为 http(s) 且目标不是私网/本机，否则拒绝（SSRF）。"""
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅支持 http(s) 协议")
    if not parsed.hostname:
        raise ValueError("缺少有效主机名")
    if is_private_host(parsed.hostname):
        raise ValueError("不允许指向内网或本机地址")
