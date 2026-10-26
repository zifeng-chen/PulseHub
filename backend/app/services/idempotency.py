"""幂等基座（10 §6、11 §4：写接口支持幂等编号，重复提交不重复创建）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.ids import fingerprint, new_id
from app.db.models.ops import IdempotencyRecord


async def begin_idempotent(
    db: AsyncSession, scope: str, idem_key: str | None, payload: Any
) -> tuple[IdempotencyRecord | None, bool]:
    """返回 (记录, 是否命中重放)。

    - 未提供幂等键：不启用幂等，返回 (None, False)。
    - 命中且指纹一致：返回 (记录, True)，调用方应直接返回上次结果。
    - 命中但指纹不同：抛 IDEMPOTENCY_CONFLICT(409)。
    - 未命中：创建占位记录并返回 (记录, False)。
    """
    if not idem_key:
        return None, False

    fp = fingerprint(payload)
    existing = (
        await db.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idem_key == idem_key,
            )
        )
    ).scalars().first()

    if existing:
        if existing.request_fingerprint != fp:
            raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, status_code=409)
        return existing, True

    record = IdempotencyRecord(
        id=new_id(),
        scope=scope,
        idem_key=idem_key,
        request_fingerprint=fp,
        status="processing",
    )
    db.add(record)
    await db.flush()
    return record, False


def complete_idempotent(record: IdempotencyRecord | None, object_id: str | None, snapshot: str | None = None) -> None:
    if record is None:
        return
    record.object_id = object_id
    record.response_snapshot = snapshot
    record.status = "completed"


def key_from(payload: dict | None, header_key: str | None = None) -> str | None:
    """从请求体或 Idempotency-Key 头里取幂等键（两者都支持，前端重试时复用同一个值）。"""
    if header_key and str(header_key).strip():
        return str(header_key).strip()[:128]
    if payload:
        value = payload.get("idempotency_key") or payload.get("idempotencyKey")
        if value and str(value).strip():
            return str(value).strip()[:128]
    return None


async def guard(
    db: AsyncSession, user_household_id: str | None, scope: str,
    idem_key: str | None, payload: Any,
) -> tuple[IdempotencyRecord | None, bool]:
    """带家庭维度的幂等入口：不同家庭即使用了相同键也不会互相干扰。"""
    if not idem_key:
        return None, False
    return await begin_idempotent(db, f"{scope}:{user_household_id or 'default'}", idem_key, payload)


class ReplayInProgress(AppError):
    """相同幂等键的上一个请求还没完成（例如前一请求仍在事务中或已失败回滚）。"""

    def __init__(self) -> None:
        super().__init__(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "相同请求正在处理中，请稍后刷新列表确认是否已保存",
            status_code=409,
        )
