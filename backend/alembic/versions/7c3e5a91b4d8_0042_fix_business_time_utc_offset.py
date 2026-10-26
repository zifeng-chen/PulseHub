"""0042：纠正历史数据里按 UTC 落库的业务时间（+账本时区偏移）。

背景
----
业务时间应存「账本本地墙钟」（见 app/core/timeutil.py）。2026-10-21 的时区审查发现
几处代码用 `datetime.now(timezone.utc)` 给业务字段写默认值，导致这些字段存的是
UTC 字面值，比本地墙钟早一个时区偏移（北京时间早 8 小时）：

1. `assets.last_valuation_date` —— 新建资产与被改估值时由系统写入，用户从不填
2. `accounts.opening_date` —— 用户不填时默认取 now（UTC），表现为「开户日 == 创建时刻」
3. `assets.acquire_date` —— 用户不填时默认取 now（UTC），表现为带非零时分
4. `real_estates.vacancy_start_date` —— 退租时由系统写入 UTC
5. `business_events.occurred_at`（仅 asset_acquire） —— 未填发生时间时取 now（UTC）

纠正策略（只动「能确定为系统自动写入」的值，用户手填的日期一律不碰）
--------------------------------------------------------------
- 字段只可能由系统写入 → 全表按本地墙钟偏移平移
- 字段既可能由人填也可能系统默认 → 仅在「值等于 created_at」时平移
  （人填的纯日期是 00:00:00，不可能精确等于创建时刻）

幂等性：由 Alembic 版本号保证只执行一次。
"""

from __future__ import annotations

import os
from datetime import timedelta

import sqlalchemy as sa
from alembic import op

revision = "7c3e5a91b4d8"
down_revision = "2f0a070ed517"
branch_labels = None
depends_on = None


def _offset_hours() -> int:
    """账本时区相对 UTC 的整小时偏移（默认 Asia/Shanghai = +8）。"""
    name = os.environ.get("FAM_LOCAL_TZ", "Asia/Shanghai")
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        probe = datetime(2026, 1, 1, tzinfo=timezone.utc)
        delta = ZoneInfo(name).utcoffset(probe) or timedelta(hours=8)
        return int(delta.total_seconds() // 3600)
    except Exception:  # noqa: BLE001
        return 8


def _shift(column: str, hours: int) -> str:
    """跨方言的「时间字段 + N 小时」表达式。"""
    if op.get_bind().dialect.name == "sqlite":
        sign = f"+{hours}" if hours >= 0 else str(hours)
        return f"datetime({column}, '{sign} hours')"
    return f"DATE_ADD({column}, INTERVAL {hours} HOUR)"


def _same_instant(left: str, right: str) -> str:
    """跨方言判断两个时间字段是否表示同一时刻（避免 SQLite 字符串精度差异）。"""
    if op.get_bind().dialect.name == "sqlite":
        return f"datetime({left}) = datetime({right})"
    return f"{left} = {right}"


def _has_clock_time(column: str) -> str:
    if op.get_bind().dialect.name == "sqlite":
        return f"strftime('%H:%M:%S', {column}) <> '00:00:00'"
    return f"TIME({column}) <> '00:00:00'"


def upgrade() -> None:
    hours = _offset_hours()
    if hours == 0:
        return  # 账本时区就是 UTC，无需纠正

    bind = op.get_bind()

    # 1) 资产估值日：只由系统写入 → 全量平移
    bind.execute(sa.text(
        f"UPDATE assets SET last_valuation_date = {_shift('last_valuation_date', hours)} "
        "WHERE last_valuation_date IS NOT NULL"
    ))

    # 2) 账户开户日：仅当等于创建时刻（说明是系统默认值，不是用户填的）
    bind.execute(sa.text(
        f"UPDATE accounts SET opening_date = {_shift('opening_date', hours)} "
        f"WHERE opening_date IS NOT NULL AND {_same_instant('opening_date', 'created_at')}"
    ))

    # 3) 资产购置日：带非零时分且等于创建时刻 → 系统默认值
    bind.execute(sa.text(
        f"UPDATE assets SET acquire_date = {_shift('acquire_date', hours)} "
        f"WHERE acquire_date IS NOT NULL AND {_has_clock_time('acquire_date')} "
        f"AND {_same_instant('acquire_date', 'created_at')}"
    ))

    # 4) 不动产空置开始日：退租时由系统写入
    bind.execute(sa.text(
        f"UPDATE real_estates SET vacancy_start_date = {_shift('vacancy_start_date', hours)} "
        "WHERE vacancy_start_date IS NOT NULL"
    ))

    # 5) 资产购置事件的发生时间：仅当等于创建时刻
    bind.execute(sa.text(
        f"UPDATE business_events SET occurred_at = {_shift('occurred_at', hours)} "
        "WHERE event_type = 'asset_acquire' AND occurred_at IS NOT NULL "
        f"AND {_same_instant('occurred_at', 'created_at')}"
    ))


def downgrade() -> None:
    """不回退数据。

    纠正后无法可靠区分「纠正过的系统值」与「用户后来手填的值」（条件里的
    `= created_at` 在平移后已不成立），盲目 -8 小时会破坏真实数据。
    如需回退，请从迁移前的备份恢复。
    """
    raise RuntimeError(
        "本迁移包含数据回填/修正，无法安全回退；如需回退，请使用升级前的数据库备份恢复。"
    )
    return
