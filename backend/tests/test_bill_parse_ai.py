"""对话式录入改走本地 AI（用户要求：不新增入口，直接替换规则解析）。

测试重点：
1. 模型输出再脏也要能抠出 JSON；
2. 模型给的「中文类型 / 带货币符号金额 / 相对日期」要能规整成标准条目；
3. 模型不可用时自动退回规则解析，接口永远可用。
"""

from __future__ import annotations

from datetime import datetime

from app.services.bill_parse import _extract_json_array, _item_from_ai

TODAY = datetime(2026, 10, 22)


def test_extract_json_array_tolerates_noise():
    assert _extract_json_array('好的，结果如下：[{"amount": "128"}]') == [{"amount": "128"}]
    assert _extract_json_array('```json\n[{"amount":"1"}]\n```') == [{"amount": "1"}]
    assert _extract_json_array('没有数组') is None
    assert _extract_json_array('') is None


def test_item_from_ai_normalizes_fields():
    acc_map = {"工资卡": "acc-1"}
    cat_map = {"餐饮": ("cat-1", "expense"), "工资": ("cat-2", "income")}
    row = {
        "date": "昨天",
        "type": "支出",
        "title": "跟同事吃火锅",
        "amount": "¥268.50",
        "account": "工资卡",
        "category": "餐饮",
    }
    item = _item_from_ai(row, 0, ["昨天跟同事吃火锅 268.5"], TODAY, acc_map, cat_map, None)
    assert item["event_type"] == "expense"
    assert item["amount"] == "268.50"
    assert item["occurred_at"].startswith("2026-10-21")
    assert item["account_id"] == "acc-1"
    assert item["category_id"] == "cat-1"
    assert item["ok"] is True
    assert item["confidence"] == "high"


def test_item_from_ai_marks_missing_amount():
    item = _item_from_ai({"type": "income", "title": "工资"}, 0, ["工资到账"], TODAY, {}, {}, None)
    assert item["event_type"] == "income"
    assert item["ok"] is False
    assert "金额" in item["errors"][0]


async def test_parse_text_falls_back_to_rules_without_ai(client):
    """测试环境没有本地模型：接口必须仍然可用，并标明走的是规则解析。"""
    from tests.conftest import auth, complete_setup

    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = (await client.post("/api/v1/ledger/parse-text", headers=h,
                           json={"text": "10月15日 超市买菜 128 元"})).json()
    assert r["success"] is True
    data = r["data"]
    assert data["parser"] == "rule"
    assert data["total"] >= 1
    assert data["items"][0]["amount"] == "128.00"
    assert data["ai_error"], "退回规则解析时要说明原因"
