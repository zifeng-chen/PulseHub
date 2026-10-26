"""字典与分类基座（08 §3、23 §3、08 §10 系统状态）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import enums as enum_zh
from app.core.config import settings
from app.core.context import CurrentUser, RequestContext
from app.core.crypto import key_version
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.db.models.business import BusinessEvent
from app.db.models.config import (
    Category,
    MaintenanceWindow,
    ThirdPartyAIProvider,
)
from app.db.models.identity import AuditLog, Household, User
from app.services import audit as audit_service

CATEGORY_DOMAINS = {
    "income": "收入",
    "expense": "支出",
    "asset": "资产",
    "liability": "负债",
    "investment": "投资",
    "lease": "租赁",
    "account": "账户",
}


ALLOWED_ACCOUNT_APPLIES = {"bank", "cash", "e_wallet", "other"}
ALLOWED_ATTRIBUTE_PROFILES = {
    "credit_card", "credit_line", "loan_line",
    "debit_card", "savings_current", "savings_fixed",
    "wallet", "cash",
}


def _normalize_profile(value) -> str | None:
    """字段档案白名单校验；非法值视为未设置（按账户类型兜底）。"""
    if not value:
        return None
    text = str(value).strip()
    return text if text in ALLOWED_ATTRIBUTE_PROFILES else None


def _normalize_applies(value) -> str | None:
    """把「适用范围」规整为逗号分隔字符串；空值返回 None（表示通用）。"""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(p).strip() for p in value]
    else:
        return None
    parts = [p for p in parts if p]
    return ",".join(parts) or None


def serialize_category(c: Category) -> dict:
    return {
        "id": c.id,
        "domain": c.domain,
        "domain_label": CATEGORY_DOMAINS.get(c.domain, c.domain),
        "parent_id": c.parent_id,
        "name": c.name,
        "level": c.level,
        "sort_order": c.sort_order,
        "status": c.status,
        "status_label": enum_zh.zh("lifecycle_status", c.status),
        "is_system": c.is_system,
        "ai_recommendable": c.ai_recommendable,
        "applicable_event_types": c.applicable_event_types,
        "applies_to": c.applies_to,
        "attribute_profile": c.attribute_profile,
    }


async def list_categories(db: AsyncSession, household_id: str, domain: str | None = None) -> list[Category]:
    stmt = select(Category).where(Category.household_id == household_id, Category.deleted_at.is_(None))
    if domain:
        stmt = stmt.where(Category.domain == domain)
    stmt = stmt.order_by(Category.domain, Category.sort_order)
    rows = list((await db.execute(stmt)).scalars().all())
    return _tree_order(rows)


def _tree_order(rows: list[Category]) -> list[Category]:
    """按「一级分类 → 其下二级分类」重新排列。

    单靠 sort_order 排序会把子分类和一级分类混在一起（子分类的 sort_order 与父级无关），
    分类管理页就会出现「餐饮」夹在「早餐 / 打车」中间的乱序，用户看不出层级。
    这里按 domain 分组，一级按 (sort_order, name)，二级跟在自己的父分类后面；
    父分类缺失（被删或跨域）的孤儿分类放到末尾，不丢数据。
    """
    key = lambda c: (c.sort_order or 0, c.name or "")  # noqa: E731
    by_domain: dict[str, list[Category]] = {}
    for row in rows:
        by_domain.setdefault(row.domain, []).append(row)

    ordered: list[Category] = []
    for domain_rows in by_domain.values():
        by_id = {c.id: c for c in domain_rows}
        children: dict[str, list[Category]] = {}
        orphans: list[Category] = []
        for row in domain_rows:
            if not row.parent_id:
                continue
            if row.parent_id in by_id:
                children.setdefault(row.parent_id, []).append(row)
            else:
                orphans.append(row)
        roots = sorted((c for c in domain_rows if not c.parent_id), key=key)
        for root in roots:
            ordered.append(root)
            ordered.extend(sorted(children.get(root.id, []), key=key))
        ordered.extend(sorted(orphans, key=key))
    return ordered


async def category_label_map(db: AsyncSession, household_id: str | None) -> dict[str, str]:
    """分类 id -> 中文名映射（08 §3 六个分类域），供各业务模块序列化回填分类标签。"""
    rows = (await db.execute(
        select(Category.id, Category.name).where(
            Category.household_id == household_id,
            Category.deleted_at.is_(None),
        )
    )).all()
    return {row[0]: row[1] for row in rows}


async def ensure_category_id(
    db: AsyncSession, household_id: str, domain: str, name: str,
    *, parent_name: str | None = None,
) -> str | None:
    """按名字取分类 id，没有就建一个。

    用于系统自动记账（还款利息、转账手续费）：这些流水不是用户手输的，
    没有分类就会在流水与分析里显示「未分类」，口径上讲不通。
    优先复用已有同名分类（用户可能在分类管理里自己建过），避免重复。
    """
    parent_id: str | None = None
    if parent_name:
        parent_id = await ensure_category_id(db, household_id, domain, parent_name)

    rows = list((await db.execute(
        select(Category).where(
            Category.household_id == household_id,
            Category.domain == domain,
            Category.name == name,
            Category.deleted_at.is_(None),
        )
    )).scalars().all())
    for category in rows:
        if (category.parent_id or None) == parent_id:
            return category.id
    if not parent_name and rows:
        # 不指定父级时，任意同名分类都能用（多为用户自建的一级分类）
        return rows[0].id

    category = Category(
        id=new_id(),
        household_id=household_id,
        domain=domain,
        parent_id=parent_id,
        name=name,
        level=2 if parent_id else 1,
        sort_order=99,
        is_system=True,
        ai_recommendable=domain in ("income", "expense"),
    )
    db.add(category)
    await db.flush()
    return category.id


async def create_category(db: AsyncSession, user: CurrentUser, payload: dict,
                          ctx: RequestContext | None = None) -> Category:
    domain = payload.get("domain")
    if domain not in CATEGORY_DOMAINS:
        raise AppError(ErrorCode.VALIDATION_ERROR, "分类域不合法", field_errors={"domain": "请选择分类域"})
    name = (payload.get("name") or "").strip()
    if not name:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请填写分类名称", field_errors={"name": "请填写分类名称"})

    parent_id = payload.get("parent_id")
    level = 1
    inherited_applies: str | None = None
    inherited_profile: str | None = None
    if parent_id:
        parent = (await db.execute(select(Category).where(Category.id == parent_id))).scalars().first()
        if parent is None or parent.household_id != user.household_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "上级分类不存在",
                           field_errors={"parent_id": "上级分类不存在"})
        level = parent.level + 1
        inherited_applies = parent.applies_to
        inherited_profile = parent.attribute_profile

    # 适用范围（账户域=适用的账户类型）；未显式指定时继承上级分类
    applies_to = _normalize_applies(payload.get("applies_to")) or inherited_applies
    # 字段档案（账户域=表单显示哪些专属字段）；未指定时继承上级分类
    attribute_profile = _normalize_profile(payload.get("attribute_profile")) or inherited_profile

    category = Category(
        id=new_id(),
        household_id=user.household_id or "",
        domain=domain,
        parent_id=parent_id,
        name=name,
        level=level,
        sort_order=int(payload.get("sort_order") or 0),
        status="active",
        is_system=False,
        ai_recommendable=bool(payload.get("ai_recommendable", True)),
        applicable_event_types=payload.get("applicable_event_types"),
        applies_to=applies_to,
        attribute_profile=attribute_profile,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(category)
    await audit_service.record_audit(
        db,
        action="category.create",
        action_label="新增分类",
        object_type="category",
        object_id=category.id,
        after={"name": name, "domain": domain},
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return category


async def update_category(db: AsyncSession, user: CurrentUser, category_id: str, payload: dict,
                          ctx: RequestContext | None = None) -> Category:
    category = (await db.execute(select(Category).where(Category.id == category_id))).scalars().first()
    if category is None or category.household_id != user.household_id:
        raise AppError(ErrorCode.NOT_FOUND, "分类不存在", status_code=404)

    before = {"name": category.name, "status": category.status,
              "sort_order": category.sort_order, "ai_recommendable": category.ai_recommendable,
              "applies_to": category.applies_to, "attribute_profile": category.attribute_profile}
    if "name" in payload and payload["name"]:
        category.name = payload["name"].strip()
    if "sort_order" in payload:
        category.sort_order = int(payload["sort_order"])
    if "ai_recommendable" in payload:
        category.ai_recommendable = bool(payload["ai_recommendable"])
    if "applies_to" in payload:
        category.applies_to = _normalize_applies(payload["applies_to"])
    if "attribute_profile" in payload:
        category.attribute_profile = _normalize_profile(payload["attribute_profile"])
    if "status" in payload and payload["status"]:
        category.status = payload["status"]

    after = {"name": category.name, "status": category.status,
             "sort_order": category.sort_order, "ai_recommendable": category.ai_recommendable,
             "applies_to": category.applies_to, "attribute_profile": category.attribute_profile}
    await audit_service.record_audit(
        db,
        action="category.update",
        action_label="编辑分类",
        object_type="category",
        object_id=category.id,
        before=before,
        after=after,
        user=user,
        ctx=ctx,
    )
    await db.flush()
    return category


async def assert_category_unused(db: AsyncSession, category_id: str) -> None:
    """已被业务事件、资产、负债、投资或租赁使用的分类不能物理删除（08 §3）。"""
    used = (
        await db.execute(select(BusinessEvent).where(BusinessEvent.category_id == category_id).limit(1))
    ).scalars().first()
    if used is not None:
        raise AppError(ErrorCode.STATE_INVALID, "该分类已被业务数据使用，不能删除，只能停用", status_code=409)

    from app.db.models.business import Account, Asset
    from app.db.models.finance import InvestmentAccount, Liability
    from app.db.models.leases import Lease

    for model, label in (
        (Asset, "资产"),
        (Liability, "负债"),
        (InvestmentAccount, "投资"),
        (Lease, "租赁"),
        (Account, "账户"),
    ):
        row = (
            await db.execute(select(model).where(model.category_id == category_id).limit(1))
        ).scalars().first()
        if row is not None:
            raise AppError(
                ErrorCode.STATE_INVALID,
                f"该分类已被{label}引用，不能删除，只能停用",
                status_code=409,
            )


async def system_status(db: AsyncSession, household_id: str | None) -> dict:
    """系统状态页数据（08 §10、18 §2.6）。"""
    from app.services.setup import current_migration_version

    providers = list((await db.execute(select(ThirdPartyAIProvider))).scalars().all())
    maintenance = (
        await db.execute(
            select(MaintenanceWindow).where(MaintenanceWindow.status == "active").limit(1)
        )
    ).scalars().first()
    users = int((await db.execute(select(User.id))).scalars().all().__len__())
    audit_count = len((await db.execute(select(AuditLog.id).limit(1000))).scalars().all())
    household = (await db.execute(select(Household))).scalars().first()

    # 供应商状态统一走 adapter.status()，与「设置首页」「AI 面板」同一口径：
    # 直接读 last_status 会出现两套取值（adapter 写 connected/failed，真实调用写 ok/failed），
    # 曾导致「已配置并可用」被误显示成灰色/未配置。
    from app.adapters import build_adapters

    adapter_statuses = {
        key: (await adapter.status()).to_dict()
        for key, adapter in build_adapters(db, household_id).items()
    }

    def _provider_view(key: str) -> dict:
        s = adapter_statuses[key]
        return {
            "status": s["status"],
            "status_label": s["status_label"],
            "label": s["status_label"],
            "detail": s.get("detail", ""),
            "last_checked_at": s.get("last_checked_at"),
        }

    return {
        "app_version": settings.app_version,
        "env": settings.app_env,
        "database": {
            "label": "本地开发数据库（SQLite）" if settings.database_url.startswith("sqlite") else "MySQL 8.x",
            "connected": True,
        },
        "migration_version": await current_migration_version(db),
        "file_storage": settings.attachment_dir,
        "key_version": key_version(),
        "maintenance": {
            "active": maintenance is not None,
            "reason": maintenance.reason if maintenance else None,
            "started_at": utc_iso(maintenance.started_at) if maintenance and maintenance.started_at else None,
        },
        "providers": {
            "local_ai": _provider_view("local_ai"),
            "ocr": _provider_view("ocr"),
            "notification": _provider_view("notification"),
            "third_party_ai": {
                **_provider_view("third_party_ai"),
                "enabled_count": sum(1 for p in providers if p.enabled),
            },
        },
        "counts": {"users": users, "audit_records": audit_count},
        "household": None if household is None else {
            "name": household.name, "base_currency": household.base_currency, "timezone": household.timezone,
        },
    }


def enum_dictionary() -> dict:
    """全部枚举中文映射，供前端字典与测试核对（03 §3.1）。"""
    return {
        "registry": enum_zh.dump_registry(),
        "category_domains": CATEGORY_DOMAINS,
    }
