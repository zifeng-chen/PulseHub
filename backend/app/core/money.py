"""金额格式化统一出口（03 §3：金额以字符串返回）。

全平台金额默认保留两位小数；家庭可在「设置 → 财务口径 → 金额精度」中调整
（0–6 位），此处通过 contextvar 在请求级生效，保证后端所有接口输出一致。

注意：**只有金额**走本模块。面积、重量、纯度、产权比例、利率、汇率等
非金额数值保持各自精度，不走这里。
"""

from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_AMOUNT_PRECISION = 2
MAX_AMOUNT_PRECISION = 4  # 与底层 DECIMAL(20,4) 及设置页范围一致

_amount_precision: ContextVar[int] = ContextVar(
    "amount_precision", default=DEFAULT_AMOUNT_PRECISION
)


def normalize_precision(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_AMOUNT_PRECISION
    return max(0, min(MAX_AMOUNT_PRECISION, n))


def set_amount_precision(value) -> None:
    """设置当前请求上下文的金额精度（由鉴权依赖按家庭配置注入）。"""
    _amount_precision.set(normalize_precision(value))


def get_amount_precision() -> int:
    return _amount_precision.get()


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def fmt_amount(value, *, precision: int | None = None, signed: bool = False) -> str:
    """把金额格式化为字符串（四舍五入，默认两位小数）。

    - precision 显式给定时使用该精度，否则用当前请求上下文的家庭配置。
    - signed=True 时正数前置「+」（用于损益、差额等）。
    """
    p = get_amount_precision() if precision is None else normalize_precision(precision)
    d = _to_decimal(value).quantize(Decimal(1).scaleb(-p), rounding=ROUND_HALF_UP)
    text = f"{d:.{p}f}"
    if signed and d > 0:
        text = "+" + text
    return text
