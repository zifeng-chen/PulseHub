"""房产证 OCR 文本解析（真实房本三种常见排版）。"""

from __future__ import annotations

from app.services.asset_ocr import parse_property_text


def test_new_certificate_labels_on_separate_lines():
    """新版不动产权证：标题行与值分行，证号没有「证书号：」字样。"""
    raw = "\n".join([
        "不动产权证书",
        "京(2020)朝阳区不动产权第 0001234 号",
        "权利人",
        "张三",
        "共有情况",
        "单独所有",
        "坐落",
        "北京市朝阳区望京街道阜通东大街 6 号院 3 号楼 2 单元 1201",
        "不动产单元号",
        "110105 011001 GB00045 F00010001",
        "权利类型",
        "国有建设用地使用权/房屋所有权",
        "用途",
        "住宅",
        "面积",
        "房屋建筑面积 89.50㎡",
        "套内建筑面积 72.30㎡",
        "使用期限",
        "2010年01月01日起 2080年01月01日止",
    ])
    fields = parse_property_text(raw)
    assert fields["ownership_no"] == "京(2020)朝阳区不动产权第 0001234 号"
    assert fields["title_no"] == fields["ownership_no"]
    assert fields["address"].startswith("北京市朝阳区望京街道阜通东大街")
    assert fields["building_area"] == "89.50"
    assert fields["inner_area"] == "72.30"
    assert fields["owner_name"] == "张三"
    assert fields["purpose"] == "住宅"
    assert fields["name"].startswith("北京市朝阳区")


def test_old_certificate_same_line_with_colons():
    """老版房屋所有权证：标签与值同一行，用冒号分隔。"""
    raw = "\n".join([
        "房屋所有权证",
        "房屋所有权证号：京房权证朝字第 123456 号",
        "房屋坐落：北京市朝阳区建国路 88 号院 5 号楼 1 单元 602",
        "建筑面积：89.5 平方米",
        "权利人：李四",
        "规划用途：住宅",
        "竣工日期：2008年06月",
    ])
    fields = parse_property_text(raw)
    assert "123456" in fields["title_no"]
    assert fields["ownership_no"] == ""
    assert fields["address"] == "北京市朝阳区建国路 88 号院 5 号楼 1 单元 602"
    assert fields["building_area"] == "89.5"
    assert fields["owner_name"] == "李四"
    assert fields["built_year"] == "2008"


def test_certificate_number_without_label():
    """证号直接印在证书上，前后没有任何「证书号」标签。"""
    raw = "京(2017)海不动产权第 0034567 号\n坐落 北京市海淀区中关村大街 1 号 2 层 201"
    fields = parse_property_text(raw)
    assert fields["ownership_no"] == "京(2017)海不动产权第 0034567 号"
    assert fields["address"] == "北京市海淀区中关村大街 1 号 2 层 201"


def test_missing_fields_stay_empty():
    """识别不到就留空，不能编造。"""
    fields = parse_property_text("这是一张模糊的照片")
    assert fields["address"] == ""
    assert fields["ownership_no"] == ""
    assert fields["title_no"] == ""
    assert fields["building_area"] == ""


def test_unit_no_fills_ownership_when_cert_no_missing():
    """只印「不动产单元号」时，按约定它就是不动产权证号，两栏都填上。"""
    raw = "\n".join([
        "不动产权证书",
        "权利人 王五",
        "坐落 北京市海淀区上地三街 9 号院 5 号楼 3 单元 801",
        "不动产单元号 110108 008001 GB00012 F00010001",
        "用途 住宅",
    ])
    fields = parse_property_text(raw)
    assert fields["ownership_no"] == "110108008001GB00012F00010001"
    assert fields["title_no"] == fields["ownership_no"]
    assert fields["address"] == "北京市海淀区上地三街 9 号院 5 号楼 3 单元 801"


def test_ownership_label_carrying_unit_code():
    """「不动产权证号」后面直接跟 28 位单元号（部分电子证照的印法）。"""
    raw = "\n".join([
        "不动产权证号：110105003001GB00045F00010001",
        "坐落：北京市朝阳区三里屯路 1 号 2 单元 902",
    ])
    fields = parse_property_text(raw)
    assert fields["ownership_no"] == "110105003001GB00045F00010001"
    assert fields["title_no"] == fields["ownership_no"]
    assert fields["address"].startswith("北京市朝阳区三里屯路")


def test_labels_split_by_spaces_and_merged_lines():
    """OCR 在标签里插了空格，还把地址和不动产单元号并到一行。"""
    raw = "\n".join([
        "不 动 产 权 证 书",
        "坐 落 北京市西城区金融大街 33 号 6 层 601 不 动 产 单 元 号 110102 001002 GB00033 F00010002",
        "权 利 人 赵六",
    ])
    fields = parse_property_text(raw)
    assert fields["address"] == "北京市西城区金融大街 33 号 6 层 601"
    assert fields["ownership_no"] == "110102001002GB00033F00010002"
    assert fields["owner_name"] == "赵六"


def test_address_wrapped_to_second_line():
    """地址太长被 OCR 折行，第二行以「3 号楼」开头时要拼回去。"""
    raw = "\n".join([
        "坐落",
        "北京市通州区运河东大街 12 号院",
        "3 号楼 2 单元 1801",
        "建筑面积 120.5 ㎡",
    ])
    fields = parse_property_text(raw)
    assert fields["address"] == "北京市通州区运河东大街 12 号院3 号楼 2 单元 1801"
    assert fields["building_area"] == "120.5"


def test_multiple_fields_on_one_line():
    """一行里挤了多个字段时，靠「下一个标签」截断，不能把后面的字段吞进值里。"""
    raw = "坐落：朝阳区建国路 88 号 SOHO 现代城 建筑面积：66.8㎡ 权利人：王五 用途：住宅"
    fields = parse_property_text(raw)
    assert fields["address"] == "朝阳区建国路 88 号 SOHO 现代城"
    assert fields["building_area"] == "66.8"
    assert fields["owner_name"] == "王五"
    assert fields["purpose"] == "住宅"
