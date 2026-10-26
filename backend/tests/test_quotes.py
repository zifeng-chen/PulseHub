"""行情服务：代码归一化与腾讯报文解析（不联网）。"""

from __future__ import annotations

from app.services.quotes import _parse_tencent_line, fund_code, tencent_code


def _line(code: str, name: str, price: str, prev: str, change: str, pct: str) -> str:
    """按腾讯 88 字段格式拼一行，用于验证解析逻辑。"""
    fields = [""] * 88
    fields[1] = name
    fields[2] = code[2:]
    fields[3] = price
    fields[4] = prev
    fields[30] = "20261024150001"
    fields[31] = change
    fields[32] = pct
    return f'v_{code}="' + "~".join(fields) + '";'


def test_tencent_code_mapping():
    assert tencent_code("600519") == "sh600519"
    assert tencent_code("510300") == "sh510300"
    assert tencent_code("000001") == "sz000001"
    assert tencent_code("159915") == "sz159915"
    assert tencent_code("hk00700") == "hk00700"
    assert tencent_code("usAAPL") == "usAAPL"
    # 非代码文本不硬凑，交给基金净值兜底或标为无行情
    assert tencent_code("银行理财-稳健1号") is None
    assert tencent_code("") is None


def test_parse_tencent_line_fields():
    quote = _parse_tencent_line("sh600519", _line("sh600519", "贵州茅台", "1335.00", "1318.49", "16.51", "1.25"))
    assert quote is not None
    assert quote["name"] == "贵州茅台"
    assert quote["price"] == "1335.0000"
    assert quote["prev_close"] == "1318.4900"
    assert quote["change"] == "16.5100"
    assert quote["change_pct"] == "1.25"
    assert quote["kind"] == "market"
    assert quote["as_of"].startswith("2026-10-24")


def test_parse_tencent_line_skips_suspended():
    # 停牌时现价为 0，不能当有效行情
    assert _parse_tencent_line("sz000001", _line("sz000001", "平安银行", "0.00", "11.30", "0", "0")) is None


def test_parse_tencent_line_computes_pct_when_missing():
    raw = _line("sz000001", "平安银行", "11.20", "11.30", "-0.10", "")
    quote = _parse_tencent_line("sz000001", raw)
    assert quote is not None
    assert quote["change_pct"] == "-0.88"


def test_fund_code_prefix():
    # 场外基金可以显式标前缀，避免 LOF 的场内价与净值混用
    assert fund_code("161725") == "161725"
    assert fund_code("of161725") == "161725"
    assert fund_code("fund000198") == "000198"
    assert fund_code("sh600519") is None
    assert tencent_code("of161725") is None
