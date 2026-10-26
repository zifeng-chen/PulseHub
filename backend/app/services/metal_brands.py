"""贵金属品牌与购买渠道字典（国内 + 中国香港/中国澳门常见品牌）。

前端「品牌」下拉与「购买渠道」联动都用这份数据；品牌可手填，
因此这里只做「常用选项」，不作为校验白名单。
"""

from __future__ import annotations

# 品牌 -> 可选购买渠道
# 渠道与品牌强相关：金店走门店/官方旗舰店；银行走柜面/手机银行；交易所/金币走特许渠道。
_GOLD_SHOP_CHANNELS = ["线下门店", "官方旗舰店（天猫/京东）", "品牌官网", "中国香港/中国澳门门店", "代购/其他"]
_BANK_CHANNELS = ["银行柜面", "手机银行", "积存金账户", "银行官方商城"]
_MINT_CHANNELS = ["中国金币特许零售商", "银行代销", "官方商城"]

BRANDS: list[dict] = [
    # ---- 内地金店 ----
    {"code": "chowtaifook", "name": "周大福", "region": "内地/中国香港/中国澳门", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "chowsangsang", "name": "周生生", "region": "内地/中国香港/中国澳门", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "lukfook", "name": "六福珠宝", "region": "内地/中国香港/中国澳门", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "tsl", "name": "谢瑞麟 TSL", "region": "内地/中国香港/中国澳门", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "kingfook", "name": "景福珠宝", "region": "中国香港", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "chowtaifook_hk", "name": "周大福（中国香港）", "region": "中国香港", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "laofengxiang", "name": "老凤祥", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "laomiao", "name": "老庙黄金", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "chinagold", "name": "中国黄金", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "caibai", "name": "菜百首饰", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "chowtaiseng", "name": "周大生", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "chj", "name": "潮宏基", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "mingpai", "name": "明牌珠宝", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "yayi", "name": "亚一金店", "region": "内地", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "kinghood", "name": "金至尊", "region": "中国香港", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "emperor", "name": "英皇珠宝", "region": "中国香港", "channels": _GOLD_SHOP_CHANNELS},
    {"code": "ctf_soinlove", "name": "周大福·投资金条", "region": "内地/中国香港", "channels": _GOLD_SHOP_CHANNELS},
    # ---- 银行投资金条 ----
    {"code": "icbc_bar", "name": "工商银行（如意金）", "region": "内地", "channels": _BANK_CHANNELS},
    {"code": "ccb_bar", "name": "建设银行（龙鼎金）", "region": "内地", "channels": _BANK_CHANNELS},
    {"code": "boc_bar", "name": "中国银行（中银吉祥金）", "region": "内地", "channels": _BANK_CHANNELS},
    {"code": "abc_bar", "name": "农业银行（传世金）", "region": "内地", "channels": _BANK_CHANNELS},
    {"code": "cmb_bar", "name": "招商银行（招行金）", "region": "内地", "channels": _BANK_CHANNELS},
    {"code": "bocom_bar", "name": "交通银行", "region": "内地", "channels": _BANK_CHANNELS},
    {"code": "psbc_bar", "name": "邮储银行", "region": "内地", "channels": _BANK_CHANNELS},
    # ---- 官方铸造/金币 ----
    {"code": "chngc", "name": "中国金币（熊猫金币）", "region": "内地", "channels": _MINT_CHANNELS},
    {"code": "sge", "name": "上海黄金交易所", "region": "内地", "channels": ["会员单位", "银行代理"]},
    # ---- 其他 ----
    {"code": "other", "name": "其他品牌 / 自行铸造", "region": "—", "channels": ["线下门店", "线上店铺", "典当/回购", "其他"]},
]

# 计价方式：克重计价（按克价+加工费）或一口价（标价销售）
PRICING_MODES = [
    {"value": "by_weight", "label": "克重计价（克价 × 克重）"},
    {"value": "flat", "label": "一口价（按件标价）"},
]

# 纯度常用档位（按国标命名，前端下拉）
PURITIES = [
    {"value": "999.9", "label": "足金 999.9（Au9999）"},
    {"value": "999", "label": "足金 999（Au999）"},
    {"value": "916", "label": "22K / 916"},
    {"value": "750", "label": "18K / Au750"},
    {"value": "585", "label": "14K / 585"},
    {"value": "999.5", "label": "铂金 Pt999.5"},
    {"value": "950", "label": "铂金 Pt950"},
    {"value": "925", "label": "银 925"},
    {"value": "999.0", "label": "银 999"},
]

# 只有「投资/理财金条」类才需要证书编号（首饰、金币、摆件通常无证书）
_CERT_CATEGORY_KEYWORDS = ("金条", "投资金", "理财金", "收藏金")


def is_certificate_category(category_name: str | None) -> bool:
    """分类名是否属于「投资理财金条」类（决定是否显示证书编号）。"""
    if not category_name:
        return False
    return any(keyword in category_name for keyword in _CERT_CATEGORY_KEYWORDS)


def brand_options() -> list[dict]:
    return [{"value": b["code"], "label": f"{b['name']}（{b['region']}）"} for b in BRANDS]


def channels_for(brand_code: str | None) -> list[str]:
    if not brand_code:
        return ["线下门店", "官方旗舰店（天猫/京东）", "银行柜面", "其他"]
    for brand in BRANDS:
        if brand["code"] == brand_code:
            return list(brand["channels"])
    return ["线下门店", "官方旗舰店（天猫/京东）", "其他"]
