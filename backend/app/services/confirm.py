"""高影响动作二次确认令牌（18 §2.4、20 §5、11 §5）。

删除、冲销、停用、解除关联、修改基础币种、启用云端外发等高影响动作，
必须携带一次性确认令牌；后端再次校验对象、版本、权限与幂等键。
令牌使用签名 JWT（含一次性 jti），已使用令牌进入短期黑名单，杜绝重复使用。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt

from app.core.config import settings
from app.core.context import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id

_TTL_SECONDS = 300  # 确认令牌 5 分钟内有效
_lock = threading.Lock()
_used_jti: dict[str, float] = {}  # jti -> 过期时间戳


def _purge() -> None:
    now = time.time()
    for jti, exp in list(_used_jti.items()):
        if exp < now:
            _used_jti.pop(jti, None)


def issue_token(user: CurrentUser, action: str, object_type: str, object_id: str | None,
                extra: dict[str, Any] | None = None) -> dict:
    jti = new_id()
    now = int(time.time())
    payload = {
        "typ": "confirm",
        "jti": jti,
        "sub": user.user_id,
        "hh": user.household_id,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "iat": now,
        "exp": now + _TTL_SECONDS,
    }
    if extra:
        payload["extra"] = extra
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return {"token": token, "expires_in": _TTL_SECONDS, "action": action,
            "object_type": object_type, "object_id": object_id}


def consume_token(token: str | None, user: CurrentUser, action: str,
                  object_type: str, object_id: str | None = None) -> dict:
    if not token:
        raise AppError(ErrorCode.VALIDATION_ERROR, "该操作需要二次确认",
                       field_errors={"confirm_token": "请通过二次确认后重试"})
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise AppError(ErrorCode.VALIDATION_ERROR, "确认已过期，请重新确认",
                       field_errors={"confirm_token": "确认已过期"})
    except jwt.InvalidTokenError:
        raise AppError(ErrorCode.VALIDATION_ERROR, "确认令牌无效",
                       field_errors={"confirm_token": "确认令牌无效"})

    if payload.get("typ") != "confirm":
        raise AppError(ErrorCode.VALIDATION_ERROR, "确认令牌类型不正确")
    if payload.get("sub") != user.user_id:
        raise AppError(ErrorCode.FORBIDDEN, "确认令牌与当前用户不匹配", status_code=403)
    if payload.get("action") != action or payload.get("object_type") != object_type:
        raise AppError(ErrorCode.VALIDATION_ERROR, "确认令牌与当前操作不匹配")
    if object_id and payload.get("object_id") and payload["object_id"] != object_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "确认令牌与目标对象不匹配")

    jti = payload.get("jti")
    with _lock:
        _purge()
        if jti in _used_jti:
            raise AppError(ErrorCode.VALIDATION_ERROR, "该确认已被使用，请重新确认")
        _used_jti[jti] = payload.get("exp", time.time() + _TTL_SECONDS)
    return payload
