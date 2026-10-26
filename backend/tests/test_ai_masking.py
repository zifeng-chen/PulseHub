"""第三方模型外发前的敏感信息脱敏。

要求：第三方模型读取用户数据时必须过滤敏感标识，但不影响财务分析所需的金额与分类。
"""

from __future__ import annotations

from app.services.ai import mask_sensitive


def test_mask_phone_id_card_bank_card():
    assert "138****8888" in mask_sensitive("我的手机号是 13812348888")
    # 身份证 18 位：保留前 6 与后 4
    assert "110101********1234" in mask_sensitive("身份证 110101199001011234")
    # 银行卡 19 位：保留前 4 与后 4
    masked = mask_sensitive("卡号 6222021234567890123")
    assert "6222 **** **** 0123" in masked
    assert "6222021234567890123" not in masked


def test_mask_plate_vin_email():
    assert "[车牌已脱敏]" in mask_sensitive("车牌 京A12345")
    assert "[VIN 已脱敏]" in mask_sensitive("VIN LSVAM4187E2123456")
    assert "[邮箱已脱敏]" in mask_sensitive("联系 admin@example.com")


def test_amounts_and_names_are_kept():
    """金额、分类、机构必须保留，否则第三方模型无法做财务分析。"""
    text = "招商信用卡已用 12,345.67 元，本月餐饮支出 888.50 元"
    masked = mask_sensitive(text)
    assert "12,345.67" in masked
    assert "888.50" in masked
    assert "招商信用卡" in masked


def test_plain_text_unchanged():
    text = "本月净资产下降，主要因为房贷还款。"
    assert mask_sensitive(text) == text
