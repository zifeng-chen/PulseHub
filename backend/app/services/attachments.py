"""附件服务（24 §4、18 §2.4）。

规则：
- 限制文件类型（图片/PDF 可进入 OCR，未知格式拒绝）、扩展名 + MIME 双校验、大小与单次数量；
- 附件绑定家庭与业务对象，访问/下载重新校验权限；
- 上传前做安全校验（扩展名、MIME、大小、SHA256），下载/删除写审计；
- 删除为软删除，需二次确认令牌与原因。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal  # noqa: F401  (保持导入一致)

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.config import settings
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.db.models.business import Attachment
from app.services import audit as audit_service
from app.services import confirm as confirm_service

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf"}
ALLOWED_MIME = {"image/png", "image/jpeg", "application/pdf"}
MAX_FILES_PER_BATCH = 5
# 文件头校验：扩展名与 MIME 都可被客户端伪造，落盘前再看真实字节
_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "pdf": (b"%PDF-",),
}


def _content_matches(ext: str, content: bytes) -> bool:
    signatures = _SIGNATURES.get(ext)
    if not signatures:
        return False
    return any(content.startswith(sig) for sig in signatures)


def _ext_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lstrip(".").lower()


def _validate_file(filename: str, mime_type: str, size_bytes: int) -> None:
    ext = _ext_of(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "不支持的文件类型",
            field_errors={"file": f"仅支持 {'/'.join(sorted(ALLOWED_EXTENSIONS))}，未知格式拒绝上传"},
        )
    if mime_type and mime_type not in ALLOWED_MIME:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "文件内容类型不匹配",
            field_errors={"file": "仅支持图片与 PDF 文件"},
        )
    if size_bytes <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "文件为空",
                       field_errors={"file": "文件内容为空"})
    if size_bytes > settings.attachment_max_mb * 1024 * 1024:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"文件过大，单文件不能超过 {settings.attachment_max_mb}MB",
            field_errors={"file": f"单文件不能超过 {settings.attachment_max_mb}MB"},
        )


def serialize_attachment(a: Attachment) -> dict:
    return {
        "id": a.id,
        "object_type": a.object_type,
        "object_id": a.object_id,
        "material_type": a.material_type,
        "material_type_label": enum_zh.zh("material_type", a.material_type) if a.material_type else None,
        "file_name": a.file_name,
        "mime_type": a.mime_type,
        "size_bytes": a.size_bytes,
        "status": a.status,
        "status_label": enum_zh.zh("file_status", a.status),
        "source_method": a.source_method,
        "note": a.note,
        "created_at": utc_iso(a.created_at),
        # 引用信息（是否已被业务记录使用），由 attach_reference_info 回填
        "referenced": False,
        "reference_status": None,
        "reference_label": None,
        "reference_id": None,
    }


async def attach_reference_info(db: AsyncSession, household_id: str | None, items: list[dict]) -> list[dict]:
    """给附件列表补「是否已被业务引用」的信息。

    智能录入里，已被确认引用的附件不应再出现在待处理列表里；这里批量查一次业务事件，
    避免前端逐条请求。也兼容上传时直接绑定 object_type/object_id 的情况。
    """
    from app.db.models.business import BusinessEvent

    ids = [i["id"] for i in items if i.get("id")]
    if not ids:
        return items

    rows = (await db.execute(
        select(BusinessEvent.attachment_id, BusinessEvent.id, BusinessEvent.title,
               BusinessEvent.event_type, BusinessEvent.status)
        .where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.attachment_id.in_(ids),
            BusinessEvent.deleted_at.is_(None),
        )
        .order_by(BusinessEvent.created_at)
    )).all()

    by_attachment: dict[str, dict] = {}
    for attachment_id, event_id, title, event_type, status in rows:
        if not attachment_id:
            continue
        current = by_attachment.get(attachment_id)
        # 已确认的引用优先展示（比草稿更有说服力）
        if current is None or (status == "confirmed" and current["reference_status"] != "confirmed"):
            by_attachment[attachment_id] = {
                "referenced": True,
                "reference_status": status,
                "reference_label": f"{enum_zh.zh('event_type', event_type)} · {title}",
                "reference_id": event_id,
            }

    for item in items:
        info = by_attachment.get(item["id"])
        if info:
            item.update(info)
        elif item.get("object_type") and item.get("object_id"):
            # 上传时已直接绑定业务对象，也算已引用
            item.update({
                "referenced": True,
                "reference_status": "bound",
                "reference_label": f"{item['object_type']} 已绑定",
                "reference_id": item["object_id"],
            })
    return items


async def _get_attachment(db: AsyncSession, user: CurrentUser, attachment_id: str) -> Attachment:
    row = (await db.execute(select(Attachment).where(Attachment.id == attachment_id))).scalars().first()
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "附件不存在", status_code=404)
    if row.household_id != user.household_id:
        raise AppError(ErrorCode.FORBIDDEN, status_code=403)
    return row


async def save_upload(
    db: AsyncSession,
    user: CurrentUser,
    file: UploadFile,
    *,
    material_type: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    note: str | None = None,
    ctx: RequestContext | None = None,
) -> Attachment:
    """保存上传附件（安全校验 → 落盘 → 记录 → 审计）。"""
    # 先看已声明的大小，避免把超大文件整个读进内存
    declared = getattr(file, "size", None)
    if declared and declared > settings.attachment_max_mb * 1024 * 1024:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"文件过大，单文件不能超过 {settings.attachment_max_mb}MB",
            field_errors={"file": f"单文件不能超过 {settings.attachment_max_mb}MB"},
        )
    content = await file.read()
    filename = file.filename or "unnamed"
    _validate_file(filename, file.content_type or "", len(content))

    if material_type and material_type not in enum_zh.MATERIAL_TYPE:
        raise AppError(ErrorCode.VALIDATION_ERROR, "材料类型不合法",
                       field_errors={"material_type": "请选择有效材料类型"})

    ext = _ext_of(filename)
    if not _content_matches(ext, content):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "文件内容与扩展名不匹配",
            field_errors={"file": "文件内容不是有效的图片或 PDF"},
        )

    sha256 = hashlib.sha256(content).hexdigest()

    # 落盘到受控附件目录（按家庭隔离，文件名不暴露原始名）
    household_dir = os.path.join(settings.attachment_dir, user.household_id or "default")
    os.makedirs(household_dir, exist_ok=True)
    stored_name = f"{new_id()}.{ext}"
    stored_path = os.path.join(household_dir, stored_name)
    with open(stored_path, "wb") as fh:
        fh.write(content)

    attachment = Attachment(
        id=new_id(),
        household_id=user.household_id,
        object_type=object_type,
        object_id=object_id,
        material_type=material_type,
        file_name=filename,
        stored_path=stored_path,
        mime_type=file.content_type or "",
        size_bytes=len(content),
        sha256=sha256,
        status="ready",
        source_method="manual",
        note=note,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(attachment)
    await db.flush()

    await audit_service.record_audit(
        db,
        action="attachment.upload",
        action_label="上传附件",
        object_type="attachment",
        object_id=attachment.id,
        after={"file_name": filename, "mime_type": attachment.mime_type,
               "size_bytes": len(content), "material_type": material_type},
        user=user,
        ctx=ctx,
    )
    return attachment


async def list_attachments(
    db: AsyncSession, user: CurrentUser, *,
    object_type: str | None = None, object_id: str | None = None, keyword: str | None = None,
) -> list[Attachment]:
    conditions = [Attachment.household_id == user.household_id, Attachment.deleted_at.is_(None)]
    if object_type:
        conditions.append(Attachment.object_type == object_type)
    if object_id:
        conditions.append(Attachment.object_id == object_id)
    if keyword:
        conditions.append(Attachment.file_name.ilike(f"%{keyword}%"))
    return list((await db.execute(
        select(Attachment).where(*conditions).order_by(Attachment.created_at.desc())
    )).scalars().all())


async def resolve_download(db: AsyncSession, user: CurrentUser, attachment_id: str) -> Attachment:
    """下载前重新校验权限（24 §4.2），返回附件（含 stored_path）。"""
    row = await _get_attachment(db, user, attachment_id)
    if not os.path.isfile(row.stored_path or ""):
        raise AppError(ErrorCode.NOT_FOUND, "附件文件缺失", status_code=404)
    await audit_service.record_audit(
        db,
        action="attachment.download",
        action_label="下载附件",
        object_type="attachment",
        object_id=row.id,
        user=user,
    )
    return row


async def delete_attachment(
    db: AsyncSession, user: CurrentUser, attachment_id: str, payload: dict,
    ctx: RequestContext | None = None,
) -> Attachment:
    """软删除附件（需二次确认令牌 + 原因）。"""
    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise AppError(ErrorCode.VALIDATION_ERROR, "删除附件必须填写原因",
                       field_errors={"reason": "请填写删除原因"})
    confirm_service.consume_token(payload.get("confirm_token"), user,
                                  "attachment.delete", "attachment", attachment_id)
    row = await _get_attachment(db, user, attachment_id)
    before = {"file_name": row.file_name}
    row.deleted_at = datetime.now(timezone.utc)
    row.updated_by = user.user_id
    await audit_service.record_audit(
        db,
        action="attachment.delete",
        action_label="删除附件",
        object_type="attachment",
        object_id=row.id,
        before=before,
        reason=reason,
        user=user,
        ctx=ctx,
    )
    return row
