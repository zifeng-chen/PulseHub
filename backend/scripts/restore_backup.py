"""离线数据恢复脚本（容器内执行，不经 Web 界面）。

为什么放在这里而不是做成一键按钮：
恢复会覆盖整库业务数据，属于灾难操作。放在运维脚本里，配合 dry-run 与显式 --yes，
比在网页上点一下更安全。

用法：
    python -m scripts.restore_backup --list
    python -m scripts.restore_backup --backup BK20261017120000 --dry-run
    python -m scripts.restore_backup --backup BK20261017120000 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.db.base import Base, SessionLocal
from app.db.models.ops import BackupRecord
from app.services import backup as backup_service
from app.services import backup_data


def _fmt_rows(n: int) -> str:
    return f"{n:,}"


async def _find(db, ref: str) -> BackupRecord | None:
    return (
        await db.execute(
            select(BackupRecord).where(
                (BackupRecord.id == ref) | (BackupRecord.backup_no == ref)
            )
        )
    ).scalars().first()


async def cmd_list(db) -> int:
    rows = await backup_service.list_backups(db, None)
    if not rows:
        print("没有可用备份。")
        return 0
    print(f"{'备份编号':<20} {'状态':<10} {'文件':>6} {'大小':>10} {'迁移版本':<16} 存储目录")
    for r in rows:
        print(f"{r.backup_no:<20} {r.status:<10} {r.file_count:>6} "
              f"{r.size_bytes / 1024:>8.1f}KB {r.migration_version:<16} {r.storage_path}")
    return 0


async def cmd_dry_run(db, ref: str) -> int:
    record = await _find(db, ref)
    if record is None:
        print(f"备份不存在：{ref}")
        return 1

    backup_dir = Path(record.storage_path or "")
    print("=" * 68)
    print(f"备份编号     {record.backup_no}")
    print(f"创建时间     {record.created_at}")
    print(f"数据截止     {record.data_cutoff_at}")
    print(f"存储目录     {backup_dir}")
    print(f"迁移版本     {record.migration_version}")
    print(f"文件数/大小  {record.file_count} / {record.size_bytes / 1024:.1f} KB")
    print(f"校验值       {record.checksum[:32]}…")
    print("=" * 68)

    if not backup_dir.is_dir():
        print("✗ 备份目录不存在，无法恢复。")
        return 1

    current_checksum = backup_service._sha256_dir(str(backup_dir))  # noqa: SLF001
    if current_checksum != record.checksum:
        print("✗ 校验失败：备份目录内容与创建时不一致，拒绝恢复。")
        return 1
    print("✓ 完整性校验通过")

    try:
        payload = backup_data.read_database_file(backup_dir)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        print("  该备份只包含附件快照，没有数据库导出，无法用于数据恢复。")
        return 1

    meta = payload.get(backup_data.META_KEY, {})
    print(f"✓ database.json 可读（导出于 {meta.get('exported_at', '未知')}）")
    print()
    print(f"{'表名':<32} {'备份行数':>10} {'当前行数':>10}  影响")
    total_backup = 0
    for table in Base.metadata.sorted_tables:
        if table.name in backup_data.SKIP_TABLES:
            continue
        backup_rows = len(payload.get(table.name) or [])
        current_rows = (await db.execute(select(func.count()).select_from(table))).scalar() or 0
        total_backup += backup_rows
        flag = "不变" if backup_rows == current_rows else "将被覆盖"
        print(f"{table.name:<32} {_fmt_rows(backup_rows):>10} {_fmt_rows(current_rows):>10}  {flag}")
    print()
    print(f"合计将从备份写入 {total_backup:,} 行。")
    print("恢复前会为当前数据自动生成安全快照（SAFE*），失败会整体回滚。")
    print("⚠ 恢复后所有用户登录态失效，需要重新登录。")
    print()
    print("确认无误后执行：")
    print(f"  python -m scripts.restore_backup --backup {record.backup_no} --yes")
    return 0


async def cmd_restore(ref: str) -> int:
    print(f"开始恢复 {ref} …")
    try:
        result = await backup_service.apply_restore(ref)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 恢复失败，已回滚：{exc}")
        return 1

    print("✓ 恢复完成")
    print(f"  恢复表数     {result['restored_tables']}")
    print(f"  恢复行数     {result['restored_rows']:,}")
    print(f"  恢复前快照   {result['safety_snapshot']}（{result['safety_rows']:,} 行）")
    print(f"  恢复记录 ID  {result['restore_id']}")
    print("⚠ 请重启 API 容器，并让所有用户重新登录。")
    return 0


async def cmd_create(db) -> int:
    """手动创建一份备份（等价于设置页的「创建备份」，供运维在维护窗口前使用）。"""
    from app.core.context import CurrentUser
    from app.db.models.identity import HouseholdMember, User

    user = (
        await db.execute(
            select(User).where(User.is_system_admin.is_(True), User.status == "active")
            .order_by(User.created_at.asc())
        )
    ).scalars().first()
    if user is None:
        print("没有可用的系统管理员，无法创建备份。")
        return 1

    member = (
        await db.execute(
            select(HouseholdMember).where(HouseholdMember.user_id == user.id,
                                          HouseholdMember.status == "active")
        )
    ).scalars().first()

    operator = CurrentUser(
        user_id=user.id, username=user.username, display_name=user.display_name,
        is_system_admin=True, household_id=member.household_id if member else None,
        role=member.role if member else "owner",
    )
    record = await backup_service.create_backup(db, operator)
    await db.commit()
    print("✓ 备份已创建")
    print(f"  备份编号   {record.backup_no}")
    print(f"  文件数     {record.file_count}")
    print(f"  大小       {record.size_bytes / 1024:.1f} KB")
    print(f"  迁移版本   {record.migration_version}")
    print(f"  存储目录   {record.storage_path}")
    return 0


async def main_async(args) -> int:
    try:
        if args.create:
            async with SessionLocal() as db:
                return await cmd_create(db)
        if args.list or not args.yes:
            async with SessionLocal() as db:
                if args.list:
                    return await cmd_list(db)
                if not args.backup:
                    print("缺少 --backup（可先用 --list 查看可用备份）")
                    return 2
                return await cmd_dry_run(db, args.backup)
        return await cmd_restore(args.backup)
    finally:
        # 显式释放连接池，避免进程退出时 asyncio 报 "Event loop is closed"
        from app.db.base import engine

        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="FAM 离线数据恢复")
    parser.add_argument("--list", action="store_true", help="列出可用备份")
    parser.add_argument("--create", action="store_true", help="创建一份新备份")
    parser.add_argument("--backup", help="备份编号或 ID")
    parser.add_argument("--yes", action="store_true", help="真正执行恢复（不加则只做 dry-run）")
    args = parser.parse_args()

    if not args.list and not args.create and not args.backup:
        parser.print_help()
        return 2

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
