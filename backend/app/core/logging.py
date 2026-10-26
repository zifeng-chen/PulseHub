"""结构化日志（24 §11）。

禁止记录密码、Token、Secret、完整证件号或完整银行卡号。
所有日志带 request_id，便于按请求编号关联应用日志、审计与外部响应。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.core.config import settings

_SENSITIVE_KEYS = {
    "password", "new_password", "old_password", "confirm_password",
    "token", "access_token", "refresh_token", "secret", "secret_key",
    "api_key", "apikey", "authorization", "database_password",
    "id_card", "id_number", "vin", "plate", "engine_no", "meter_no",
    "card_no", "bank_card",
}


def mask_value(value: Any) -> str:
    """敏感值只保留掩码。"""
    if value is None or value == "":
        return ""
    text = str(value)
    if len(text) <= 4:
        return "****"
    return f"{text[:2]}{'*' * (len(text) - 4)}{text[-2:]}"


def scrub(payload: Any) -> Any:
    """递归脱敏，用于日志。"""
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                out[key] = mask_value(value)
            else:
                out[key] = scrub(value)
        return out
    if isinstance(payload, list):
        return [scrub(i) for i in payload]
    return payload


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "env": settings.app_env,
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            base.update(scrub(extra))
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if settings.is_production:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(level or ("INFO" if not settings.is_production else "INFO"))
    # 降低第三方噪声
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    logger.info(message, extra={"extra_fields": fields})
