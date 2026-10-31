"""审计基座（08 §10、18 §2.4、20 §4）。

审计记录只允许追加；敏感值只记录掩码；字段级记录变更前后差异。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.context import CurrentUser, RequestContext
from app.core.ids import new_id, utc_iso
from app.core.logging import mask_value
from app.db.models.identity import AuditLog, AuditLogChange

# 敏感字段（只记录掩码）
SENSITIVE_FIELDS = {
    "password", "password_hash", "new_password", "old_password", "confirm_password",
    "token", "refresh_token", "access_token", "secret", "secret_key", "api_key",
    "password_cipher", "token_cipher", "api_key_cipher", "secret_key_cipher",
    "id_card", "id_number", "vin", "plate", "engine_no", "meter_no", "card_no",
    "certificate_no", "property_cert_no",
}

# 常见字段中文标签（字段级差异展示用；缺失时回退字段名）
FIELD_LABELS: dict[str, str] = {
    "name": "名称", "display_name": "显示名称", "username": "登录账号",
    "email": "邮箱", "phone": "手机号", "status": "状态", "role": "角色",
    "description": "描述", "base_currency": "基础币种", "timezone": "时区",
    "is_multi_member": "是否启用多成员", "default_period": "默认统计周期",
    "asset_purchase_separate": "资产购置独立于消费",
    "investment_valuation_in_net_worth": "投资估值纳入净资产",
    "bill_display_mode": "账单日/还款日显示方式", "amount_precision": "金额精度",
    "theme": "主题模式", "reduced_motion": "减少动效",
    "event_type": "事件类型", "amount": "金额", "currency": "币种",
    "occurred_at": "发生时间", "title": "标题", "note": "备注",
    "category_id": "分类", "account_id": "账户", "member_id": "成员",
    "asset_id": "资产", "counterparty": "商户/对方", "source": "来源",
    "enabled": "启用状态", "protocol": "协议", "base_url": "服务地址",
    "model": "模型", "timeout": "超时", "context_length": "上下文长度",
    "cloud_egress_enabled": "云端外发总开关", "mask_sensitive": "敏感字段脱敏",
    "provider": "提供商", "region": "区域", "endpoint": "接口地址",
    "channel_type": "渠道类型", "type_switches": "提醒类型开关",
    "include_in_net_worth": "是否计入净资产", "account_type": "账户类型",
    "institution": "机构", "opening_balance": "期初余额", "opening_date": "期初日期",
    "event_no": "事件编号", "object_id": "对象", "object_type": "对象类型",
    "reason": "原因", "result": "结果",
}


def label_for(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


def _safe(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if field_name.lower() in SENSITIVE_FIELDS or any(s in field_name.lower() for s in SENSITIVE_FIELDS):
        return mask_value(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def build_changes(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    fields: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """计算字段级前后差异。"""
    before = before or {}
    after = after or {}
    keys = set(fields or (set(before) | set(after)))
    changes: list[dict[str, str]] = []
    for key in sorted(keys):
        b = _safe(before.get(key), key)
        a = _safe(after.get(key), key)
        if b != a:
            changes.append({"field": key, "label": label_for(key), "before": b, "after": a})
    return changes


def summarize(payload: dict[str, Any] | None, limit: int = 20) -> str | None:
    if not payload:
        return None
    items = {k: _safe(v, k) for k, v in list(payload.items())[:limit]}
    return json.dumps(items, ensure_ascii=False)


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    action_label: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    related_object_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    changes: list[dict[str, str]] | None = None,
    reason: str | None = None,
    result: str = "success",
    error_code: str | None = None,
    duration_ms: int | None = None,
    external_provider: str | None = None,
    desensitized: bool = True,
    user: CurrentUser | None = None,
    ctx: RequestContext | None = None,
    household_id: str | None = None,
) -> AuditLog:
    """写入审计主记录 + 字段级差异。调用方负责提交事务。"""
    change_list = changes if changes is not None else build_changes(before, after)

    log = AuditLog(
        id=new_id(),
        household_id=household_id or (user.household_id if user else None),
        request_id=ctx.request_id if ctx else None,
        actor_id=user.user_id if user else None,
        actor_name=user.display_name if user else None,
        actor_role=user.role if user else None,
        session_id=user.session_id if user else None,
        ip_summary=ctx.ip_summary if ctx else None,
        source_page=ctx.source_page if ctx else None,
        source_method=ctx.source_method if ctx else "manual",
        action=action,
        object_type=object_type,
        object_id=object_id,
        related_object_id=related_object_id,
        reason=reason,
        result=result,
        error_code=error_code,
        duration_ms=duration_ms,
        external_provider=external_provider,
        desensitized=desensitized,
        before_summary=summarize(before),
        after_summary=summarize(after),
        extra=json.dumps({"action_label": action_label or action}, ensure_ascii=False),
    )
    db.add(log)

    for ch in change_list:
        db.add(
            AuditLogChange(
                id=new_id(),
                audit_log_id=log.id,
                field_name=ch["field"],
                field_label=ch.get("label") or label_for(ch["field"]),
                before_value=ch.get("before") or "",
                after_value=ch.get("after") or "",
            )
        )
    return log


async def list_audit_logs(
    db: AsyncSession,
    *,
    household_id: str | None,
    page: int = 1,
    page_size: int = 20,
    action: str | None = None,
    object_type: str | None = None,
    actor_id: str | None = None,
) -> tuple[list[AuditLog], int]:
    from sqlalchemy import func

    # 没有家庭归属就不能查审计：条件式过滤（`if household_id:` 才加 where）在
    # household_id 为空时会退化成「查全部家庭」，已移除家庭的账号仍能登录，
    # 于是能读到别人家的操作记录。这里的语义改成「无家庭 = 看不到任何审计」。
    if not household_id:
        return [], 0

    conditions = [AuditLog.household_id == household_id]
    if action:
        conditions.append(AuditLog.action == action)
    if object_type:
        conditions.append(AuditLog.object_type == object_type)
    if actor_id:
        conditions.append(AuditLog.actor_id == actor_id)

    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, int(total)


async def audit_detail(
    db: AsyncSession, log_id: str, household_id: str | None = None
) -> tuple[AuditLog | None, list[AuditLogChange]]:
    # 与列表同理：无家庭归属时不返回任何记录，避免按 id 直读别家的审计详情
    if not household_id:
        return None, []
    conditions = [AuditLog.id == log_id, AuditLog.household_id == household_id]
    log = (await db.execute(select(AuditLog).where(*conditions))).scalars().first()
    if not log:
        return None, []
    changes = list(
        (await db.execute(select(AuditLogChange).where(AuditLogChange.audit_log_id == log_id)))
        .scalars()
        .all()
    )
    return log, changes


def serialize_audit(log: AuditLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "request_id": log.request_id,
        "created_at": utc_iso(log.created_at),
        "actor_id": log.actor_id,
        "actor_name": log.actor_name,
        "actor_role": log.actor_role,
        "role_label": enum_zh.zh("role", log.actor_role),
        "household_id": log.household_id,
        "session_id": log.session_id,
        "ip_summary": log.ip_summary,
        "source_page": log.source_page,
        "source_method": log.source_method,
        "source_method_label": enum_zh.zh("source_method", log.source_method),
        "action": log.action,
        "object_type": log.object_type,
        "object_id": log.object_id,
        "related_object_id": log.related_object_id,
        "reason": log.reason,
        "result": log.result,
        "error_code": log.error_code,
        "duration_ms": log.duration_ms,
        "external_provider": log.external_provider,
        "desensitized": log.desensitized,
        "before_summary": log.before_summary,
        "after_summary": log.after_summary,
        "action_label": log.action,
    }


def serialize_change(ch: AuditLogChange) -> dict[str, Any]:
    return {
        "field_name": ch.field_name,
        "field_label": ch.field_label,
        "before_value": ch.before_value,
        "after_value": ch.after_value,
    }
