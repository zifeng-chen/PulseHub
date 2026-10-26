"""备份与恢复基座（22 §1、24 §2）。

备份不是导出功能：用于本地灾备与版本升级恢复，不生成供用户传播的报表文件。
M0 提供接口 + 记录 + 完整性校验；数据库逻辑导出交由部署工具链，附件目录做真实快照。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.db.base import Base
from app.db.models.ops import BackupRecord, RestoreRecord
from app.services import audit as audit_service
from app.services import backup_data


async def _current_migration_version(db: AsyncSession) -> str:
    try:
        row = (await db.execute(text("SELECT version_num FROM alembic_version"))).first()
        return row[0] if row else "unknown"
    except Exception:  # noqa: BLE001 - 开发库可能没有 alembic_version
        return "unknown"


async def _get_record(db: AsyncSession, user: CurrentUser, backup_id: str) -> BackupRecord:
    """读取备份记录并校验家庭归属（备份含全库数据，不能跨家庭读取）。"""
    record = (
        await db.execute(
            select(BackupRecord).where(
                (BackupRecord.id == backup_id) | (BackupRecord.backup_no == backup_id)
            )
        )
    ).scalars().first()
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "备份不存在", status_code=404)
    if record.household_id and record.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return record


def _backup_no() -> str:
    return f"BK{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def _dir_size(path: str) -> tuple[int, int]:
    total = 0
    count = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
                count += 1
            except OSError:
                continue
    return total, count


def _sha256_dir(path: str) -> str:
    digest = hashlib.sha256()
    if not os.path.isdir(path):
        return digest.hexdigest()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            fp = os.path.join(root, name)
            digest.update(name.encode("utf-8"))
            try:
                with open(fp, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                continue
    return digest.hexdigest()


async def create_backup(
    db: AsyncSession, user: CurrentUser, ctx: RequestContext | None = None
) -> BackupRecord:
    backup_no = _backup_no()
    target_dir = os.path.join(settings.backup_dir, backup_no)
    os.makedirs(target_dir, exist_ok=True)

    # 附件目录快照（真实）
    attachments_snapshot = os.path.join(target_dir, "attachments")
    try:
        if os.path.isdir(settings.attachment_dir):
            shutil.copytree(settings.attachment_dir, attachments_snapshot, dirs_exist_ok=True)
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"附件快照失败：{exc}", status_code=500)

    # SQLite 开发底座：直接快照数据库文件
    if settings.database_url.startswith("sqlite"):
        raw = settings.database_url.split("///", 1)[-1]
        if os.path.isfile(raw):
            shutil.copy2(raw, os.path.join(target_dir, os.path.basename(raw)))

    # 数据库逻辑导出（跨库通用，MySQL 生产环境同样可用）
    migration_version = await _current_migration_version(db)
    table_rows = await backup_data.dump_database(db, Path(target_dir), migration_version)

    size, count = _dir_size(target_dir)
    checksum = _sha256_dir(target_dir)

    record = BackupRecord(
        id=new_id(),
        household_id=user.household_id,
        backup_no=backup_no,
        storage_path=target_dir,
        data_cutoff_at=datetime.now(timezone.utc),
        db_version="sqlite" if settings.database_url.startswith("sqlite") else "mysql",
        app_version=settings.app_version,
        migration_version=migration_version,
        file_count=count,
        size_bytes=size,
        checksum=checksum,
        # 备份目录当前为明文存储（未做加密），如实标记，避免给人「已加密」的错觉
        encrypted=False,
        status="created",
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(record)

    await audit_service.record_audit(
        db,
        action="backup.create",
        action_label="创建数据备份",
        object_type="backup",
        object_id=record.id,
        after={
            "backup_no": backup_no, "file_count": count, "checksum": checksum,
            "tables": len(table_rows), "rows": sum(table_rows.values()),
        },
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return record


async def verify_backup(db: AsyncSession, user: CurrentUser, backup_id: str,
                        ctx: RequestContext | None = None) -> dict:
    record = await _get_record(db, user, backup_id)

    ok = os.path.isdir(record.storage_path or "")
    current = _sha256_dir(record.storage_path) if ok else ""
    integrity = bool(ok and current == record.checksum)
    size, count = _dir_size(record.storage_path or "") if ok else (0, 0)

    record.status = "verified" if integrity else "failed"
    if not integrity:
        record.error = "备份目录缺失或校验值不一致"

    await audit_service.record_audit(
        db,
        action="backup.verify",
        action_label="验证备份完整性",
        object_type="backup",
        object_id=record.id,
        after={"integrity": integrity, "file_count": count},
        result="success" if integrity else "failure",
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return {"integrity": integrity, "file_count": count, "size_bytes": size,
            "checksum": record.checksum, "status": record.status}


async def list_backups(db: AsyncSession, household_id: str | None) -> list[BackupRecord]:
    conditions = []
    if household_id:
        conditions.append(BackupRecord.household_id == household_id)
    return list(
        (
            await db.execute(
                select(BackupRecord)
                .where(*conditions)
                .order_by(BackupRecord.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
    )


async def restore_preview(db: AsyncSession, user: CurrentUser, backup_id: str) -> dict:
    record = await _get_record(db, user, backup_id)
    return {
        "backup_no": record.backup_no,
        "app_version": record.app_version,
        "db_version": record.db_version,
        "data_cutoff_at": utc_iso(record.data_cutoff_at) if record.data_cutoff_at else None,
        "file_count": record.file_count,
        "scope": "恢复将覆盖当前业务数据、配置与附件索引，所有用户需重新登录",
        "confirm_phrase_required": "确认恢复",
        "reversible": True,
    }


async def restore_execute(
    db: AsyncSession, user: CurrentUser, backup_id: str, confirm_phrase: str,
    ctx: RequestContext | None = None,
) -> RestoreRecord:
    preview = await restore_preview(db, user, backup_id)
    if confirm_phrase != preview["confirm_phrase_required"]:
        raise AppError(ErrorCode.VALIDATION_ERROR, "确认短语不正确",
                       field_errors={"confirm_phrase": "请输入「确认恢复」"})

    record = RestoreRecord(
        id=new_id(),
        household_id=user.household_id,
        backup_no=preview["backup_no"],
        status="preview",
        started_at=datetime.now(timezone.utc),
        operator_id=user.user_id,
        confirm_phrase=confirm_phrase,
        verification_result="已生成恢复预览，等待维护窗口执行",
        rollback_performed=False,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(record)
    await audit_service.record_audit(
        db,
        action="backup.restore_preview",
        action_label="提交恢复请求",
        object_type="restore",
        object_id=record.id,
        after={"backup_no": preview["backup_no"], "status": record.status},
        reason="管理员在设置中提交数据恢复",
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return record


def serialize_backup(r: BackupRecord) -> dict:
    return {
        "id": r.id,
        "backup_no": r.backup_no,
        "created_at": utc_iso(r.created_at),
        "data_cutoff_at": utc_iso(r.data_cutoff_at) if r.data_cutoff_at else None,
        "db_version": r.db_version,
        "app_version": r.app_version,
        "migration_version": r.migration_version,
        "file_count": r.file_count,
        "size_bytes": r.size_bytes,
        "size_label": f"{r.size_bytes / 1024:.1f} KB",
        "checksum": r.checksum,
        "encrypted": r.encrypted,
        "status": r.status,
        "status_label": {"created": "已创建", "verified": "已验证", "failed": "校验失败"}.get(r.status, r.status),
        "error": r.error,
    }


async def apply_restore(backup_ref: str) -> dict:
    """真正执行数据恢复（离线脚本调用）。

    使用独立 engine / session：恢复过程中会整表覆盖（含 users），
    与 Web 请求共用 session 会导致身份映射与事务状态互相污染。
    """
    from sqlalchemy import func
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            record = (
                await db.execute(
                    select(BackupRecord).where(
                        (BackupRecord.id == backup_ref) | (BackupRecord.backup_no == backup_ref)
                    )
                )
            ).scalars().first()
            if record is None:
                raise AppError(ErrorCode.NOT_FOUND, f"备份不存在：{backup_ref}", status_code=404)

            backup_dir = Path(record.storage_path or "")
            if not backup_dir.is_dir():
                raise AppError(ErrorCode.NOT_FOUND, f"备份目录不存在：{backup_dir}", status_code=404)

            if _sha256_dir(str(backup_dir)) != record.checksum:
                raise AppError(ErrorCode.STATE_INVALID,
                               "备份校验失败：目录内容与创建时不一致，已拒绝恢复", status_code=409)

            backup_data.read_database_file(backup_dir)  # 提前确认 database.json 可读

            # 当前数据安全快照：误执行时还能回来
            safety_no = f"SAFE{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            safety_dir = Path(settings.backup_dir) / safety_no
            safety_dir.mkdir(parents=True, exist_ok=True)
            safety_rows = await backup_data.dump_database(
                db, safety_dir, await _current_migration_version(db))

            # 先把要用到的值取出来，避免 commit 后触发过期对象的懒加载
            household_id = record.household_id
            backup_no = record.backup_no
            operator_id = record.created_by
            restore_id = new_id()

            db.add(RestoreRecord(
                id=restore_id,
                household_id=household_id,
                backup_no=backup_no,
                status="running",
                started_at=datetime.now(timezone.utc),
                operator_id=operator_id,
                confirm_phrase="离线恢复脚本",
                created_by=operator_id,
                updated_by=operator_id,
            ))
            await db.commit()

            try:
                restored = await backup_data.load_database(db, backup_dir)
                await db.flush()

                mismatched: list[str] = []
                for table in Base.metadata.sorted_tables:
                    if table.name in backup_data.SKIP_TABLES:
                        continue
                    actual = (await db.execute(select(func.count()).select_from(table))).scalar() or 0
                    expected = (0 if table.name in backup_data.CLEAR_ONLY_TABLES
                                else restored.get(table.name, 0))
                    if actual != expected:
                        mismatched.append(f"{table.name} 期望 {expected} 实际 {actual}")
                if mismatched:
                    raise AppError(ErrorCode.INTERNAL_ERROR,
                                   "恢复后行数校验不一致：" + "；".join(mismatched[:5]),
                                   status_code=500)

                await db.commit()
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                db.add(RestoreRecord(
                    id=new_id(),
                    household_id=household_id,
                    backup_no=backup_no,
                    status="failed",
                    started_at=datetime.now(timezone.utc),
                    ended_at=datetime.now(timezone.utc),
                    operator_id=operator_id,
                    confirm_phrase="离线恢复脚本",
                    verification_result="恢复失败，已回滚",
                    error=str(exc)[:500],
                    created_by=operator_id,
                    updated_by=operator_id,
                ))
                await db.commit()
                raise

            # 恢复记录随 restore_records 一起被备份数据覆盖过，这里补写一条完成记录
            db.add(RestoreRecord(
                id=new_id(),
                household_id=household_id,
                backup_no=backup_no,
                status="success",
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                operator_id=operator_id,
                confirm_phrase="离线恢复脚本",
                verification_result=(
                    f"已恢复 {len(restored)} 张表、{sum(restored.values())} 行，"
                    f"行数校验通过；恢复前安全快照 {safety_no}"
                ),
                created_by=operator_id,
                updated_by=operator_id,
            ))
            await db.commit()

            return {
                "backup_no": backup_no,
                "restore_id": restore_id,
                "status": "success",
                "restored_tables": len(restored),
                "restored_rows": sum(restored.values()),
                "safety_snapshot": safety_no,
                "safety_rows": sum(safety_rows.values()),
            }
    finally:
        await engine.dispose()


def serialize_restore(r: RestoreRecord) -> dict:
    return {
        "id": r.id,
        "backup_no": r.backup_no,
        "status": r.status,
        "status_label": {"preview": "已提交（待运维执行）", "running": "恢复中", "success": "已完成",
                         "failed": "失败", "rolled_back": "已回滚"}.get(r.status, r.status),
        "started_at": utc_iso(r.started_at) if r.started_at else None,
        "ended_at": utc_iso(r.ended_at) if r.ended_at else None,
        "verification_result": r.verification_result,
        "rollback_performed": r.rollback_performed,
    }
