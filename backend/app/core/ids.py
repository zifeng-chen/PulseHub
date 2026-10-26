"""编号、请求 ID 与幂等键（23 §2、10 §6）。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any


def new_request_id() -> str:
    return uuid.uuid4().hex


def new_id() -> str:
    """业务主键：32 位十六进制，不含业务语义。"""
    return uuid.uuid4().hex


def short_id(identifier: str | None, size: int = 8) -> str:
    """给普通用户展示的短编号。"""
    if not identifier:
        return ""
    return identifier[:size].upper()


def new_idempotency_key() -> str:
    return uuid.uuid4().hex


def fingerprint(payload: Any) -> str:
    """对请求体做稳定指纹，用于幂等键复用冲突检测。"""
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def utc_iso(dt: datetime | None = None) -> str:
    value = dt or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def decimal_str(value: Any, places: int = 4) -> str:
    """金额/数量一律以字符串返回（23 §1）。用 Decimal 而非 float，避免二进制精度丢失。"""
    if value is None:
        return "0"
    try:
        from decimal import Decimal, ROUND_HALF_UP

        d = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return f"{d:.{places}f}"
    except Exception:  # noqa: BLE001
        return str(value)
