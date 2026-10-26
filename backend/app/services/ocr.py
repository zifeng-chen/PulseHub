"""OCR 识别服务（06 §10、09 §47、10 §5）。

规则：
- OCR 失败可手工录入，不阻塞手工记账；
- 调用记录保存原文、结构化结果与置信度，支持失败重试；
- 结构化结果仅供用户审核，不自动确认（14 §103、19 §5.8）；
- 低置信度不默认选中：确定性提取标注低置信度，由用户显式确认。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.providers import BaiduOcrAdapter
from app.core import enums as enum_zh
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.db.models.business import Attachment
from app.db.models.capture import OcrCallLog
from app.services import audit as audit_service


_AMOUNT_PATTERNS = [
    re.compile(r"[¥￥]\s*(\d{1,10}(?:\.\d{1,2})?)"),
    re.compile(r"(?:合计|总计|金额|应收|实收)[^\d]{0,6}(\d{1,10}(?:\.\d{1,2})?)"),
]
_DATE_PATTERN = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")


def extract_fields(raw_text: str) -> dict:
    """简单确定性结构化：金额与日期候选（不依赖 AI，低置信度）。"""
    amount = None
    for pattern in _AMOUNT_PATTERNS:
        m = pattern.search(raw_text or "")
        if m:
            amount = m.group(1)
            break
    if amount is None:
        m = re.search(r"(?<!\d)(\d{1,10}\.\d{2})(?!\d)", raw_text or "")
        if m:
            amount = m.group(1)

    date_str = None
    m = _DATE_PATTERN.search(raw_text or "")
    if m:
        date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    confidence = 60 if amount else 30
    return {
        "amount": amount,
        "occurred_at": date_str,
        "confidence": confidence,
        "low_confidence": confidence < 60,
        "note": "OCR 原文的确定性提取，仅供审核参考，不自动确认",
    }


def serialize_log(log: OcrCallLog) -> dict:
    structured = None
    if log.structured_json:
        try:
            structured = json.loads(log.structured_json)
        except json.JSONDecodeError:
            structured = None
    return {
        "id": log.id,
        "attachment_id": log.attachment_id,
        "material_type": log.material_type,
        "material_type_label": enum_zh.zh("material_type", log.material_type) if log.material_type else None,
        "provider": log.provider,
        "status": log.status,
        "status_label": enum_zh.zh("ocr_status", log.status),
        "raw_text": log.raw_text,
        "structured": structured,
        "confidence": log.confidence,
        "error_message": log.error_message,
        "duration_ms": log.duration_ms,
        "retry_count": log.retry_count,
        "note": log.note,
        "created_at": utc_iso(log.created_at),
    }


async def _get_attachment(db: AsyncSession, user: CurrentUser, attachment_id: str) -> Attachment:
    row = (await db.execute(select(Attachment).where(Attachment.id == attachment_id))).scalars().first()
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "附件不存在", status_code=404)
    if row.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return row


async def _get_log(db: AsyncSession, user: CurrentUser, log_id: str) -> OcrCallLog:
    row = (await db.execute(select(OcrCallLog).where(OcrCallLog.id == log_id))).scalars().first()
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "OCR 记录不存在", status_code=404)
    if row.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return row


async def recognize(
    db: AsyncSession,
    user: CurrentUser,
    attachment_id: str,
    material_type: str | None = None,
    ctx: RequestContext | None = None,
) -> OcrCallLog:
    attachment = await _get_attachment(db, user, attachment_id)

    adapter = BaiduOcrAdapter(db, user.household_id)
    started = datetime.now(timezone.utc)

    # 未配置直接抛降级错误（不记录调用，前端显示可配置入口）
    raw_text = await adapter.recognize(attachment.stored_path or "")

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    structured = extract_fields(raw_text)

    log = OcrCallLog(
        id=new_id(),
        household_id=user.household_id,
        attachment_id=attachment.id,
        material_type=material_type or attachment.material_type,
        provider="baidu",
        status="success",
        raw_text=raw_text,
        structured_json=json.dumps(structured, ensure_ascii=False),
        confidence=structured["confidence"],
        duration_ms=duration_ms,
        retry_count=0,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(log)
    await db.flush()

    await audit_service.record_audit(
        db,
        action="ocr.recognize",
        action_label="OCR 识别",
        object_type="ocr_call_log",
        object_id=log.id,
        related_object_id=attachment.id,
        after={"status": "success", "confidence": structured["confidence"],
               "duration_ms": duration_ms},
        user=user,
        ctx=ctx,
    )
    return log


async def record_failure(
    db: AsyncSession,
    user: CurrentUser,
    attachment_id: str,
    error: str,
    material_type: str | None = None,
) -> OcrCallLog:
    """识别失败时记录调用日志（可重试）。"""
    log = OcrCallLog(
        id=new_id(),
        household_id=user.household_id,
        attachment_id=attachment_id,
        material_type=material_type,
        provider="baidu",
        status="failed",
        error_message=error,
        retry_count=0,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(log)
    await db.flush()
    return log


async def list_logs(
    db: AsyncSession, user: CurrentUser, *, attachment_id: str | None = None,
    status: str | None = None, page: int = 1, page_size: int = 20,
) -> tuple[list[OcrCallLog], int]:
    from sqlalchemy import func

    conditions = [OcrCallLog.household_id == user.household_id, OcrCallLog.deleted_at.is_(None)]
    if attachment_id:
        conditions.append(OcrCallLog.attachment_id == attachment_id)
    if status:
        conditions.append(OcrCallLog.status == status)
    total = int((await db.execute(
        select(func.count(OcrCallLog.id)).where(*conditions))).scalar_one())
    rows = list((await db.execute(
        select(OcrCallLog).where(*conditions).order_by(OcrCallLog.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return rows, total


async def retry_log(
    db: AsyncSession, user: CurrentUser, log_id: str, ctx: RequestContext | None = None,
) -> OcrCallLog:
    """对失败记录重试：重新识别原附件，成功则更新原记录，失败则累加重试次数。"""
    log = await _get_log(db, user, log_id)
    if log.status == "success":
        raise AppError(ErrorCode.STATE_INVALID, "该记录已识别成功，无需重试", status_code=409)
    if not log.attachment_id:
        raise AppError(ErrorCode.STATE_INVALID, "缺少关联附件，无法重试", status_code=409)
    attachment = await _get_attachment(db, user, log.attachment_id)

    adapter = BaiduOcrAdapter(db, user.household_id)
    started = datetime.now(timezone.utc)
    raw_text = await adapter.recognize(attachment.stored_path or "")
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    structured = extract_fields(raw_text)

    log.status = "success"
    log.raw_text = raw_text
    log.structured_json = json.dumps(structured, ensure_ascii=False)
    log.confidence = structured["confidence"]
    log.duration_ms = duration_ms
    log.error_message = None
    log.retry_count = (log.retry_count or 0) + 1
    log.updated_by = user.user_id

    await audit_service.record_audit(
        db,
        action="ocr.retry",
        action_label="重试 OCR 识别",
        object_type="ocr_call_log",
        object_id=log.id,
        after={"status": "success", "retry_count": log.retry_count},
        user=user,
        ctx=ctx,
    )
    return log
