"""借出款分类、房产证解析、还款日提醒与投资年化（2026-10 新增能力）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sqlalchemy import select

from app.core.context import CurrentUser
from app.db.base import SessionLocal
from app.db.models.identity import HouseholdMember, User
from app.services.asset_ocr import parse_property_text
from app.services.investments import _xirr
from app.services.reminders import send_due_bills_today
from tests.conftest import auth, complete_setup


# ---------------- 房产证解析 ----------------

def test_parse_property_certificate_text():
    text = """不动产权证书
权利人：张三
房屋坐落：北京市朝阳区望京街道望京花园 1 号楼 2 单元 301
房屋建筑面积：120.5 平方米
套内建筑面积：98.2 平方米
不动产权证号：京(2020)朝阳不动产权第 0001234 号
规划用途：住宅
竣工日期：2015-06-30
"""
    fields = parse_property_text(text)
    assert fields["building_area"] == "120.5"
    assert fields["inner_area"] == "98.2"
    assert fields["address"].startswith("北京市朝阳区")
    assert "0001234" in fields["ownership_no"]
    assert fields["owner_name"] == "张三"
    assert fields["purpose"] == "住宅"
    assert fields["built_year"] == "2015"
    assert fields["name"].startswith("北京市朝阳区")


def test_parse_property_certificate_never_fabricates():
    fields = parse_property_text("这是一张模糊的图片，没有任何可识别字段")
    assert all(value == "" for value in fields.values())


# ---------------- 还款日提醒 ----------------

async def test_repayment_reminder_from_account_due_day(client):
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    today = datetime.now(ZoneInfo("Asia/Shanghai")).day
    if today > 28:
        pytest.skip("还款日只允许 1-28，月末三天无法构造「今天到期」")

    r = await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "还款日测试卡", "account_type": "bank", "currency": "CNY",
        "credit_limit": "50000", "credit_used": "3000", "due_day": today,
    })
    assert r.json()["success"] is True, r.text

    resp = await client.get("/api/v1/ledger/reminders", headers=h)
    groups = resp.json()["data"]["groups"]
    bills = next(g for g in groups if g["type"] == "credit_card_bill")
    keys = [it["key"] for it in bills["items"]]
    assert any(k.startswith("acctbill:") for k in keys), keys
    item = next(it for it in bills["items"] if it["key"].startswith("acctbill:"))
    assert item["days_left"] == 0
    assert item["amount"].replace(",", "") == "3000.00"


async def _owner_user() -> CurrentUser:
    async with SessionLocal() as db:
        member = (await db.execute(select(HouseholdMember))).scalars().first()
        user = (await db.execute(select(User).where(User.id == member.user_id))).scalars().first()
        return CurrentUser(
            user_id=user.id, username=user.username, display_name=user.display_name,
            is_system_admin=True, household_id=member.household_id, role=member.role,
        )


async def test_due_day_send_is_deduplicated(client):
    """还款日当天只发一次：第一次尝试发送（未配置渠道→失败也留记录），第二次跳过。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    today = datetime.now(ZoneInfo("Asia/Shanghai")).day
    if today > 28:
        pytest.skip("还款日只允许 1-28，月末三天无法构造「今天到期」")
    await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "去重测试卡", "account_type": "bank", "currency": "CNY",
        "credit_limit": "20000", "credit_used": "1200", "due_day": today,
    })

    user = await _owner_user()
    async with SessionLocal() as db:
        first = await send_due_bills_today(db, user)
    assert first["due"] == 1
    assert first["failed"] == 1  # 未配置通知渠道：发送失败，但记录已写

    async with SessionLocal() as db:
        second = await send_due_bills_today(db, user)
    assert second["due"] == 1
    assert second["skipped"] == 1
    assert second["failed"] == 0


# ---------------- 投资年化（XIRR） ----------------

def test_xirr_annualized_return():
    d0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rate = _xirr([(d0, -1000.0), (d0 + timedelta(days=365), 1100.0)])
    assert rate is not None
    assert abs(rate - 0.10) < 0.002

    # 只有一笔现金流 / 没有正负号变化 → 无法计算
    assert _xirr([(d0, -1000.0)]) is None
    assert _xirr([(d0, -1000.0), (d0 + timedelta(days=365), -500.0)]) is None


# ---------------- 借出款分类 ----------------

async def test_receivable_category_seeded(client):
    """借出款是一级分类，下面挂现金/银行卡/信用卡/电子钱包等出借方式。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])
    r = await client.get("/api/v1/settings/categories?domain=asset", headers=h)
    items = r.json()["data"]["items"]
    root = next(c for c in items if c["name"] == "借出款")
    assert root["parent_id"] is None
    children = {c["name"] for c in items if c["parent_id"] == root["id"]}
    assert children >= {"现金出借", "银行卡出借", "信用卡出借", "电子钱包出借", "其他出借"}


async def test_receivable_create_and_partial_repayment(client):
    """借出形成应收、回款分批收回；借出与回款都不影响净资产总额。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    acc = (await client.post("/api/v1/ledger/accounts", headers=h, json={
        "name": "现金", "account_type": "cash", "currency": "CNY",
        "opening_balance": "50000", "include_in_net_worth": True,
    })).json()["data"]

    cats = (await client.get("/api/v1/settings/categories?domain=asset", headers=h)).json()["data"]["items"]
    root = next(c for c in cats if c["name"] == "借出款")
    cash_lend = next(c for c in cats if c["parent_id"] == root["id"] and c["name"] == "现金出借")

    r = await client.post("/api/v1/ledger/assets", headers=h, json={
        "asset_type": "other", "name": "借给张三", "category_id": cash_lend["id"],
        "currency": "CNY", "acquire_cost": "10000", "current_value": "10000",
        "include_in_net_worth": True,
        "receivable": {
            "borrower_name": "张三", "borrower_id_no": "110101199001011234",
            "lend_type": "cash", "principal": "10000",
            "source_account_id": acc["id"], "repayment_method": "lump_sum",
            "due_date": "2026-12-31",
        },
        "acquisition": {"mode": "confirm", "account_id": acc["id"], "amount": "10000"},
    })
    assert r.json()["success"] is True, r.text
    asset_id = r.json()["data"]["id"]

    detail = (await client.get(f"/api/v1/ledger/assets/{asset_id}", headers=h)).json()["data"]
    rec = detail["related"]["receivable"]
    assert rec["outstanding"] == "10000.00"
    assert rec["borrower_name"] == "张三"

    balance = (await client.get(f"/api/v1/ledger/accounts/{acc['id']}", headers=h)).json()["data"]["balance"]
    assert float(balance) == 40000.0

    r = await client.post(f"/api/v1/ledger/assets/receivables/{asset_id}/repayments", headers=h, json={
        "amount": "4000", "account_id": acc["id"], "occurred_at": "2026-10-19T12:00:00+08:00",
        "note": "微信转账",
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["outstanding"] == "6000.00"

    balance = (await client.get(f"/api/v1/ledger/accounts/{acc['id']}", headers=h)).json()["data"]["balance"]
    assert float(balance) == 44000.0

    r = await client.post(f"/api/v1/ledger/assets/receivables/{asset_id}/repayments", headers=h, json={
        "amount": "6000", "account_id": acc["id"],
    })
    assert r.json()["success"] is True, r.text
    assert r.json()["data"]["settled"] is True

    detail = (await client.get(f"/api/v1/ledger/assets/{asset_id}", headers=h)).json()["data"]
    assert detail["status"] == "archived"
    assert float(detail["current_value"]) == 0.0
    assert len(detail["related"]["receivable"]["repayments"]) == 2
