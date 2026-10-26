"""数据导入服务（24 §7：人工审核式导入，不提供财务报表导出）。

流程：上传 CSV → 解析 → 字段映射 → 校验（金额/类型/账户/分类）→ 重复检查 →
预览 → 生成草稿事件 → 用户批量确认 → 生成正式事件与审计。
导入失败只影响当前批次，不污染正式数据；不绕过金额、币种、权限与统一事件规则。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.timeutil import local_iso, local_now, to_local_naive
from app.core.context import CurrentUser, RequestContext
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.money import fmt_amount
from app.db.models.business import Account, BusinessEvent
from app.db.models.config import Category, ImportBatch, ImportRecord
from app.services import audit as audit_service
from app.services import events as event_service

# CSV 列名（中英文）→ 标准字段
_COLUMN_ALIASES = {
    "类型": "event_type", "event_type": "event_type", "type": "event_type",
    "标题": "title", "title": "title",
    "金额": "amount", "amount": "amount",
    "日期": "occurred_at", "occurred_at": "occurred_at", "date": "occurred_at",
    "账户": "account", "account": "account", "账户名": "account",
    "分类": "category", "category": "category", "分类名": "category",
    "备注": "note", "note": "note",
}

# 事件类型显示 → 内部值
_EVENT_TYPE_MAP = {
    "收入": "income", "income": "income",
    "支出": "expense", "expense": "expense",
}

_REQUIRED_COLUMNS = ("event_type", "title", "amount")


def _parse_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError(ErrorCode.VALIDATION_ERROR, "CSV 缺少表头",
                       field_errors={"file": "CSV 必须包含表头行"})
    rows: list[dict] = []
    for raw in reader:
        item: dict = {}
        for key, val in raw.items():
            field = _COLUMN_ALIASES.get(key.strip(), key.strip())
            item[field] = _sanitize_cell((val or "").strip())
        if any(item.values()):
            rows.append(item)
    first_fields = set(rows[0].keys()) if rows else set()
    lacking = [c for c in _REQUIRED_COLUMNS if c not in first_fields]
    if lacking:
        raise AppError(ErrorCode.VALIDATION_ERROR, "CSV 缺少必需列",
                       field_errors={"file": f"缺少必需列：{', '.join(lacking)}（类型/标题/金额）"})
    return rows


def _sanitize_cell(value: str) -> str:
    """防 CSV/公式注入：以 = + - @ 开头的单元格会被表格软件当作公式执行。

    加单引号前缀使其退化为普通文本；仅对「文本型」字段（标题/备注/对方）有意义，
    金额列会走数值校验，不受影响。
    """
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


async def _load_maps(db: AsyncSession, household_id: str) -> tuple[dict, dict]:
    accounts = (await db.execute(
        select(Account).where(Account.household_id == household_id, Account.deleted_at.is_(None))
    )).scalars().all()
    categories = (await db.execute(
        select(Category).where(Category.household_id == household_id, Category.deleted_at.is_(None))
    )).scalars().all()
    acc_map = {a.name: a.id for a in accounts if a.status == "active"}
    cat_map = {c.name: c for c in categories if c.status == "active"}
    return acc_map, cat_map


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return to_local_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


async def _find_existing_matches(
    db: AsyncSession, household_id: str, parsed: list[dict],
) -> dict[tuple[str, str], list[dict]]:
    """在已有流水里找「同日期 + 同金额」的记录，供导入时人工确认是否同一笔。

    只做提示、不自动判重：同一天同金额完全可能是两笔真实消费（两杯咖啡），
    所以命中的行交给用户逐条决定「跳过」还是「照常导入」。
    返回 {(日期, 金额): [已存在事件摘要]}。
    """
    # 传入的是 _analyze 的行结果：occurred_at 已是 local_iso 字符串，amount 已是格式化字符串
    pairs = [
        (p["occurred_at"][:10], p["amount"])
        for p in parsed if p.get("amount") and p.get("occurred_at")
    ]
    if not pairs:
        return {}

    amounts = {Decimal(amount) for _, amount in pairs}
    dates = sorted({date for date, _ in pairs})
    date_from = to_local_naive(datetime.fromisoformat(dates[0] + "T00:00:00"))
    date_to = to_local_naive(datetime.fromisoformat(dates[-1] + "T00:00:00")) + timedelta(days=1)

    rows = (await db.execute(
        select(BusinessEvent).where(
            BusinessEvent.household_id == household_id,
            BusinessEvent.deleted_at.is_(None),
            BusinessEvent.status.in_(("draft", "confirmed")),
            BusinessEvent.event_type.in_(("income", "expense")),
            BusinessEvent.amount.in_(amounts),
            BusinessEvent.occurred_at >= date_from,
            BusinessEvent.occurred_at < date_to,
        ).order_by(BusinessEvent.occurred_at.desc())
    )).scalars().all()

    matches: dict[tuple[str, str], list[dict]] = {}
    for event in rows:
        if event.occurred_at is None:
            continue
        key = (event.occurred_at.date().isoformat(), fmt_amount(event.amount))
        bucket = matches.setdefault(key, [])
        if len(bucket) >= 5:          # 最多提示 5 条，避免同日同额太多时刷屏
            continue
        bucket.append({
            "event_id": event.id,
            "event_no": event.event_no,
            "title": event.title,
            "amount": fmt_amount(event.amount),
            "occurred_at": local_iso(event.occurred_at),
            "status": event.status,
            "status_label": enum_zh.zh("event_status", event.status),
            "event_type_label": enum_zh.zh("event_type", event.event_type),
            "source_label": enum_zh.zh("source_method", event.source),
        })
    return matches


async def _analyze(db: AsyncSession, user: CurrentUser, rows: list[dict]) -> dict:
    """解析并校验每行，返回结构化结果（不落库）。"""
    acc_map, cat_map = await _load_maps(db, user.household_id or "")
    seen: set[tuple] = set()
    results: list[dict] = []

    for row in rows:
        errs: list[str] = []
        event_type = _EVENT_TYPE_MAP.get(row.get("event_type", ""))
        if event_type is None:
            errs.append("事件类型必须是「收入」或「支出」")

        title = row.get("title", "")
        if not title:
            errs.append("标题不能为空")

        amount = None
        raw_amount = row.get("amount", "")
        if not raw_amount:
            errs.append("金额不能为空")
        else:
            try:
                amount = Decimal(raw_amount)
            except Exception:  # noqa: BLE001
                errs.append("金额格式不正确")
            else:
                if amount < 0:
                    errs.append("金额不能为负数")

        occurred_at = None
        raw_date = row.get("occurred_at", "")
        if raw_date:
            occurred_at = _parse_date(raw_date)
            if occurred_at is None:
                errs.append("日期格式应为 YYYY-MM-DD")

        account_id = None
        acc_name = row.get("account", "")
        if acc_name:
            account_id = acc_map.get(acc_name)
            if account_id is None:
                errs.append(f"账户「{acc_name}」不存在或已停用")

        category_id = None
        cat_name = row.get("category", "")
        if cat_name:
            cat = cat_map.get(cat_name)
            if cat is None:
                errs.append(f"分类「{cat_name}」不存在或已停用")
            elif event_type and cat.domain != event_type:
                errs.append(f"分类「{cat_name}」与事件类型不匹配")
            else:
                category_id = cat.id

        # 重复检查（同批次内：日期 + 金额 + 标题）
        dup_key = (occurred_at.date().isoformat() if occurred_at else "", str(amount or ""), title)
        duplicate = dup_key in seen
        if not duplicate and not errs:
            seen.add(dup_key)

        results.append({
            "event_type": event_type,
            "title": title,
            "amount": fmt_amount(amount) if amount is not None else "",
            "occurred_at": local_iso(occurred_at) if occurred_at else None,
            "account_id": account_id,
            "category_id": category_id,
            "account": acc_name,
            "category": cat_name,
            "note": row.get("note", ""),
            "raw": row,
            "errors": errs,
            "duplicate": duplicate,
            "suspected_duplicates": [],   # 与已有流水「同日期 + 同金额」的匹配（下面统一回填）
            "suspected_duplicate": False,
            "ok": not errs and not duplicate,
        })

    # 与库内已有流水比对：同日期 + 同金额视为「疑似重复」，交用户确认
    matches = await _find_existing_matches(db, user.household_id or "", results)
    for r in results:
        if r["errors"] or r["amount"] == "" or not r["occurred_at"]:
            continue
        key = (r["occurred_at"][:10], r["amount"])
        hit = matches.get(key) or []
        if hit:
            r["suspected_duplicates"] = hit
            r["suspected_duplicate"] = True

    return {"rows": results, "total": len(results),
            "success": sum(1 for r in results if r["ok"]),
            "error": sum(1 for r in results if r["errors"]),
            "duplicate": sum(1 for r in results if r["duplicate"]),
            "suspected_duplicate": sum(1 for r in results if r["suspected_duplicate"])}


async def preview_import(db: AsyncSession, user: CurrentUser, text: str) -> dict:
    rows = _parse_rows(text)
    return await _analyze(db, user, rows)


async def create_import(
    db: AsyncSession, user: CurrentUser, file_name: str, text: str,
    ctx: RequestContext | None = None, skip_rows: list[int] | None = None,
) -> ImportBatch:
    """创建导入批次。

    `skip_rows` 是用户在预览页确认「与已有流水是同一笔」的行号（从 1 开始）：
    这些行只落记录、标记为 duplicate，不生成草稿事件——避免一笔消费记两遍。
    """
    rows = _parse_rows(text)
    analysis = await _analyze(db, user, rows)
    skip = {int(n) for n in (skip_rows or []) if str(n).isdigit()}

    batch = ImportBatch(
        id=new_id(), household_id=user.household_id,
        source_file_name=file_name, status="draft",
        field_mapping=json.dumps(list(_COLUMN_ALIASES.keys()), ensure_ascii=False),
        total_rows=analysis["total"],
        success_rows=sum(
            1 for idx, r in enumerate(analysis["rows"], start=1) if r["ok"] and idx not in skip),
        error_rows=analysis["error"],
        duplicate_count=analysis["duplicate"] + sum(
            1 for idx, r in enumerate(analysis["rows"], start=1) if idx in skip and r["ok"]),
        note="人工审核式导入：成功行生成草稿事件，需逐条或批量确认后才入账",
        created_by=user.user_id, updated_by=user.user_id,
    )
    db.add(batch)
    await db.flush()

    # 成功行生成草稿事件；错误/重复/用户确认跳过行只落记录，不生成事件
    for idx, r in enumerate(analysis["rows"], start=1):
        status = "error" if r["errors"] else ("duplicate" if (r["duplicate"] or idx in skip) else "pending")
        error_text = "；".join(r["errors"])
        if not error_text and idx in skip:
            error_text = "用户确认与已有流水为同一笔，已跳过"
        record = ImportRecord(
            id=new_id(), household_id=user.household_id, batch_id=batch.id,
            row_no=idx, raw_data=json.dumps(r["raw"], ensure_ascii=False),
            status=status,
            error=error_text or None,
            duplicate_of=None,
            created_by=user.user_id, updated_by=user.user_id,
        )
        db.add(record)
        await db.flush()

        if status == "pending":
            event = await event_service.create_draft(db, user, {
                "event_type": r["event_type"], "title": r["title"],
                "amount": r["amount"], "occurred_at": r["occurred_at"],
                "account_id": r["account_id"], "category_id": r["category_id"],
                "note": r["note"], "source": "manual",
            })
            record.event_id = event.id

    await audit_service.record_audit(
        db, action="import.create", action_label="数据导入（生成草稿）",
        object_type="import_batch", object_id=batch.id,
        after={"file": file_name, "total": analysis["total"],
               "success": analysis["success"], "error": analysis["error"],
               "duplicate": analysis["duplicate"]},
        user=user, ctx=ctx,
    )
    await db.flush()
    return batch


async def list_batches(db: AsyncSession, user: CurrentUser) -> list[ImportBatch]:
    return list((await db.execute(
        select(ImportBatch).where(
            ImportBatch.household_id == user.household_id,
            ImportBatch.deleted_at.is_(None),
        ).order_by(ImportBatch.created_at.desc())
    )).scalars().all())


def serialize_batch(b: ImportBatch) -> dict:
    return {
        "id": b.id, "source_file_name": b.source_file_name, "status": b.status,
        "status_label": "已确认" if b.status == "confirmed" else "草稿（待确认）",
        "total_rows": b.total_rows, "success_rows": b.success_rows,
        "error_rows": b.error_rows, "duplicate_count": b.duplicate_count,
        "note": b.note, "created_at": utc_iso(b.created_at),
    }


async def get_batch(db: AsyncSession, user: CurrentUser, batch_id: str) -> tuple[ImportBatch, list[ImportRecord]]:
    batch = (await db.execute(
        select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.deleted_at.is_(None))
    )).scalars().first()
    if batch is None or batch.household_id != user.household_id:
        raise AppError(ErrorCode.NOT_FOUND, "导入批次不存在", status_code=404)
    records = list((await db.execute(
        select(ImportRecord).where(ImportRecord.batch_id == batch_id).order_by(ImportRecord.row_no)
    )).scalars().all())
    return batch, records


def serialize_record(r: ImportRecord) -> dict:
    return {
        "id": r.id, "row_no": r.row_no, "status": r.status,
        "status_label": {"pending": "待确认", "error": "错误", "duplicate": "重复",
                         "confirmed": "已确认"}.get(r.status, r.status),
        "raw_data": json.loads(r.raw_data) if r.raw_data else None,
        "error": r.error, "duplicate_of": r.duplicate_of, "event_id": r.event_id,
    }


async def confirm_batch(
    db: AsyncSession, user: CurrentUser, batch_id: str,
    ctx: RequestContext | None = None,
) -> tuple[ImportBatch, int]:
    """批量确认草稿事件（24 §7：用户确认后才生成正式事件与审计）。"""
    batch, records = await get_batch(db, user, batch_id)
    if batch.status == "confirmed":
        raise AppError(ErrorCode.STATE_INVALID, "该批次已确认", status_code=409)

    confirmed = 0
    for r in records:
        if r.status != "pending" or not r.event_id:
            continue
        event = (await db.execute(
            select(BusinessEvent).where(BusinessEvent.id == r.event_id)
        )).scalars().first()
        if event is None or event.status != "draft":
            continue
        await event_service.confirm_event(db, user, event.id, event.version, ctx)
        r.status = "confirmed"
        r.updated_by = user.user_id
        confirmed += 1

    batch.status = "confirmed"
    batch.updated_by = user.user_id

    await audit_service.record_audit(
        db, action="import.confirm", action_label="批量确认导入草稿",
        object_type="import_batch", object_id=batch.id,
        after={"confirmed": confirmed},
        user=user, ctx=ctx,
    )
    await db.flush()
    return batch, confirmed
