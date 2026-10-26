"""房屋租赁合同模板（生成 .docx）。

为什么自己拼 OOXML 而不装 python-docx：
- 运行环境只保留最小依赖，多一个包就多一次镜像重建与升级风险；
- 合同模板是**固定版式 + 少量变量**，.docx 本质是 zip + 几个 XML，拼出来更可控。

产出可直接用 Word / WPS 打开、修改、打印，也可以另存为 PDF 签署。
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from xml.sax.saxutils import escape

BLANK = "＿＿＿＿＿＿＿＿＿＿"

_W_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# 正文默认宋体小四（w:sz 21 = 10.5pt），行距 1.5
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles """ + _W_NS + """>
<w:docDefaults>
<w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>
<w:sz w:val="21"/><w:szCs w:val="21"/>
</w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal">
<w:name w:val="Normal"/><w:qFormat/>
</w:style>
</w:styles>"""


def _run(text: str, *, bold: bool = False, size: int | None = None, font: str = "宋体") -> str:
    props = [
        f'<w:rFonts w:ascii="Times New Roman" w:eastAsia="{font}" w:hAnsi="Times New Roman"/>',
    ]
    if bold:
        props.append("<w:b/>")
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    return (
        "<w:r><w:rPr>" + "".join(props) + "</w:rPr>"
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    )


def _para(
    text: str,
    *,
    bold: bool = False,
    size: int | None = None,
    align: str | None = None,
    indent: bool = False,
    space_before: int = 0,
    space_after: int = 0,
    font: str = "宋体",
    line: int = 360,
) -> str:
    ppr = [f'<w:spacing w:before="{space_before}" w:after="{space_after}" w:line="{line}" w:lineRule="auto"/>']
    if align:
        ppr.append(f'<w:jc w:val="{align}"/>')
    if indent:
        # 首行缩进 2 字符（用字符数，字体换行也稳定）
        ppr.append('<w:ind w:firstLineChars="200" w:firstLine="420"/>')
    return "<w:p><w:pPr>" + "".join(ppr) + "</w:pPr>" + _run(text, bold=bold, size=size, font=font) + "</w:p>"


def _blank_para() -> str:
    return '<w:p><w:pPr><w:spacing w:line="200" w:lineRule="auto"/></w:pPr></w:p>'


def _build_docx(body_xml: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_W_NS}><w:body>{body_xml}"
        # A4 + 常规页边距（上下 2.54cm、左右 3.17cm，单位 twip）
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" '
        'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        zf.writestr("word/styles.xml", _STYLES)
        zf.writestr("word/document.xml", document)
    return buffer.getvalue()


def _fmt_date(value) -> str:
    if not value:
        return BLANK
    return f"{value.year} 年 {value.month} 月 {value.day} 日"


def _fmt_money(value) -> str:
    if value in (None, ""):
        return BLANK
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


CURRENCY_LABELS = {"CNY": "元", "USD": "美元", "HKD": "港元", "EUR": "欧元", "JPY": "日元"}


def _currency_label(code) -> str:
    return CURRENCY_LABELS.get((code or "CNY").upper(), str(code or "CNY"))


def _month_span(start, end) -> str:
    """租期月数：按自然月估算（不足一月按一月），让合同上的「共计 X 个月」不至于空着。"""
    if not start or not end:
        return BLANK
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day >= start.day:
        months += 1
    return str(max(1, months))


def _or(value, suffix: str = "") -> str:
    text = ("" if value is None else str(value)).strip()
    return f"{text}{suffix}" if text else BLANK


PERIOD_LABELS = {
    "monthly": "月付",
    "quarterly": "季付",
    "half_yearly": "半年付",
    "yearly": "年付",
    "weekly": "周付",
    "one_time": "一次性付清",
}

REPAIR_BEARER = {
    "landlord": "由出租方（甲方）承担",
    "tenant": "由承租方（乙方）承担",
    "both": "由双方按约定分担",
}


def extract_paragraphs(content: bytes) -> list[dict]:
    """把生成的 .docx 抽成纯文本段落，供网页预览。

    不引 docx 解析库：合同是我们自己拼的 OOXML，只要按 <w:p> / <w:t> 取值即可，
    这样预览与下载用的是**同一份内容**，不会出现「预览一套、下载另一套」。
    """
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    result: list[dict] = []
    for block in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", block, re.S)
        line = html.unescape("".join(texts)).strip()
        if not line:
            result.append({"text": "", "heading": False})
            continue
        heading = bool(re.match(r"^(房屋租赁合同|第[一二三四五六七八九十]+条)", line))
        result.append({"text": line, "heading": heading})
    return result


def build_lease_contract(*, lease=None, asset=None, extension=None, household_name: str = "") -> bytes:
    """生成房屋租赁合同。

    lease / asset 为空时输出空白模板（供先打印再手写）；有租约时自动填入已知信息，
    未知项留取下划线，由双方签署时补充。
    """
    ext = extension or {}
    address = (
        ext.get("address")
        or (asset.name if asset is not None else None)
        or BLANK
    )
    area = ext.get("building_area")
    area_text = f"{float(area):.2f} 平方米" if area else BLANK
    title_no = ext.get("title_no") or ext.get("ownership_no") or BLANK
    tenant = getattr(lease, "tenant_name", None)
    tenant_contact = getattr(lease, "tenant_contact", None)
    rent = getattr(lease, "monthly_rent", None)
    deposit = getattr(lease, "deposit_amount", None)
    period = PERIOD_LABELS.get(getattr(lease, "payment_frequency", None) or "", "月付")
    start = getattr(lease, "start_date", None)
    end = getattr(lease, "end_date", None)
    purpose = getattr(lease, "lease_purpose", None) or "居住"
    bearer = REPAIR_BEARER.get(
        getattr(lease, "property_repair_bearer", None) or "", "由出租方（甲方）承担"
    )
    lease_no = getattr(lease, "lease_no", None)
    currency = _currency_label(getattr(lease, "currency", None))
    months = _month_span(start, end)

    parts: list[str] = []
    parts.append(_para("房屋租赁合同", bold=True, size=36, align="center", space_after=120, font="黑体"))
    if lease_no:
        parts.append(_para(f"合同编号：{lease_no}", align="center", size=18, space_after=200))
    else:
        parts.append(_para(f"合同编号：{BLANK}", align="center", size=18, space_after=200))

    parts.append(_para(
        "根据《中华人民共和国民法典》及有关法律法规，甲、乙双方在平等、自愿、协商一致的基础上，"
        "就房屋租赁事宜达成如下协议，共同遵守。",
        indent=True, space_after=120,
    ))

    parts.append(_para("出租方（甲方）", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(f"姓名／名称：{BLANK}　　身份证号／统一社会信用代码：{BLANK}", indent=True))
    parts.append(_para(f"联系电话：{BLANK}　　通讯地址：{BLANK}", indent=True, space_after=80))

    parts.append(_para("承租方（乙方）", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(f"姓名／名称：{_or(tenant)}　　身份证号／统一社会信用代码：{BLANK}", indent=True))
    parts.append(_para(
        f"联系电话：{_or(tenant_contact)}　　通讯地址：{BLANK}", indent=True,
    ))
    parts.append(_para(
        f"紧急联系人及电话：{BLANK}（用于无法联系乙方时的应急联络）", indent=True, space_after=120,
    ))

    parts.append(_para("第一条　房屋基本情况", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(f"1.1　房屋坐落：{address}", indent=True))
    parts.append(_para(f"1.2　不动产权证号／房产证号：{title_no}", indent=True))
    parts.append(_para(f"1.3　建筑面积：{area_text}；房屋用途：{purpose}。", indent=True))
    parts.append(_para(
        "1.4　房屋权属状况：甲方保证对上述房屋享有合法出租权，房屋不存在查封、权属争议或其他"
        "影响乙方正常使用的权利负担；如因权属问题导致合同无法履行，甲方应退还已收租金及押金，"
        "并赔偿乙方因此产生的实际损失。",
        indent=True,
    ))
    parts.append(_para(
        "1.5　房屋内附属设施、家具家电及钥匙数量见《附件一：房屋及附属设施交接清单》，"
        "该清单经双方签字确认后与本合同具有同等效力。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第二条　租赁期限", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        f"2.1　租赁期自 {_fmt_date(start)} 起至 {_fmt_date(end)} 止，共计 {months} 个月。",
        indent=True,
    ))
    parts.append(_para(
        "2.2　租赁期满，乙方需继续承租的，应于期满前 30 日书面通知甲方，同等条件下乙方享有优先"
        "承租权；双方另行签订续租合同。乙方未续租的，应于期满之日腾退房屋。",
        indent=True,
    ))
    parts.append(_para(
        "2.3　甲方应于起租日前将房屋交付乙方使用，交付时双方共同验收并在附件一上签字。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第三条　租金及支付方式", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        f"3.1　月租金为 {_fmt_money(rent)} {currency}（大写：{BLANK}），租金标准在租赁期内"
        "保持不变，双方另有书面约定的除外。",
        indent=True,
    ))
    parts.append(_para(
        f"3.2　支付方式：{period}，乙方应于每期开始前 {BLANK} 日内支付下一期租金。"
        "支付至甲方指定的下列账户：",
        indent=True,
    ))
    parts.append(_para(
        f"户名：{BLANK}　　开户行：{BLANK}　　账号：{BLANK}", indent=True,
    ))
    parts.append(_para(
        "3.3　乙方逾期支付租金的，每逾期一日应按当期应付租金的 0.5% 向甲方支付违约金；"
        "逾期超过 15 日的，甲方有权解除本合同。",
        indent=True,
    ))
    parts.append(_para(
        "3.4　租赁期内发生的水费、电费、燃气费、供暖费、物业费、网络及有线电视费等，"
        "按本合同第五条约定承担。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第四条　押金", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        f"4.1　乙方应于签订本合同时向甲方支付押金 {_fmt_money(deposit)} {currency}，"
        "甲方应出具收据。",
        indent=True,
    ))
    parts.append(_para(
        "4.2　押金用于担保乙方履行本合同义务。租赁期满或合同解除，乙方结清全部费用、"
        "腾退房屋并完成交接后，甲方应于 7 日内无息退还押金；因乙方原因造成房屋或设施损坏、"
        "欠缴费用的，甲方可从押金中扣除相应金额，并向乙方提供费用明细。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第五条　费用承担", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        "5.1　租赁期内，房屋的水费、电费、燃气费、网络费、有线电视费由乙方承担，按实际使用量"
        "及相关部门账单结算。",
        indent=True,
    ))
    parts.append(_para(
        "5.2　物业费、供暖费、房屋租赁税费及房屋本体维修基金由甲方承担；"
        "双方另有约定的，在本条 5.4 中载明。",
        indent=True,
    ))
    parts.append(_para(
        f"5.3　房屋及附属设施的自然损耗维修{('，' + bearer) if bearer else ''}；"
        "因乙方使用不当造成的损坏，由乙方负责修复或赔偿。",
        indent=True,
    ))
    parts.append(_para("5.4　其他约定：", indent=True, space_after=120))
    parts.append(_para(f"{BLANK}。", indent=True, space_after=120))

    parts.append(_para("第六条　房屋使用与维护", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        "6.1　乙方应按照约定的用途使用房屋，不得擅自改变房屋结构或用途；确需装修、"
        "增设附属设施的，应事先取得甲方书面同意，并遵守物业及消防管理规定。",
        indent=True,
    ))
    parts.append(_para(
        "6.2　乙方应合理使用房屋及设施，做好日常防火、防盗、用水用电安全；"
        "因乙方原因造成火灾、水浸等事故的，由乙方承担赔偿责任。",
        indent=True,
    ))
    parts.append(_para(
        "6.3　乙方不得在房屋内从事违法犯罪活动，不得存放易燃易爆、剧毒等危险物品；"
        "不得影响相邻方正常生活。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第七条　转租与转让", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        "7.1　未经甲方书面同意，乙方不得将房屋全部或部分转租、转借给第三人，"
        "不得以房屋使用权抵偿债务。",
        indent=True,
    ))
    parts.append(_para(
        "7.2　租赁期内甲方出售房屋的，应提前 30 日书面通知乙方，乙方在同等条件下享有优先"
        "购买权；房屋所有权变动不影响本合同效力（买卖不破租赁）。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第八条　合同解除与终止", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        "8.1　经双方协商一致，可以解除本合同。",
        indent=True,
    ))
    parts.append(_para(
        "8.2　有下列情形之一的，甲方有权解除合同并要求乙方腾退房屋：乙方逾期支付租金超过 15 日；"
        "乙方擅自转租或改变房屋用途；乙方利用房屋从事违法活动；乙方严重损坏房屋且拒不修复。",
        indent=True,
    ))
    parts.append(_para(
        "8.3　有下列情形之一的，乙方有权解除合同：甲方未按约定交付房屋或交付的房屋不符合约定，"
        "经催告后 15 日内仍未改正；房屋存在危及乙方安全或健康的缺陷；甲方擅自提高租金或"
        "干扰乙方正常使用。",
        indent=True,
    ))
    parts.append(_para(
        "8.4　提前解除合同的一方，应提前 30 日书面通知对方；因提前解除给对方造成损失的，"
        "除本合同另有约定外，应赔偿对方实际损失。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第九条　违约责任", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        "9.1　任何一方违反本合同约定，应承担违约责任，并赔偿对方因此遭受的直接损失。",
        indent=True,
    ))
    parts.append(_para(
        "9.2　甲方未按约定交付房屋的，每逾期一日应按月租金的 0.5% 向乙方支付违约金；"
        "逾期超过 15 日的，乙方有权解除合同。",
        indent=True,
    ))
    parts.append(_para(
        "9.3　乙方无正当理由提前退租的，已付租金不予退还，并按一个月租金标准向甲方支付违约金；"
        "甲方无正当理由提前收回房屋的，应退还剩余租金及押金，并按一个月租金标准向乙方支付违约金。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第十条　争议解决", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        "10.1　因本合同发生的争议，双方应友好协商解决；协商不成的，任何一方可向房屋所在地"
        "人民法院提起诉讼。",
        indent=True,
    ))
    parts.append(_para(
        "10.2　本合同适用中华人民共和国法律。",
        indent=True, space_after=120,
    ))

    parts.append(_para("第十一条　其他约定", bold=True, size=24, space_before=120, space_after=60))
    parts.append(_para(
        "11.1　本合同未尽事宜，双方可另行签订补充协议，补充协议与本合同具有同等效力。",
        indent=True,
    ))
    parts.append(_para(
        "11.2　本合同一式两份，甲、乙双方各执一份，自双方签字（盖章）之日起生效。",
        indent=True,
    ))
    parts.append(_para(
        f"11.3　其他：{BLANK}。", indent=True, space_after=200,
    ))

    parts.append(_para(
        "（以下为签署栏，请双方核对身份信息后签字并按手印）", size=18, align="center", space_after=160,
    ))
    parts.append(_para(
        f"甲方（签字／盖章）：{BLANK}　　　　乙方（签字／盖章）：{BLANK}", space_after=120,
    ))
    parts.append(_para(
        f"日期：　　年　　月　　日　　　　　　　日期：　　年　　月　　日", space_after=200,
    ))

    # 附件一：交接清单
    parts.append(_para("附件一：房屋及附属设施交接清单", bold=True, size=24, align="center", space_before=200, space_after=120))
    parts.append(_para(
        "交接日期：　　年　　月　　日　　交接时水表读数：＿＿＿＿　电表读数：＿＿＿＿　燃气表读数：＿＿＿＿",
        space_after=120,
    ))
    for line in (
        "钥匙：＿＿ 把（含门禁卡 ＿＿ 张）　　门锁是否完好：□ 是　□ 否",
        "家具家电（名称 / 数量 / 现状）：",
        "＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿",
        "＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿",
        "房屋现状及需要说明的问题：",
        "＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿",
    ):
        parts.append(_para(line, space_after=60))
    parts.append(_blank_para())
    parts.append(_para("甲方确认：＿＿＿＿＿＿＿＿　　　　乙方确认：＿＿＿＿＿＿＿＿", space_before=200))

    return _build_docx("".join(parts))


def contract_filename(lease=None) -> str:
    """下载文件名：带租客与房屋名，避免多份合同混在一起。"""
    parts = ["房屋租赁合同"]
    if lease is not None:
        tenant = (getattr(lease, "tenant_name", "") or "").strip()
        lease_no = (getattr(lease, "lease_no", "") or "").strip()
        if tenant:
            parts.append(tenant)
        if lease_no:
            parts.append(lease_no)
    return "-".join(parts) + ".docx"
