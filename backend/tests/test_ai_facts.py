"""AI 上下文的计算口径：数字只准引用，不准模型自己重算。

背景：界面显示「可支撑 30.2 个月」，同一页的 AI 却算出 15 个月——因为
① 空月份被当成零支出；② 上下文只给了明细，没给已算好的指标，模型只能自己拼。
这里用测试守住三件事：
- 快照必须带「核心指标」块，并明确禁止重算；
- 跑道只统计有记账的月份（空月份不算零支出）；
- 问「能撑多久」走确定性回答，数值与统计服务完全一致。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.context import CurrentUser
from app.core.ids import new_id
from app.core.timeutil import local_now
from app.db.base import SessionLocal
from app.db.models.business import Account, BusinessEvent, EventMovement
from app.db.models.identity import User
from app.services import ai as ai_service
from app.services import stats as stats_service
from tests.conftest import auth, complete_setup


async def _context(db, client, headers) -> tuple[CurrentUser, dict]:
    r = await client.post("/api/v1/ledger/accounts", headers=headers, json={
        "name": "测试储蓄卡", "account_type": "bank", "currency": "CNY",
        "opening_balance": "100000",
    })
    account = r.json()["data"]
    acc = (await db.execute(select(Account).where(Account.id == account["id"]))).scalars().first()
    user_row = (await db.execute(select(User).where(User.username == "admin"))).scalars().first()
    user = CurrentUser(
        user_id=user_row.id, username=user_row.username, display_name=user_row.display_name,
        is_system_admin=True, household_id=acc.household_id, role="admin",
    )
    return user, account


async def _add_expense(db, user: CurrentUser, account_id: str, amount: str, when: datetime) -> None:
    event = BusinessEvent(
        id=new_id(), household_id=user.household_id,
        event_no=f"EV{new_id()[:8].upper()}", event_type="expense", status="confirmed",
        occurred_at=when, confirmed_at=when,
        amount=Decimal(amount), currency="CNY",
        original_amount=Decimal(amount), original_currency="CNY",
        base_currency="CNY", conversion_status="same_currency",
        account_id=account_id, title="测试消费", source="manual", note="测试",
        version=1, created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(event)
    await db.flush()
    db.add(EventMovement(
        id=new_id(), household_id=user.household_id, event_id=event.id,
        target_type="account", target_id=account_id, direction="out",
        amount=Decimal(amount), currency="CNY",
        created_by=user.user_id, updated_by=user.user_id,
    ))
    await db.flush()


@pytest.mark.asyncio
async def test_snapshot_puts_core_metrics_first(client):
    """快照必须把系统算好的指标放在前面，并标明「禁止重算」。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    async with SessionLocal() as db:
        user, account = await _context(db, client, h)
        now = local_now()
        await _add_expense(db, user, account["id"], "3000", now.replace(day=5, hour=12, minute=0, second=0))
        await db.commit()

        text = await ai_service._finance_snapshot(db, user)
        assert "核心指标" in text
        assert "禁止重算" in text
        assert "生存跑道" in text
        assert "未记账" in text
        # 净资产等式必须带构成，模型才不会自己拆
        assert "净资产" in text and "− 负债" in text
        # 不报「账户余额合计」：它含投资账户与公积金，和流动资金不是同一个数
        assert "账户余额合计" not in text


@pytest.mark.asyncio
async def test_runway_ignores_months_without_records(client):
    """只有一个月有记账时，月均支出就该等于那个月，样本数必须是 1。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    async with SessionLocal() as db:
        user, account = await _context(db, client, h)
        now = local_now()
        await _add_expense(db, user, account["id"], "3000", now.replace(day=5, hour=12, minute=0, second=0))
        await db.commit()

        snap = await stats_service.dashboard_snapshot(db, user, "current_month")
        liq = snap["liquidity"]
        assert liq["runway_sample_months"] == 1, liq
        assert liq["monthly_expense"] == "3000.00", liq["monthly_expense"]
        # 100000 期初 − 3000 支出 = 97000 流动资金
        assert liq["value"] == "97000.00", liq["value"]
        assert liq["runway_months"] == "32.3", liq["runway_months"]


@pytest.mark.asyncio
async def test_runway_answer_matches_stats(client):
    """问「能撑多久」走确定性回答，且数值与统计服务一致。"""
    tokens = await complete_setup(client)
    h = auth(tokens["access"])

    async with SessionLocal() as db:
        user, account = await _context(db, client, h)
        now = local_now()
        await _add_expense(db, user, account["id"], "3000", now.replace(day=5, hour=12, minute=0, second=0))
        await db.commit()

        snap = await stats_service.dashboard_snapshot(db, user, "current_month")
        expected = snap["liquidity"]["runway_months"]
        answer = await ai_service._deterministic_answer(db, user, "runway")
        assert expected in answer["answer"]
        assert "月均支出" in answer["answer"]
        # 分子分母都要给出来，用户才能核对
        assert snap["liquidity"]["monthly_expense"] in answer["answer"]


def test_runway_questions_use_deterministic_path():
    """问跑道走确定性；问辞职决策交给模型（离职口径不同）。"""
    assert ai_service._detect_topic("我还能撑多久") == "runway"
    assert ai_service._detect_topic("生存跑道还有几个月") == "runway"
    assert ai_service._should_use_deterministic("我还能撑多久", "runway", "auto") is True
    assert ai_service._should_use_deterministic("我要辞职，还能撑多久", "runway", "auto") is False
