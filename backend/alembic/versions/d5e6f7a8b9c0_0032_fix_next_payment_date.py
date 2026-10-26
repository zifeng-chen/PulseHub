"""0032：修复「已还款但下次还款日没推进」的历史数据。

旧版本登记还款时不会推进 `next_payment_date`，导致：
- 只填了还款日的存量贷款 `next_payment_date` 一直是 NULL，页面按还款日推出来的
  永远是「今天」，看起来像没还过；
- 有的记录停在上一期的日期上。

本次把有还款记录、但下次还款日仍在还款日之前（或为空）的负债，按
「最后一笔还款 + 1 个还款周期（对齐还款日）」补正。补正后若仍早于今天，
说明确实逾期，交给状态计算如实显示。

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None

_FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12}


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _advance(base: datetime, freq: str, payment_day: int | None) -> datetime:
    """往后推一个还款周期，并按还款日对齐（月末自动收敛）。"""
    if freq == "weekly":
        return base + timedelta(days=7)
    if freq == "one_time":
        return base
    months = _FREQ_MONTHS.get(freq or "monthly", 1)
    year = base.year
    month = base.month + months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(payment_day or base.day, _days_in_month(year, month))
    return base.replace(year=year, month=month, day=day)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT l.id, l.payment_day, l.payment_frequency, l.next_payment_date,
               MAX(r.occurred_at) AS last_occurred
        FROM liabilities l
        JOIN liability_repayments r
          ON r.liability_id = l.id AND r.deleted_at IS NULL
        WHERE l.deleted_at IS NULL
        GROUP BY l.id, l.payment_day, l.payment_frequency, l.next_payment_date
    """)).fetchall()

    for liability_id, payment_day, freq, next_due, last_occurred in rows:
        if last_occurred is None:
            continue
        if next_due is not None and next_due > last_occurred:
            continue  # 已经推进过，不动
        fixed = _advance(last_occurred, freq or "monthly", payment_day)
        conn.execute(
            sa.text("UPDATE liabilities SET next_payment_date = :due WHERE id = :id"),
            {"due": fixed, "id": liability_id},
        )
        print(f"[0032] {liability_id}: next_payment_date → {fixed.date()}")


def downgrade() -> None:
    # 数据修复不回滚（回滚会重新变成「已还款却显示今天」）
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
