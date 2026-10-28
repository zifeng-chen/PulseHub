"""统计期间边界与现金流跑道（后端确定性计算）。

为什么单独测：
- 期间边界算错不会报错，只会让「本月支出」多一笔或漏一笔，用户对不上数；
- 业务时间是本地墙钟（app/core/timeutil.py），边界必须是「本月 1 日 00:00 含、
  下月 1 日 00:00 不含」，跨年月份尤其容易写错；
- 跑道是逐月模拟（收益滚存 + 通胀递增），阈值与回退逻辑要钉住。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.stats import RUNWAY_MAX_MONTHS, _dynamic_runway, period_range
from tests.conftest import auth, complete_setup

TZ = "Asia/Shanghai"


class TestPeriodRange:
    """纯函数：边界对齐与标签。"""

    def test_current_month_starts_first_day_and_ends_next_month(self):
        start, end, label = period_range("current_month", TZ)
        assert start.day == 1 and (start.hour, start.minute, start.second) == (0, 0, 0)
        assert end.day == 1
        assert (end.year * 12 + end.month) - (start.year * 12 + start.month) == 1
        # 结束不含：标签末日应为 end - 1 天
        assert label.endswith(f"{(end - timedelta(days=1)):%Y-%m-%d}")

    def test_last_month_is_the_previous_full_month(self):
        start, end, _ = period_range("last_month", TZ)
        this_month_start = period_range("current_month", TZ)[0]
        # 上月结束 = 本月开始
        assert end == this_month_start
        assert (end.year * 12 + end.month) - (start.year * 12 + start.month) == 1
        assert start.day == 1

    @pytest.mark.parametrize(("period", "months"), [("last_3_months", 3), ("last_6_months", 6)])
    def test_multi_month_windows_span_exact_months(self, period, months):
        start, end, _ = period_range(period, TZ)
        assert start.day == 1 and end.day == 1
        assert (end.year * 12 + end.month) - (start.year * 12 + start.month) == months

    def test_current_year(self):
        start, end, _ = period_range("current_year", TZ)
        assert (start.month, start.day) == (1, 1)
        assert (end.month, end.day) == (1, 1)
        assert end.year == start.year + 1

    def test_all_has_no_bounds(self):
        assert period_range("all", TZ) == (None, None, "全部期间")

    def test_unknown_period_falls_back_to_all(self):
        assert period_range("whatever", TZ)[:2] == (None, None)

    def test_boundaries_are_naive_local_wallclock(self):
        """边界必须与业务时间同口径（naive 本地），否则东八区会整体偏 8 小时。"""
        start, end, _ = period_range("current_month", TZ)
        assert start.tzinfo is None and end.tzinfo is None


async def _expense_at(client, headers, account_id: str, amount: str, occurred_at: str):
    r = await client.post("/api/v1/ledger/events", headers=headers, json={
        "event_type": "expense", "title": "测试支出", "amount": amount,
        "account_id": account_id, "occurred_at": occurred_at,
    })
    assert r.status_code == 200, r.text
    event = r.json()["data"]
    await client.post(f"/api/v1/ledger/events/{event['id']}/confirm", headers=headers,
                      json={"version": event["version"]})
    return event


class TestPeriodBoundariesThroughApi:
    """接口层：月初 00:00 计入本月，上月最后一刻不计入。"""

    @pytest.fixture
    async def setup(self, client):
        tokens = await complete_setup(client)
        h = auth(tokens["access"])
        r = await client.post("/api/v1/ledger/accounts", headers=h, json={
            "name": "边界测试卡", "account_type": "bank", "currency": "CNY",
            "opening_balance": "10000",
        })
        assert r.status_code == 200, r.text
        return h, r.json()["data"]["id"]

    async def _expense_total(self, client, h, period: str) -> float:
        r = await client.get("/api/v1/dashboard/snapshot", headers=h, params={"period": period})
        assert r.status_code == 200, r.text
        return float(r.json()["data"]["metrics"]["expense"])

    async def test_first_second_of_month_counts_this_month(self, client, setup):
        h, account_id = setup
        today = date.today()
        first = today.replace(day=1)
        await _expense_at(client, h, account_id, "111", f"{first.isoformat()}T00:00:00")

        assert await self._expense_total(client, h, "current_month") == pytest.approx(111)
        # 上月窗口不应包含它
        assert await self._expense_total(client, h, "last_month") == pytest.approx(0)

    async def test_last_second_of_previous_month_excluded(self, client, setup):
        h, account_id = setup
        first = date.today().replace(day=1)
        prev_last = first - timedelta(days=1)
        await _expense_at(client, h, account_id, "222", f"{prev_last.isoformat()}T23:59:59")

        assert await self._expense_total(client, h, "current_month") == pytest.approx(0)
        assert await self._expense_total(client, h, "last_month") == pytest.approx(222)

    async def test_all_period_includes_everything(self, client, setup):
        h, account_id = setup
        first = date.today().replace(day=1)
        prev_last = first - timedelta(days=1)
        await _expense_at(client, h, account_id, "10", f"{first.isoformat()}T00:00:00")
        await _expense_at(client, h, account_id, "20", f"{prev_last.isoformat()}T23:59:59")
        assert await self._expense_total(client, h, "all") == pytest.approx(30)


class TestDynamicRunway:
    """跑道模拟：资产滚存 + 支出通胀。"""

    def test_invalid_inputs_return_none(self):
        from decimal import Decimal

        assert _dynamic_runway(Decimal("0"), Decimal("1000")) is None
        assert _dynamic_runway(Decimal("10000"), Decimal("0")) is None
        assert _dynamic_runway(Decimal("-5"), Decimal("1000")) is None

    def test_never_bankrupt_returns_none(self):
        from decimal import Decimal

        # 1000 万本金、每月只花 1000：撑满 120 个月 → None（表示 10 年以上）
        assert _dynamic_runway(Decimal("10000000"), Decimal("1000")) is None

    def test_thin_buffer_runs_out_quickly(self):
        from decimal import Decimal

        months = _dynamic_runway(Decimal("2000"), Decimal("1000"))
        assert months is not None
        # 第一个月：2000*1.0021 - 1000 ≈ 1004；第二个月见底
        assert 1 <= months <= 3

    def test_yield_extends_runway_versus_no_yield(self):
        from decimal import Decimal

        months = _dynamic_runway(Decimal("120000"), Decimal("5000"), months=120)
        assert months is not None
        # 静态算法是 24 个月；有 2.5% 收益 + 通胀后应在同一量级，且不超过上限
        assert 20 <= months <= RUNWAY_MAX_MONTHS

    def test_returns_the_month_it_runs_dry(self):
        from decimal import Decimal

        # 返回「第几个月撑不住」。每月先计收益再扣支出，所以 1000 元够 1000 元开销时，
        # 首月利息（约 2.08 元）会让余额还剩一点，实际第 2 个月才见底。
        assert _dynamic_runway(Decimal("900"), Decimal("1000")) == 1
        assert _dynamic_runway(Decimal("1000"), Decimal("1000")) == 2
