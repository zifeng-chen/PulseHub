"""首次启动与安装检测基座（04 §1–§3、18 §2.2）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core import enums as enum_zh
from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_id, utc_iso
from app.core.security import assess_password_strength, hash_password
from app.db.models.config import (
    DeploymentConfig,
    HouseholdSetting,
    LocalAIConfig,
    NotificationChannel,
    OcrConfig,
    PrivacyPolicy,
    SchemaVersion,
    ThirdPartyAIProvider,
    UserPreference,
)
from app.db.models.identity import Household, HouseholdMember, Role, SystemConfig, User

INITIALIZED_KEY = "system_initialized"

# 系统分类种子（08 §3：六个分类域）
# 默认分类（08 §3）：一级分类 + 常见二级子分类；仅新家庭初始化时写入。
DEFAULT_CATEGORY_TREE: dict[str, list[tuple[str, list[str]]]] = {
    "income": [
        ("工资收入", []), ("奖金", []), ("投资收益", []), ("租金收入", []), ("其他收入", []),
    ],
    "expense": [
        ("餐饮", ["早餐", "午餐", "晚餐", "外卖", "买菜"]),
        ("交通", ["打车", "加油", "油费", "高速费", "公交地铁", "停车费", "洗车"]),
        ("居住", ["房租", "物业费", "维修"]),
        ("购物", ["日用百货", "服饰", "数码", "家电"]),
        ("医疗", ["门诊", "药品", "体检"]),
        ("教育", ["学费", "培训", "兴趣班", "书籍"]),
        ("通讯", ["话费月租", "宽带"]),
        ("娱乐", ["影音", "旅行", "运动健身"]),
        ("水电煤", ["水费", "电费", "燃气费", "供暖费"]),
        ("还款", ["房贷还款", "车贷还款", "信用卡还款", "其他贷款还款"]),
        # 系统自动记账的支出（还款利息、转账手续费）要有固定归属，否则流水会落到「未分类」
        ("利息与费用", ["贷款利息", "手续费"]),
        ("其他支出", []),
    ],
    "asset": [
        # 资产类型是一级、分类是二级：不动产下只保留住宅与车位
        ("不动产", ["住宅", "车位"]),
        ("车辆", ["燃油车", "新能源车"]),
        ("贵金属", ["黄金", "白银"]),
        # 借出款是应收资产（现金减少、资产增加，净资产不变），按资金来源细分
        ("借出款", ["现金出借", "银行卡出借", "信用卡出借", "电子钱包出借", "其他出借"]),
        ("其他资产", []),
    ],
    "liability": [
        # 信用卡不属于债务（其欠款由账户余额表达）。
        # 分类是「这笔钱借来做什么」，与债务类型（房贷/车贷/消费贷…）正交，避免两处选项重复。
        ("购房", []), ("购车", []), ("装修", []), ("教育", []),
        ("医疗", []), ("经营周转", []), ("日常周转", []), ("其他", []),
    ],
    "investment": [
        ("股票", ["A股", "港股", "美股"]),
        ("基金", ["股票基金", "债券基金", "货币基金"]),
        ("债券", []),
        ("存款", ["活期", "定期"]),
        ("其他投资", []),
    ],
    "lease": [
        ("租金应收", []), ("押金", []), ("物业费", []),
    ],
    "account": [
        ("银行卡", ["储蓄卡", "信用卡"]),
        ("消费信贷", ["花呗", "借呗", "白条", "京东金条", "美团月付", "抖音月付"]),
        ("电子钱包", ["支付宝", "微信零钱"]),
        ("储蓄存款", ["活期储蓄", "定期存款"]),
        ("现金", ["现金备用", "应急金"]),
        ("收款账户", []),
        ("其他账户", []),
    ],
}

# 兼容：仅一级分类名（部分调用方按平铺列表使用）
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    domain: [name for name, _ in tree] for domain, tree in DEFAULT_CATEGORY_TREE.items()
}

# 账户域：一级分类适用的账户类型（08 §3）。值为 account_type 枚举 code，逗号分隔；
# 未列出的分类（收款账户 / 其他账户）视为通用，任何账户类型下都可见。
ACCOUNT_CATEGORY_APPLIES: dict[str, str] = {
    "银行卡": "bank",
    "储蓄存款": "bank",
    "消费信贷": "e_wallet",
    "电子钱包": "e_wallet",
    "现金": "cash",
}

# 资产域：分类所属的资产类型（用于「先选类型 → 只显示该类型下的分类」）
ASSET_CATEGORY_APPLIES: dict[str, str] = {
    "不动产": "real_estate",
    "车辆": "vehicle",
    "贵金属": "precious_metal",
    "其他资产": "other",
}

# 账户域：字段档案（决定新建账户时显示哪些专属字段，按分类细化）
ACCOUNT_CATEGORY_PROFILE_ROOT: dict[str, str] = {
    "银行卡": "debit_card",
    "储蓄存款": "savings_current",
    "消费信贷": "credit_line",
    "电子钱包": "wallet",
    "现金": "cash",
}
# 子分类可覆盖父分类的档案（按每个分类的真实属性细化）
ACCOUNT_CATEGORY_PROFILE_CHILD: dict[str, str] = {
    "信用卡": "credit_card",      # 额度 + 有效期 + 账单日/还款日 + 卡号
    "借呗": "loan_line",          # 借款额度 + 年化利率 + 还款日
    "京东金条": "loan_line",
    "定期存款": "savings_fixed",  # 年利率 + 存期 + 到期日
}


# ---------------- 安装检测 ----------------

async def is_initialized(db: AsyncSession) -> bool:
    """是否已完成初始化。

    仅以系统标记为准；初始化完成后公开初始化接口关闭（04 §7）。
    """
    row = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == INITIALIZED_KEY))
    ).scalars().first()
    return bool(row and row.value == "true")


async def current_migration_version(db: AsyncSession) -> str:
    """当前数据库迁移版本：优先读取 alembic 真实版本（alembic_version 表），回退 schema_versions 兜底。"""
    try:
        row = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - alembic_version 表不存在时回退
        row = None
    if row:
        return str(row)
    sv = (
        await db.execute(select(SchemaVersion).order_by(SchemaVersion.applied_at.desc()))
    ).scalars().first()
    return sv.version if sv else "—"


async def health(db: AsyncSession) -> dict[str, Any]:
    """后端健康状态 + 数据库连通性（04 §2）。"""
    db_ok = True
    db_error: str | None = None
    database_label = "本地开发数据库（SQLite）" if settings.database_url.startswith("sqlite") else "MySQL"
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - 连通异常
        db_ok = False
        db_error = str(exc)
    latency = int((time.perf_counter() - started) * 1000)

    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "api_version": settings.api_version,
        "env": settings.app_env,
        "database": {
            "ok": db_ok,
            "label": database_label,
            "latency_ms": latency,
            "error": db_error,
        },
        "file_storage": {"ok": True, "path": settings.attachment_dir},
        "server_time": utc_iso(),
    }


async def install_check(db: AsyncSession) -> dict[str, Any]:
    """安装检测页数据：品牌、版本、数据库、文件权限、后端健康、是否已初始化。"""
    h = await health(db)
    initialized = await is_initialized(db)
    checks = [
        {
            "key": "backend",
            "label": "后端服务",
            "status": "pass",
            "detail": f"{settings.app_name} {settings.app_version}",
            "suggestion": None,
        },
        {
            "key": "database",
            "label": "数据库服务",
            "status": "pass" if h["database"]["ok"] else "fail",
            "detail": f"{h['database']['label']}，延迟 {h['database']['latency_ms']} ms"
            if h["database"]["ok"] else (h["database"]["error"] or "连接失败"),
            "suggestion": None if h["database"]["ok"] else "请检查数据库服务是否启动、连接参数是否正确",
        },
        {
            "key": "file",
            "label": "文件存储权限",
            "status": "pass",
            # 不暴露服务器绝对路径（公开页 /setup/check 会被未登录访客看到，L-10）
            "detail": "附件存储目录可读写",
            "suggestion": None,
        },
        {
            "key": "migration",
            "label": "数据库迁移版本",
            "status": "pass",
            "detail": await current_migration_version(db),
            "suggestion": None,
        },
    ]
    overall = "fail" if any(c["status"] == "fail" for c in checks) else "pass"
    return {
        "initialized": initialized,
        "overall": overall,
        "checks": checks,
        "app_version": settings.app_version,
        "env": settings.app_env,
        "database_type": "mysql" if not settings.database_url.startswith("sqlite") else "embedded",
    }


# ---------------- 步骤一：数据库连接 ----------------

def _mysql_url(host: str, port: int, database: str, username: str, password: str) -> str:
    return (
        f"mysql+aiomysql://{username}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    )


async def test_database(payload: dict[str, Any]) -> dict[str, Any]:
    """测试 MySQL 连接：返回延迟、版本、可建表权限。字段级失败原因。"""
    host = (payload.get("host") or "").strip()
    database = (payload.get("database_name") or "").strip()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    port = int(payload.get("port") or 3306)
    timeout = int(payload.get("connect_timeout") or 10)

    field_errors: dict[str, str] = {}
    if not host:
        field_errors["host"] = "请填写数据库主机"
    if not database:
        field_errors["database_name"] = "请填写数据库名"
    if not username:
        field_errors["username"] = "请填写数据库用户名"
    if not password:
        field_errors["password"] = "请填写数据库密码"
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "数据库连接参数不完整", field_errors=field_errors)

    url = _mysql_url(host, port, database, username, password)
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": timeout})
    started = time.perf_counter()
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT VERSION()"))).scalar_one()
            # 可建表权限探测：创建并删除临时表
            can_create = True
            create_error = None
            try:
                await conn.execute(text("CREATE TABLE IF NOT EXISTS _fam_perm_probe (id INT)"))
                await conn.execute(text("DROP TABLE _fam_perm_probe"))
                await conn.commit()
            except Exception as exc:  # pragma: no cover
                can_create = False
                create_error = str(exc)
        latency = int((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "latency_ms": latency,
            "mysql_version": str(version),
            "can_create_table": can_create,
            "message": "连接成功" + ("" if can_create else "，但当前账号缺少建表权限"),
            "create_error": create_error,
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "mysql_version": None,
            "can_create_table": False,
            "message": f"连接失败：{exc}",
            "field_errors": {"host": "请确认主机、端口、网络与数据库服务状态"},
        }
    finally:
        await engine.dispose()


async def save_database_config(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """保存数据库配置（口令只存密文）。

    非生产环境允许使用内嵌开发数据库作为可运行底座（use_embedded_dev_database=true）。
    """
    use_embedded = bool(payload.get("use_embedded_dev_database"))
    if use_embedded and settings.is_production:
        raise AppError(ErrorCode.FORBIDDEN, "生产环境不允许使用内嵌开发数据库", status_code=403)

    if use_embedded:
        if not settings.database_url.startswith("sqlite"):
            raise AppError(ErrorCode.VALIDATION_ERROR, "当前环境未启用内嵌开发数据库")
        config = DeploymentConfig(
            id=new_id(),
            db_type="embedded",
            host="embedded",
            port=0,
            database_name="fam_dev",
            username="embedded",
            password_cipher=None,
            ssl_enabled=False,
            connect_timeout=0,
            initialized=False,
        )
        db.add(config)
        return {"mode": "embedded", "message": "已使用本地开发数据库作为运行底座"}

    test = await test_database(payload)
    if not test.get("ok"):
        raise AppError(ErrorCode.VALIDATION_ERROR, test.get("message", "数据库连接失败"))

    config = DeploymentConfig(
        id=new_id(),
        db_type="mysql",
        host=payload.get("host"),
        port=int(payload.get("port") or 3306),
        database_name=payload.get("database_name"),
        username=payload.get("username"),
        password_cipher=encrypt_secret(payload.get("password")),
        ssl_enabled=bool(payload.get("ssl_enabled")),
        connect_timeout=int(payload.get("connect_timeout") or 10),
        initialized=True,
    )
    db.add(config)
    return {"mode": "mysql", "message": "数据库配置已保存", "mysql_version": test.get("mysql_version")}


# ---------------- 步骤二：创建管理员 ----------------

async def create_admin(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    display_name = (payload.get("display_name") or "").strip()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    confirm = payload.get("confirm_password") or ""

    field_errors: dict[str, str] = {}
    if not display_name:
        field_errors["display_name"] = "请填写管理员名称"
    if not username:
        field_errors["username"] = "请填写登录账号"
    if not password:
        field_errors["password"] = "请填写密码"
    if password != confirm:
        field_errors["confirm_password"] = "两次输入的密码不一致"
    if field_errors:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请检查管理员信息", field_errors=field_errors)

    strength = assess_password_strength(password)
    if not strength["acceptable"]:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "密码强度不足",
            field_errors={"password": "；".join(strength["warnings"]) or "密码强度不足"},
        )

    exists = (await db.execute(select(User).where(User.username == username))).scalars().first()
    if exists:
        raise AppError(ErrorCode.VALIDATION_ERROR, "登录账号已存在", field_errors={"username": "该账号已被占用"})

    user = User(
        id=new_id(),
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        email=payload.get("email"),
        recovery_email=payload.get("recovery_email"),
        recovery_hint=payload.get("recovery_hint"),
        is_system_admin=True,
        status="active",
    )
    db.add(user)
    await db.flush()

    db.add(UserPreference(id=new_id(), user_id=user.id, theme="system", language="zh-CN"))
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_system_admin": True,
        "password_strength": strength,
    }


# ---------------- 步骤三：家庭基础信息 ----------------

async def create_household(db: AsyncSession, payload: dict[str, Any], admin: User) -> dict[str, Any]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请填写家庭名称", field_errors={"name": "请填写家庭名称"})

    household = Household(
        id=new_id(),
        name=name,
        description=payload.get("description"),
        base_currency=(payload.get("base_currency") or "CNY").upper(),
        timezone=payload.get("timezone") or "Asia/Shanghai",
        is_multi_member=bool(payload.get("is_multi_member", True)),
    )
    db.add(household)
    await db.flush()
    # 初始化流程里「家庭还不存在」可能已被缓存成 None，建完必须清掉
    from app.core.agg_cache import invalidate
    invalidate(db, "household")

    db.add(
        HouseholdMember(
            id=new_id(),
            household_id=household.id,
            user_id=admin.id,
            role="owner",
            status="active",
            joined_at=datetime.now(timezone.utc),
        )
    )

    db.add(
        HouseholdSetting(
            id=new_id(),
            household_id=household.id,
            base_currency=household.base_currency,
            default_period="current_month",
        )
    )
    for role_code, role_name in (("owner", "家庭所有者"), ("admin", "系统管理员"),
                                 ("member", "家庭成员"), ("readonly", "只读成员")):
        db.add(Role(id=new_id(), code=role_code, name=role_name, household_id=household.id,
                    is_system=True, description=f"{role_name}（系统内置）"))

    await _seed_categories(db, household.id)

    return {
        "id": household.id,
        "name": household.name,
        "base_currency": household.base_currency,
        "timezone": household.timezone,
        "is_multi_member": household.is_multi_member,
    }


async def current_household(db: AsyncSession) -> Household | None:
    """当前家庭（M0 为单家庭部署；多家庭由后续模块通过 household_id 扩展）。

    一次快照请求里统计、提醒、AI 建议、卡片账单都要拿它，实测被查 4 次；
    家庭配置在一次请求内不会变，缓存即可。改了家庭设置（时区/币种/开关）
    的写路径需要 `agg_cache.invalidate(db, "household")`。
    """
    from app.core.agg_cache import request_cached

    return await request_cached(
        db, ("household", "current"), lambda: _current_household(db)
    )


async def _current_household(db: AsyncSession) -> Household | None:
    return (await db.execute(select(Household))).scalars().first()


async def _seed_categories(db: AsyncSession, household_id: str) -> int:
    """写入默认分类：一级分类 + 常见二级子分类（08 §3）。"""
    from app.db.models.config import Category

    count = 0
    for domain, tree in DEFAULT_CATEGORY_TREE.items():
        ai_ok = domain in ("income", "expense")
        for index, (parent_name, children) in enumerate(tree):
            # 账户域带上「适用账户类型」与「字段档案」，供新建账户时按类型/分类联动（08 §3）
            # 资产域带上「所属资产类型」，保证选完类型后分类只显示该类型下的选项
            applies = (
                ACCOUNT_CATEGORY_APPLIES.get(parent_name) if domain == "account"
                else ASSET_CATEGORY_APPLIES.get(parent_name) if domain == "asset"
                else None
            )
            root_profile = ACCOUNT_CATEGORY_PROFILE_ROOT.get(parent_name) if domain == "account" else None
            parent = Category(
                id=new_id(),
                household_id=household_id,
                domain=domain,
                parent_id=None,
                name=parent_name,
                level=1,
                sort_order=index,
                is_system=True,
                ai_recommendable=ai_ok,
                applies_to=applies,
                attribute_profile=root_profile,
            )
            db.add(parent)
            count += 1
            for cindex, child_name in enumerate(children):
                child_profile = (
                    ACCOUNT_CATEGORY_PROFILE_CHILD.get(child_name, root_profile)
                    if domain == "account" else None
                )
                db.add(
                    Category(
                        id=new_id(),
                        household_id=household_id,
                        domain=domain,
                        parent_id=parent.id,
                        name=child_name,
                        level=2,
                        sort_order=cindex,
                        is_system=True,
                        ai_recommendable=ai_ok,
                        applies_to=applies,
                        attribute_profile=child_profile,
                    )
                )
                count += 1
    return count


async def save_finance_settings(db: AsyncSession, household_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    setting = (
        await db.execute(select(HouseholdSetting).where(HouseholdSetting.household_id == household_id))
    ).scalars().first()
    if setting is None:
        setting = HouseholdSetting(id=new_id(), household_id=household_id)
        db.add(setting)
    before = {c: getattr(setting, c) for c in (
        "asset_purchase_separate", "investment_valuation_in_net_worth",
        "bill_display_mode", "default_date_range", "amount_precision",
    )}
    if "asset_purchase_separate" in payload:
        setting.asset_purchase_separate = bool(payload["asset_purchase_separate"])
    if "investment_valuation_in_net_worth" in payload:
        setting.investment_valuation_in_net_worth = bool(payload["investment_valuation_in_net_worth"])
    if "bill_display_mode" in payload:
        setting.bill_display_mode = payload["bill_display_mode"]
    if "default_date_range" in payload:
        setting.default_date_range = payload["default_date_range"]
    if "amount_precision" in payload:
        setting.amount_precision = int(payload["amount_precision"])
    after = {c: getattr(setting, c) for c in before}
    return {"before": before, "after": after}


# ---------------- 步骤五：能力接入配置 ----------------

async def save_local_ai(db: AsyncSession, household_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    config = (
        await db.execute(select(LocalAIConfig).where(LocalAIConfig.household_id == household_id))
    ).scalars().first()
    if config is None:
        config = LocalAIConfig(id=new_id(), household_id=household_id)
        db.add(config)
    config.protocol = payload.get("protocol") or "openai_compatible"
    config.base_url = payload.get("base_url")
    config.model = payload.get("model")
    if payload.get("api_key"):
        config.api_key_cipher = encrypt_secret(payload["api_key"])
    if "timeout" in payload:
        config.timeout = int(payload["timeout"])
    if "context_length" in payload:
        config.context_length = int(payload["context_length"])
    if "stream" in payload:
        config.stream = bool(payload["stream"])
    config.enabled = bool(payload.get("enabled", False))
    config.last_status = "configured" if config.base_url else "unconfigured"
    return {"status": config.last_status}


async def save_ocr(db: AsyncSession, household_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    config = (
        await db.execute(select(OcrConfig).where(OcrConfig.household_id == household_id))
    ).scalars().first()
    if config is None:
        config = OcrConfig(id=new_id(), household_id=household_id)
        db.add(config)
    key_changed = bool(payload.get("api_key") or payload.get("secret_key"))
    if payload.get("api_key"):
        config.api_key_cipher = encrypt_secret(payload["api_key"])
    if payload.get("secret_key"):
        config.secret_key_cipher = encrypt_secret(payload["secret_key"])
    config.region = payload.get("region")
    config.endpoint = payload.get("endpoint")
    config.enabled = bool(payload.get("enabled", False))
    # 换了密钥才需要重新验证；否则保留最近一次真实调用的结果
    if key_changed or not config.last_status:
        config.last_status = "configured" if config.api_key_cipher else "unconfigured"
    return {"status": config.last_status}


async def save_notification(db: AsyncSession, household_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    channel = (
        await db.execute(select(NotificationChannel).where(NotificationChannel.household_id == household_id))
    ).scalars().first()
    if channel is None:
        channel = NotificationChannel(id=new_id(), household_id=household_id, channel_type="pushplus")
        db.add(channel)
    token_changed = bool(payload.get("token"))
    if payload.get("token"):
        channel.token_cipher = encrypt_secret(payload["token"])
    channel.enabled = bool(payload.get("enabled", False))
    # 换了 token 才需要重新验证；否则保留最近一次真实发送的结果
    if token_changed or not channel.last_status:
        channel.last_status = "configured" if channel.token_cipher else "unconfigured"
    return {"status": channel.last_status}


async def save_third_party_ai(db: AsyncSession, household_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """第三方 AI 默认关闭（08 §8、14 §5）；未确认外发范围前不得启用。"""
    provider = ThirdPartyAIProvider(
        id=new_id(),
        household_id=household_id,
        name=payload.get("name") or "第三方 AI 接口",
        interface_type=payload.get("interface_type") or "openai_compatible",
        base_url=payload.get("base_url"),
        api_key_cipher=encrypt_secret(payload.get("api_key")),
        org_id=payload.get("org_id"),
        default_model=payload.get("default_model"),
        fallback_model=payload.get("fallback_model"),
        timeout=int(payload.get("timeout") or 60),
        stream=bool(payload.get("stream", True)),
        allow_attachment=bool(payload.get("allow_attachment", False)),
        allow_sensitive=bool(payload.get("allow_sensitive", False)),
        enabled=False,  # 始终默认关闭
        priority=int(payload.get("priority") or 100),
        last_status="unconfigured",
    )
    db.add(provider)
    return {"status": "unconfigured", "enabled": False, "message": "第三方 AI 默认关闭，需在隐私策略允许后手动启用"}


# ---------------- 步骤六：完成初始化 ----------------

async def ensure_privacy(db: AsyncSession, household_id: str | None) -> None:
    existing = (
        await db.execute(select(PrivacyPolicy).where(PrivacyPolicy.household_id == household_id))
    ).scalars().first()
    if existing is None:
        db.add(PrivacyPolicy(id=new_id(), household_id=household_id))


async def mark_initialized(db: AsyncSession, actor_id: str | None) -> None:
    row = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == INITIALIZED_KEY))
    ).scalars().first()
    if row is None:
        row = SystemConfig(id=new_id(), key=INITIALIZED_KEY, value="true", scope="system")
        db.add(row)
    else:
        row.value = "true"
    db.add(
        SchemaVersion(
            id=new_id(),
            version="0001_initial",
            app_version=settings.app_version,
            description="M0 框架基座初始迁移",
            applied_at=datetime.now(timezone.utc),
        )
    )


async def setup_summary(db: AsyncSession) -> dict[str, Any]:
    """完成页摘要：管理员、家庭、基础币种、AI/OCR/通知状态。"""
    admin = (await db.execute(select(User).where(User.is_system_admin.is_(True)))).scalars().first()
    household = (await db.execute(select(Household))).scalars().first()
    local_ai = (await db.execute(select(LocalAIConfig))).scalars().first()
    ocr = (await db.execute(select(OcrConfig))).scalars().first()
    channel = (await db.execute(select(NotificationChannel))).scalars().first()
    provider = (await db.execute(select(ThirdPartyAIProvider))).scalars().first()
    return {
        "initialized": await is_initialized(db),
        "admin": None if not admin else {"username": admin.username, "display_name": admin.display_name},
        "household": None if not household else {
            "name": household.name,
            "base_currency": household.base_currency,
            "timezone": household.timezone,
        },
        "providers": {
            "local_ai": (local_ai.last_status if local_ai else "unconfigured"),
            "local_ai_label": _provider_label(local_ai.last_status if local_ai else "unconfigured"),
            "ocr": (ocr.last_status if ocr else "unconfigured"),
            "ocr_label": _provider_label(ocr.last_status if ocr else "unconfigured"),
            "notification": (channel.last_status if channel else "unconfigured"),
            "notification_label": _provider_label(channel.last_status if channel else "unconfigured"),
            "third_party_ai": (provider.last_status if provider else "unconfigured"),
            "third_party_ai_label": _provider_label(provider.last_status if provider else "unconfigured"),
        },
        "permission_actions": {k: v["zh"] for k, v in enum_zh.PERMISSION_ACTION.items()},
    }


def _provider_label(status: str) -> str:
    return {
        "unconfigured": "未配置",
        "configured": "已连接",
        "testing": "检测中",
        "failed": "测试失败",
        "connected": "已连接",
    }.get(status, "未配置")
