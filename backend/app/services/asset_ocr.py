"""房产证识别 → 资产字段（AI 协助录入）。

不做"全自动入库"：OCR 只负责把图片转文字，再用确定性规则提取常见字段，
最终由用户在资产表单里核对后保存。识别失败会给出明确原因，可改手工录入。

真实房本照片经过 OCR 后常见五种情况，都要能吃下：
1. 标签与值同一行，用「：」分隔；
2. 标签单独一行，值在下一行（新版不动产权证最常见）；
3. 证号直接印成「京(2020)朝阳区不动产权第 0001234 号」，没有「证书号：」字样；
4. 标签被 OCR 插进空格（「坐 落」「不 动 产 单 元 号」），或者几个字段被并到同一行；
5. 只印「不动产单元号」而没有单独的证书号 —— 按约定它就是不动产权证号。

取不到就留空，绝不猜。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.core.context import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.adapters import build_adapters

_ALLOWED = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
_MAX_BYTES = 10 * 1024 * 1024

# 房产证上会用到的字段名。既用来定位标签，也用来把「值」在下一个标签处截断，
# 否则「坐落 北京市… 不动产单元号 1101…」会把后面的字段一起吞进地址。
_LABELS = [
    "权利人", "共有情况",
    "房屋坐落", "房屋位置", "房屋地址", "房屋所在地", "坐落地址", "坐落",
    "不动产单元号",
    "不动产权证书编号", "不动产权证书号", "不动产权证号", "不动产权证书",
    "房屋所有权证号", "房屋所有权证", "房地产权证", "房地权证", "房权证",
    "权利类型", "权利性质", "权利其他状况",
    "规划用途", "房屋用途", "设计用途", "用途",
    "房屋建筑面积", "套内建筑面积", "建筑面积", "共有宗地面积", "分摊面积", "面积",
    "土地使用期限", "使用期限", "竣工日期", "竣工时间", "建成年份", "建成年代",
    "登记时间", "登记机构", "附记", "证书编号", "编号", "备注",
]
_TRAIL = " ：:，,、;；。·|｜\t"

_SORTED_LABELS = sorted(_LABELS, key=len, reverse=True)


def _loose(label: str) -> str:
    """标签可能被 OCR 插进空格：「坐 落」「不 动 产 单 元 号」，逐字匹配更稳。"""
    return r"\s*".join(re.escape(ch) for ch in label)


# 值后面紧跟「另一个字段」时才算截断；房权证/不动产权证这类是号码本身的一部分，不能切
_NEXT_FIELD_LABELS = [
    "权利人", "共有情况", "房屋坐落", "房屋位置", "坐落地址", "坐落",
    "不动产单元号", "权利类型", "权利性质", "规划用途", "房屋用途", "用途",
    "房屋建筑面积", "套内建筑面积", "建筑面积", "面积",
    "使用期限", "竣工日期", "竣工时间", "建成年份", "登记机构", "登记时间", "附记",
]

_OWNERSHIP_PATTERNS = [
    # 京(2020)朝阳区不动产权第 0001234 号
    r"([\u4e00-\u9fa5]{0,10}[（(]\s*\d{4}\s*[）)]\s*[\u4e00-\u9fa5\s]{0,14}"
    + _loose("不动产权第") + r"\s*[0-9A-Za-z]+\s*号)",
    r"(" + _loose("不动产权第") + r"\s*[0-9A-Za-z]+\s*号)",
]
_OWNERSHIP_LABELS = ["不动产权证书编号", "不动产权证书号", "不动产权证号"]
_TITLE_PATTERNS = [
    r"([\u4e00-\u9fa5]{0,10}房权证[^\n]{2,32})",
    r"([\u4e00-\u9fa5]{0,10}房地权证[^\n]{2,32})",
    r"房屋所有权证\s*[：:]?\s*([0-9A-Za-z第号\-]{4,})",
]
_TITLE_LABELS = ["房屋所有权证号", "房产证号", "房屋所有权证"]
_ADDRESS_LABELS = ["房屋坐落", "房屋位置", "坐落地址", "房屋地址", "房屋所在地", "坐落", "地址"]


def _lines(text: str) -> list[str]:
    normalized = (
        text.replace("\u3000", " ")
        .replace("\xa0", " ")
        .replace("．", ".")
    )
    return [ln.strip() for ln in normalized.splitlines() if ln.strip()]


def _cut(value: str, labels: list[str] | None = None) -> str:
    """值里混进了下一个字段就截断；整个值都是标签时返回空。"""
    if not value:
        return ""
    cut = len(value)
    for label in (labels or _SORTED_LABELS):
        match = re.search(_loose(label), value)
        if match and 0 < match.start() < cut:
            cut = match.start()
    return value[:cut].strip(_TRAIL)


def _extend(value: str, lines: list[str], idx: int, max_len: int) -> str:
    """地址被折成两行时补上第二行（只认「3 号楼 / 2 单元 / 1201 室」这类续行）。"""
    if len(value) >= max_len or idx + 1 >= len(lines):
        return value[:max_len]
    nxt = lines[idx + 1].strip(_TRAIL)
    if not nxt or not _cut(nxt):
        return value[:max_len]
    if re.match(r"^(?:\d+|[一二三四五六七八九十]+)\s*(?:号楼|栋|幢|座|单元|层|室|户|号)", nxt):
        return (value + nxt)[:max_len]
    return value[:max_len]


def _value_from_lines(
    lines: list[str],
    labels: list[str],
    max_len: int = 90,
    join_continuation: bool = False,
    cut_labels: list[str] | None = None,
) -> str:
    """按「同行取值 → 下一行取值」的顺序找标签对应的值。"""
    alt = "|".join(_loose(label) for label in sorted(labels, key=len, reverse=True))
    pattern = re.compile(rf"^(?:{alt})\s*[：:]?\s*(.*)$")
    for idx, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        value = _cut(match.group(1).strip(_TRAIL), cut_labels)
        if value:
            return _extend(value, lines, idx, max_len) if join_continuation else value[:max_len]
        for nxt in lines[idx + 1: idx + 4]:
            candidate = _cut(nxt.strip(_TRAIL), cut_labels)
            if not candidate:
                continue
            return _extend(candidate, lines, idx + 1, max_len) if join_continuation else candidate[:max_len]

    # 兜底：一行里挤了好几个字段时，标签不在行首（「… 权利人：王五 用途：住宅」）
    joined = "\n".join(lines)
    for label in sorted(labels, key=len, reverse=True):
        for match in re.finditer(_loose(label) + r"\s*[：:]?\s*([^\n]*)", joined):
            value = _cut(match.group(1).strip(_TRAIL), cut_labels)
            if value:
                return value[:max_len]
    return ""


def _find_number(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = (match.group(1) if match.groups() else match.group(0)).strip(_TRAIL)
        if value:
            return value
    return ""


def _unit_no(text: str, lines: list[str]) -> str:
    """不动产单元号：28 位左右，OCR 常在中间插空格，统一去掉。"""
    value = _value_from_lines(lines, ["不动产单元号"], 40)
    if not value:
        value = _find_number(text, [
            _loose("不动产单元号") + r"\s*[：:]?\s*([0-9A-Za-z][0-9A-Za-z\- ]{7,})",
        ])
    return re.sub(r"[\s\-]+", "", value)


def _cert_numbers(text: str, lines: list[str], unit_no: str) -> tuple[str, str]:
    """返回 (不动产权证号, 房产证号)。两类证件编号格式不同，分别提取。"""
    ownership = _find_number(text, _OWNERSHIP_PATTERNS)
    if not ownership:
        ownership = _value_from_lines(lines, _OWNERSHIP_LABELS, 40, cut_labels=_NEXT_FIELD_LABELS)
        ownership = re.sub(r"\s+", "", ownership)
    if not ownership:
        ownership = _find_number(text, [
            _loose("不动产权") + r"(?:证\s*书|证)?(?:编)?\s*号\s*[：:]?\s*([0-9A-Za-z][0-9A-Za-z\-\s]{7,})",
        ])
        ownership = re.sub(r"\s+", "", ownership)

    # 老版：房屋所有权证 / 房权证
    title = _find_number(text, _TITLE_PATTERNS)
    if not title:
        title = _value_from_lines(lines, _TITLE_LABELS, 60, cut_labels=_NEXT_FIELD_LABELS)
    if title:
        # 同一行里可能还跟着下一个标签（不能按「房权证」切，那是号码本身）
        title = _cut(title, _NEXT_FIELD_LABELS)

    # 新版证书常只印「不动产单元号」：按约定它是不动产权证号的兜底来源
    if not ownership and unit_no:
        ownership = unit_no
    # 新版证书里「房产证编号」与「不动产权证号」是同一个号，两栏都填上
    if ownership and not title:
        title = ownership
    return ownership, title


def _area(text: str, lines: list[str], labels: list[str]) -> str:
    for label in labels:
        for match in re.finditer(rf"{_loose(label)}\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?)", text):
            # 「套内建筑面积」里也含「建筑面积」，别把套内面积当成建筑面积
            if label == "建筑面积" and re.search(r"套内\s*$", text[: match.start()]):
                continue
            return match.group(1)
    value = _value_from_lines(lines, labels, 24)
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", value)
    return match.group(0) if match else ""


def parse_property_text(text: str) -> dict:
    """从房产证 OCR 文本中提取常见字段（不编造：提取不到就留空）。"""
    lines = _lines(text)
    normalized = "\n".join(lines)

    unit_no = _unit_no(normalized, lines)
    ownership_no, title_no = _cert_numbers(normalized, lines, unit_no)
    address = _value_from_lines(lines, _ADDRESS_LABELS, 120, join_continuation=True)

    building_area = _area(normalized, lines, ["房屋建筑面积", "建筑面积"])
    inner_area = _area(normalized, lines, ["套内建筑面积"])
    owner = _value_from_lines(lines, ["权利人"], 40)
    purpose = _value_from_lines(lines, ["规划用途", "房屋用途", "设计用途", "用途"], 30)
    built_year = _find_number(normalized, [
        r"(?:" + "|".join(_loose(x) for x in ("竣工日期", "竣工时间", "建成年份", "建成年代", "建成年月"))
        + r")[^\d]{0,6}((?:19|20)\d{2})",
        r"((?:19|20)\d{2})\s*年\s*\d{1,2}\s*月\s*(?:竣工|建成)",
    ])

    name = address or (f"{owner} 的房产" if owner else "")
    return {
        "name": name[:120],
        "address": address,
        "building_area": building_area,
        "inner_area": inner_area,
        "ownership_no": ownership_no,
        "title_no": title_no,
        "owner_name": owner,
        "purpose": purpose,
        "built_year": built_year,
    }


async def extract_property_fields(db, user: CurrentUser, file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED:
        raise AppError(ErrorCode.VALIDATION_ERROR,
                       "请上传房产证照片（PNG / JPG / WEBP）", status_code=422)
    content = await file.read()
    if not content:
        raise AppError(ErrorCode.VALIDATION_ERROR, "文件为空", status_code=422)
    if len(content) > _MAX_BYTES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "图片不能超过 10MB", status_code=422)

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fh:
            fh.write(content)
            tmp_path = fh.name
        adapter = build_adapters(db, user.household_id)["ocr"]
        text = await adapter.recognize(tmp_path)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    fields = parse_property_text(text)
    filled = sum(1 for v in fields.values() if v)
    return {
        "fields": fields,
        "filled": filled,
        "raw_text": text[:4000],
        "note": "识别结果需你核对后再保存；提取不到的字段请手工补充。",
    }
