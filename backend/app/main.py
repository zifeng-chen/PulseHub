"""FAM V2.1 后端应用入口（18 §2.1、24 §1.2）。

启动顺序：加载配置 → 初始化日志 → 检查目录 → 检查数据库与迁移版本 → 启动业务入口。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import AppError, ErrorCode, error_response, failure
from app.core.logging import setup_logging
from app.db.base import SessionLocal, create_all
from app.db.models.config import AIEvaluationCase
from app.middleware.context import RequestContextMiddleware
from app.middleware.maintenance import MaintenanceModeMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.permissions import seed_role_permissions

logger = logging.getLogger("fam")

# AI 固定评测用例（24 §5）
EVAL_CASES = [
    ("净资产是多少", "net_worth", "家庭当前净资产是多少？", "与确定性统计一致"),
    ("资产占比", "assets", "哪类资产占比最高？", "给出类别与占比，标注数据期间"),
    ("消费总额", "expenses", "本月消费总额是多少？", "与已确认支出事件一致"),
    ("收入来源", "income", "本月收入主要来自哪里？", "排除借款与押金"),
    ("负债余额", "liabilities", "当前负债余额和月供是多少？", "与负债模块一致"),
    ("贷款利息", "liabilities", "本年支付的贷款利息是多少？", "本金不计入利息"),
    ("投资损益", "investments", "当前持仓的未实现损益是多少？", "标注估值日期"),
    ("租金逾期", "leases", "哪些租约存在租金逾期？", "列出租约与金额"),
    ("车辆年度费用", "assets", "车辆年度持有成本是多少？", "按车辆聚合"),
    ("水电煤支出", "assets", "本年水电煤支出合计？", "按不动产聚合"),
    ("数据缺失", "data_quality", "哪些数据缺失会影响分析？", "列出缺失对象"),
    ("多币种分组", "multi_currency", "外币资产折合多少人民币？", "无有效汇率时必须说明未换算"),
]


async def _bootstrap() -> None:
    """启动自检与开发底座建表。生产环境不自动建表，必须走 Alembic 迁移。"""
    if not settings.is_production:
        await create_all()
    async with SessionLocal() as db:
        await seed_role_permissions(db)
        existing = (await db.execute(select(AIEvaluationCase).limit(1))).scalars().first()
        if existing is None:
            for name, category, question, expected in EVAL_CASES:
                db.add(AIEvaluationCase(name=name, category=category,
                                        question=question, expected=expected, enabled=True))
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_secrets()
    setup_logging()
    settings.ensure_dirs()
    app.state.maintenance = {"active": False, "reason": None, "started_at": None}
    logger.info("启动 %s v%s（环境：%s）", settings.app_name, settings.app_version, settings.app_env)
    await _bootstrap()
    # 读取维护状态
    try:
        from app.db.models.identity import MaintenanceWindow

        async with SessionLocal() as db:
            window = (
                await db.execute(select(MaintenanceWindow).where(MaintenanceWindow.status == "active"))
            ).scalars().first()
            if window is not None:
                app.state.maintenance = {"active": True, "reason": window.reason}
    except Exception:  # pragma: no cover
        pass

    # 还款日当天自动提醒（进程内轻量循环；测试环境不启动）
    from app.services import due_notify
    due_notify.start(app)

    yield

    await due_notify.stop(app)
    logger.info("服务已停止")


def create_app() -> FastAPI:
    docs_enabled = not settings.is_production
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )

    # 中间件顺序：**后添加者位于更外层**，所以这里从内到外依次添加。
    # 限流放在最内：CORS 在它外层，429 响应也能带上跨域头（否则浏览器只看到网络错误）；
    # 请求上下文与维护模式在外层，保证即使被限流也有 request_id 与统一日志。
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(MaintenanceModeMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # ---- 异常处理：统一信封 + 中文说明 ----
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        if exc.status_code >= 500:
            logger.exception("业务异常：%s", exc.code)
        return error_response(exc, request)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        field_errors: dict[str, str] = {}
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
            field_errors[".".join(loc) or "request"] = err.get("msg", "字段不合法")
        return JSONResponse(
            status_code=422,
            content=failure(ErrorCode.VALIDATION_ERROR, "提交内容校验失败，请检查字段",
                            field_errors=field_errors, request=request),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("未处理异常")
        return JSONResponse(
            status_code=500,
            content=failure(ErrorCode.INTERNAL_ERROR, request=request,
                            retryable=True),
        )

    app.include_router(api_router, prefix=f"/api/{settings.api_version}")

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "app": settings.app_name,
            "version": settings.app_version,
            "api": f"/api/{settings.api_version}",
            "health": f"/api/{settings.api_version}/health",
        }

    return app


app = create_app()
