"""诊断一次聚合请求到底发了哪些 SQL、重复了多少次。

用途：接口变慢时先看这里，比猜「可能是缓存问题」快得多。
本轮就是靠它发现一次快照里 `investment_trades` 被查了 17 次、
`accounts` 被查 6 次。

用法：

```bash
cd backend
FAM_DATABASE_URL="sqlite+aiosqlite:///./data/_bench.db" \
  python scripts/diag_snapshot.py snapshot        # 或 reminders / overview
  python scripts/diag_snapshot.py snapshot --profile   # 另附 cProfile 热点
```

需要先按 `docs/guides/开发启动说明.md` 造一份压测库，否则数据太少看不出问题。
"""

from __future__ import annotations

import asyncio
import cProfile
import io
import os
import pstats
import sys
import time
from collections import Counter

os.environ.setdefault("FAM_DATABASE_URL", "sqlite+aiosqlite:///./data/_bench.db")
os.environ.setdefault("FAM_APP_ENV", "development")

from sqlalchemy import event, select  # noqa: E402

from app.core.context import CurrentUser  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models.identity import HouseholdMember, User  # noqa: E402
from app.services import insights as insights_service  # noqa: E402
from app.services import reminders as reminder_service  # noqa: E402
from app.services import stats as stats_service  # noqa: E402

SQLS: list[tuple[str, float]] = []
_STARTED = [0.0]


def _before(conn, cursor, statement, parameters, context, executemany):
    _STARTED[0] = time.perf_counter()
    SQLS.append((statement, 0.0))


def _after(conn, cursor, statement, parameters, context, executemany):
    if SQLS:
        sql, _ = SQLS[-1]
        SQLS[-1] = (sql, (time.perf_counter() - _STARTED[0]) * 1000)


async def _build_user(db) -> CurrentUser:
    user = (await db.execute(select(User).limit(1))).scalars().first()
    member = (
        await db.execute(
            select(HouseholdMember).where(HouseholdMember.user_id == user.id).limit(1)
        )
    ).scalars().first()
    return CurrentUser(
        user_id=user.id, username=user.username, display_name=user.display_name or "x",
        is_system_admin=True, household_id=member.household_id,
    )


async def main(topic: str, with_profile: bool) -> int:
    sync_engine = SessionLocal.kw["bind"].sync_engine
    event.listen(sync_engine, "before_cursor_execute", _before)
    event.listen(sync_engine, "after_cursor_execute", _after)

    async with SessionLocal() as db:
        user = await _build_user(db)
        calls = {
            "snapshot": lambda: stats_service.dashboard_snapshot(db, user, "current_month"),
            "reminders": lambda: reminder_service.list_reminders(db, user),
            "overview": lambda: insights_service.insight_overview(db, user, "current_month"),
        }
        if topic not in calls:
            print(f"未知主题 {topic}，可选：{'/'.join(calls)}")
            return 2
        fn = calls[topic]

        await fn()  # 预热（含首次导入、连接建立，不计入统计）
        db.info.clear()
        SQLS.clear()

        if with_profile:
            pr = cProfile.Profile()
            pr.enable()
        started = time.perf_counter()
        await fn()
        elapsed = (time.perf_counter() - started) * 1000
        if with_profile:
            pr.disable()
        db.info.clear()

    print(f"\n{topic}: 总 {elapsed:.1f}ms，SQL {len(SQLS)} 条，SQL 累计 {sum(s for _, s in SQLS):.1f}ms")

    counts = Counter(" ".join(sql.split())[:90] for sql, _ in SQLS)
    repeated = [(head, n) for head, n in counts.most_common(15) if n > 1]
    if repeated:
        print("\n重复执行的 SQL：")
        for head, n in repeated:
            print(f"  ×{n:<3} {head}")

    print("\n最慢 8 条：")
    for sql, ms in sorted(SQLS, key=lambda x: -x[1])[:8]:
        print(f"  {ms:7.2f}ms  {' '.join(sql.split())[:100]}")

    if with_profile:
        stream = io.StringIO()
        pstats.Stats(pr, stream=stream).sort_stats("tottime").print_stats(12)
        print("\nPython 热点（tottime）：")
        for line in stream.getvalue().splitlines():
            if line.strip().startswith(tuple("0123456789")) and "function calls" not in line:
                print(line)
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(asyncio.run(main(args[0] if args else "snapshot", "--profile" in sys.argv)))
