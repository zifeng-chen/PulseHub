"""业务时间口径。

平台把「业务时间」（发生时间、交易时间、估值时间等）按家庭本地时区保存为
**本地墙钟时间（naive）**，与用户在页面上选择的日期时间是同一个值；
系统时间（created_at / updated_at / 审计时间）仍然保存 UTC。

原因：
- MySQL / SQLite 的 DATETIME 不保存时区，带偏移的值落库时会被直接截断，
  如果代码又按 UTC 解读，就会出现「显示多 8 小时、月初月末算错一天」；
- 家庭账本关心的是"本地哪一天"，统一用本地墙钟最直观，也不需要历史数据迁移。

对外序列化时用 `local_iso()`（不带 Z / 偏移），前端按本地时间解析即可。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

try:  # Windows 环境可能缺少 IANA 时区库
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(os.environ.get("FAM_LOCAL_TZ", "Asia/Shanghai"))
except Exception:  # noqa: BLE001
    _TZ = timezone(timedelta(hours=8))


def local_now() -> datetime:
    """当前本地时间（naive），用于业务时间的默认值。"""
    return to_local_naive(datetime.now(timezone.utc))


def to_local_naive(value: datetime | None) -> datetime | None:
    """统一成家庭本地墙钟时间：带时区的先换算，naive 视为已经是本地时间。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(_TZ).replace(tzinfo=None)


def local_tz():
    """家庭本地时区（用于给数据库里的本地墙钟时间补时区）。"""
    return _TZ


def to_local_aware(value: datetime | None) -> datetime | None:
    """给本地墙钟时间补上本地时区；已带时区的换算到本地时区。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_TZ)
    return value.astimezone(_TZ)


def local_iso(value: datetime | None) -> str | None:
    """业务时间对外序列化：本地墙钟 ISO，不带时区后缀。"""
    dt = to_local_naive(value)
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat()
