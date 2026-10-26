"""投资标的行情（只读）：股票 / 场内基金 / 场外基金净值。

设计取舍（2026-10 实测结论）：
- 家庭账本只需要「每天一次的最新收益率」，不做盘中实时。
  因此这里按需拉取 + 30 分钟进程内缓存，失败返回 `available=False` 并保留上次成功结果，
  绝不写业务表——写入估值由调用方（用户点确认）决定。
- 数据源：
  - 股票 / 场内 ETF、LOF、港股、美股 → 腾讯 `qt.gtimg.cn`（一次可批量，GBK）；
  - 场外基金 → 东方财富 `fund.eastmoney.com/pingzhongdata/{code}.js`（单位净值 + 当日涨幅，
    货币基金没有净值字段，改用万份收益）；
  - 银行理财：没有公开行情源，这里直接返回 `unsupported`，由页面提示手工估值。
- 腾讯返回的是 GBK 文本，字段用 `~` 分隔；A 股 / 港股 / 美股共用同一套下标（已实测）：
  1=名称 3=现价 4=昨收 31=涨跌额 32=涨跌幅(%)。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TENCENT_URL = "http://qt.gtimg.cn/q="
FUND_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
TENCENT_LABEL = "腾讯行情"
FUND_LABEL = "东方财富基金净值"
CACHE_TTL_SECONDS = 30 * 60
TIMEOUT_SECONDS = 8.0
MAX_BATCH = 60          # 腾讯单次最多 60 个代码，超了拆批
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 缓存：symbol -> {"quote": dict, "fetched_at": float}
_CACHE: dict[str, dict[str, Any]] = {}

_TENCENT_RE = re.compile(r"^(sh|sz|hk|us)[A-Za-z0-9.]+$")
_FUND_RE = re.compile(r"^\d{6}$")
_NET_WORTH_RE = re.compile(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", re.S)
_MCF_RE = re.compile(r"Data_millionCopiesIncome\s*=\s*(\[.*?\]);", re.S)
_FUND_NAME_RE = re.compile(r'fS_name\s*=\s*"([^"]+)"')


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def fund_code(symbol: str) -> str | None:
    """场外基金代码：支持 `of161725` / `fund161725` 前缀强制按净值取值。"""
    text = (symbol or "").strip()
    for prefix in ("of", "fund"):
        if text.lower().startswith(prefix):
            code = text[len(prefix):].strip()
            return code if _FUND_RE.match(code) else None
    return text if _FUND_RE.match(text) else None


def tencent_code(symbol: str) -> str | None:
    """把标的代码补成腾讯格式：6 位数字按首位判断沪/深，已带前缀的原样返回。"""
    text = (symbol or "").strip()
    if text.lower().startswith(("of", "fund")):
        # 明确标成场外基金的，不走场内行情
        return None
    if not text:
        return None
    if _TENCENT_RE.match(text):
        # 美股代码区分大小写（usAAPL），A/港股的 sh/sz/hk 统一小写
        return text if text[:2].lower() == "us" else text.lower()
    if _FUND_RE.match(text):
        # 5/6/9 开头是沪市（含 5xx ETF、6xx 股票、9xx B 股），其余按深市
        return ("sh" if text[0] in "569" else "sz") + text
    return None


def _format_stamp(raw: str) -> str:
    """腾讯的时间戳是 14 位 `YYYYMMDDHHMMSS`，转成 `YYYY-MM-DD HH:MM` 便于展示。"""
    text = (raw or "").strip()
    if len(text) >= 14 and text[:14].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return _today_iso()


def _parse_tencent_line(code: str, raw: str) -> dict[str, Any] | None:
    parts = raw.strip().strip('";').split("~")
    if len(parts) < 6:
        return None
    price = _dec(parts[3])
    prev_close = _dec(parts[4])
    if price is None or price <= 0 or prev_close is None or prev_close <= 0:
        # 停牌或代码不存在时现价为 0，不能当有效行情
        return None
    change = _dec(parts[31]) if len(parts) > 31 else None
    pct = _dec(parts[32]) if len(parts) > 32 else None
    if pct is None:
        pct = (price - prev_close) / prev_close * Decimal("100")
    if change is None:
        change = price - prev_close
    return {
        "price": f"{price:.4f}",
        "prev_close": f"{prev_close:.4f}",
        "change": f"{change:.4f}",
        "change_pct": f"{pct:.2f}",
        "name": parts[1] or None,
        "as_of": _format_stamp(parts[30] if len(parts) > 30 else ""),
        "source": "tencent",
        "source_label": TENCENT_LABEL,
        "kind": "market",
    }


def _fetch_tencent(codes: list[str]) -> dict[str, dict[str, Any]]:
    """腾讯批量行情：code（如 sh600519）→ quote。"""
    result: dict[str, dict[str, Any]] = {}
    if not codes:
        return result
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        for start in range(0, len(codes), MAX_BATCH):
            batch = codes[start:start + MAX_BATCH]
            try:
                resp = client.get(
                    TENCENT_URL + ",".join(batch),
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                text = resp.content.decode("gbk", errors="replace")
            except Exception as exc:  # noqa: BLE001
                logger.warning("腾讯行情请求失败：%s", exc)
                continue
            for line in text.split("\n"):
                if "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                code = key.strip().replace("v_", "")
                parsed = _parse_tencent_line(code, raw)
                if parsed:
                    result[code] = parsed
    return result


def _json_array(raw: str) -> list[Any]:
    import json

    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _fetch_fund(code: str) -> dict[str, Any] | None:
    """场外基金：优先单位净值，货币基金退化为万份收益（没有涨跌概念）。"""
    if not _FUND_RE.match(code):
        return None
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = client.get(
                FUND_URL.format(code=code),
                headers={"User-Agent": USER_AGENT, "Referer": "https://fund.eastmoney.com/"},
            )
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("基金净值请求失败 %s：%s", code, exc)
        return None

    name_match = _FUND_NAME_RE.search(text)
    name = name_match.group(1) if name_match else None

    nav_match = _NET_WORTH_RE.search(text)
    rows = _json_array(nav_match.group(1)) if nav_match else []
    if rows:
        last = rows[-1]
        price = _dec(last.get("y"))
        pct = _dec(last.get("equityReturn"))
        if price is not None and price > 0:
            return {
                "price": f"{price:.4f}",
                "prev_close": None,
                "change": None,
                "change_pct": f"{pct:.2f}" if pct is not None else None,
                "name": name,
                "as_of": datetime.fromtimestamp(
                    (last.get("x") or 0) / 1000, tz=timezone.utc
                ).astimezone().date().isoformat() if last.get("x") else _today_iso(),
                "source": "eastmoney_fund",
                "source_label": FUND_LABEL,
                "kind": "fund",
            }

    # 货币基金：没有单位净值，用万份收益表示收益水平
    mcf_match = _MCF_RE.search(text)
    mcf_rows = _json_array(mcf_match.group(1)) if mcf_match else []
    if mcf_rows:
        last = mcf_rows[-1]
        income = _dec(last[1]) if isinstance(last, list) and len(last) > 1 else None
        if income is not None:
            return {
                "price": None,
                "prev_close": None,
                "change": None,
                "change_pct": None,
                "income_per_10k": f"{income:.4f}",
                "name": name,
                "as_of": datetime.fromtimestamp(
                    (last[0] or 0) / 1000, tz=timezone.utc
                ).astimezone().date().isoformat() if last[0] else _today_iso(),
                "source": "eastmoney_fund",
                "source_label": FUND_LABEL,
                "kind": "money_fund",
            }
    return None


def _cache_key(symbol: str, prefer_fund: bool) -> str:
    """同一代码在「场内 / 场外」两种口径下价格不同，缓存必须分开。"""
    return f"{'fund' if prefer_fund else 'market'}:{symbol}"


def _cached(key: str) -> dict[str, Any] | None:
    item = _CACHE.get(key)
    if item and time.time() - item["fetched_at"] < CACHE_TTL_SECONDS:
        return item["quote"]
    return None


def fetch_quotes(
    symbols: list[str], prefer_fund: bool = False,
) -> dict[str, dict[str, Any]]:
    """批量取行情：返回 symbol → quote。

    quote 字段：price / prev_close / change / change_pct / name / as_of / source / source_label / kind，
    取不到时返回 {"available": False, "reason": ...}，调用方据此提示而不是报错。
    """
    unique = [s.strip() for s in symbols if s and s.strip()]
    result: dict[str, dict[str, Any]] = {}
    pending: list[str] = []

    for symbol in dict.fromkeys(unique):
        cached = _cached(_cache_key(symbol, prefer_fund))
        if cached is not None:
            result[symbol] = cached
        else:
            pending.append(symbol)

    # 基金账户里的 6 位代码先按场外净值取：LOF 的场内价与净值不同，不能混用
    if prefer_fund:
        for symbol in pending:
            code = fund_code(symbol)
            if not code:
                continue
            fund = _fetch_fund(code)
            if fund:
                result[symbol] = fund

    # 1) 能映射成腾讯代码的先批量取（股票 / 场内基金 / 港美股）
    tencent_map = {s: tencent_code(s) for s in pending}
    tencent_codes = [c for c in tencent_map.values() if c]
    tencent_quotes = _fetch_tencent(list(dict.fromkeys(tencent_codes)))
    for symbol, code in tencent_map.items():
        if not code or symbol in result:
            # 基金账户已经按净值取到了，不拿场内价覆盖
            continue
        quote = tencent_quotes.get(code)
        if quote:
            result[symbol] = quote

    # 2) 腾讯取不到的 6 位代码，按场外基金净值再试一次
    for symbol in pending:
        if symbol in result:
            continue
        code = fund_code(symbol)
        if not code:
            continue
        fund = _fetch_fund(code)
        if fund:
            result[symbol] = fund

    now = time.time()
    for symbol in pending:
        quote = result.get(symbol) or {
            "available": False,
            "reason": "没有匹配到行情源（银行理财、私募等无公开行情，请手工估值）",
        }
        result[symbol] = quote
        _CACHE[_cache_key(symbol, prefer_fund)] = {"quote": quote, "fetched_at": now}

    # 缓存里的老结果也带上 available 标记
    for symbol in list(result):
        result[symbol].setdefault("available", True)
    return result
