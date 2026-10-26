"""统一错误码、异常与响应信封（23 §1、11 §3）。

所有接口成功/失败都走同一信封；失败必须返回中文 message。
HTTP 语义：401 登录、403 权限、404 不存在、409 数据已变化、422 字段错误、429 稍后重试。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ErrorCode:
    OK = "OK"
    VALIDATION_ERROR = "VALIDATION_ERROR"          # 422
    UNAUTHORIZED = "UNAUTHORIZED"                  # 401
    FORBIDDEN = "FORBIDDEN"                        # 403
    NOT_FOUND = "NOT_FOUND"                        # 404
    VERSION_CONFLICT = "VERSION_CONFLICT"          # 409 数据已变化
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"  # 409 幂等键复用且参数不同
    SETUP_ALREADY_DONE = "SETUP_ALREADY_DONE"      # 409 系统已初始化
    SETUP_REQUIRED = "SETUP_REQUIRED"              # 409 未初始化
    LOGIN_FAILED = "LOGIN_FAILED"                  # 401 账号或密码错误（不区分）
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"              # 423 账号锁定
    SESSION_EXPIRED = "SESSION_EXPIRED"            # 401
    RATE_LIMITED = "RATE_LIMITED"                  # 429
    MAINTENANCE_MODE = "MAINTENANCE_MODE"          # 503
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"    # 503
    INTERNAL_ERROR = "INTERNAL_ERROR"              # 500
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"  # 能力未配置（可操作配置入口）
    PROVIDER_TEST_FAILED = "PROVIDER_TEST_FAILED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"              # 第三方接口额度用完（可去服务商购买/申请）
    FILE_TYPE_NOT_ALLOWED = "FILE_TYPE_NOT_ALLOWED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    STATE_INVALID = "STATE_INVALID"                # 状态机不允许的动作


# 错误码 -> HTTP 状态
_CODE_HTTP: dict[str, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.SESSION_EXPIRED: 401,
    ErrorCode.LOGIN_FAILED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.SETUP_ALREADY_DONE: 409,
    ErrorCode.SETUP_REQUIRED: 409,
    ErrorCode.STATE_INVALID: 409,
    ErrorCode.ACCOUNT_LOCKED: 423,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.MAINTENANCE_MODE: 503,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_NOT_CONFIGURED: 409,
    ErrorCode.PROVIDER_TEST_FAILED: 400,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.FILE_TYPE_NOT_ALLOWED: 422,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.INTERNAL_ERROR: 500,
}

# 默认中文说明
_CODE_MESSAGE: dict[str, str] = {
    ErrorCode.OK: "操作成功",
    ErrorCode.VALIDATION_ERROR: "提交内容校验失败，请检查字段",
    ErrorCode.UNAUTHORIZED: "登录状态已失效，请重新登录",
    ErrorCode.SESSION_EXPIRED: "会话已过期，请重新登录",
    ErrorCode.FORBIDDEN: "当前账号没有执行此操作的权限",
    ErrorCode.NOT_FOUND: "对象不存在或已被删除",
    ErrorCode.VERSION_CONFLICT: "数据已被其他操作修改，请刷新后重试",
    ErrorCode.IDEMPOTENCY_CONFLICT: "重复提交且内容不一致，请刷新后重试",
    ErrorCode.SETUP_ALREADY_DONE: "系统已完成初始化，初始化接口已关闭",
    ErrorCode.SETUP_REQUIRED: "系统尚未初始化，请先完成首次启动引导",
    ErrorCode.LOGIN_FAILED: "账号或密码错误",
    ErrorCode.ACCOUNT_LOCKED: "连续登录失败次数过多，账号已被短暂锁定，请稍后再试",
    ErrorCode.RATE_LIMITED: "请求过于频繁，请稍后重试",
    ErrorCode.MAINTENANCE_MODE: "系统处于维护模式，暂时无法写入业务数据",
    ErrorCode.SERVICE_UNAVAILABLE: "服务暂时不可用，请稍后重试",
    ErrorCode.QUOTA_EXCEEDED: "第三方接口额度已用完，请到服务商控制台查看剩余额度",
    ErrorCode.INTERNAL_ERROR: "服务内部错误，请记录请求编号后联系管理员",
    ErrorCode.PROVIDER_NOT_CONFIGURED: "该能力尚未配置，请前往设置完成配置",
    ErrorCode.PROVIDER_TEST_FAILED: "连接测试失败",
    ErrorCode.FILE_TYPE_NOT_ALLOWED: "不支持的文件类型",
    ErrorCode.FILE_TOO_LARGE: "文件超过大小限制",
    ErrorCode.STATE_INVALID: "当前状态不允许执行该操作",
}


class AppError(Exception):
    """业务异常，统一被异常处理器转换为中文错误响应。"""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        field_errors: dict[str, str] | None = None,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.message = message or _CODE_MESSAGE.get(code, "操作失败")
        self.field_errors = field_errors or {}
        self.retryable = retryable
        self.status_code = status_code or _CODE_HTTP.get(code, 400)
        super().__init__(self.message)


def _request_id(request: Request | None) -> str:
    if request is not None:
        rid = getattr(request.state, "request_id", None)
        if rid:
            return rid
    return ""


def success(data: Any, request: Request | None = None, message: str = "操作成功") -> dict:
    return {
        "success": True,
        "data": data,
        "request_id": _request_id(request),
        "message": message,
    }


def failure(
    code: str,
    message: str | None = None,
    *,
    field_errors: dict[str, str] | None = None,
    retryable: bool = False,
    request: Request | None = None,
) -> dict:
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message or _CODE_MESSAGE.get(code, "操作失败"),
            "field_errors": field_errors or {},
            "retryable": retryable,
            "request_id": _request_id(request),
        },
    }


def error_response(err: AppError, request: Request | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=err.status_code,
        content=failure(
            err.code,
            err.message,
            field_errors=err.field_errors,
            retryable=err.retryable,
            request=request,
        ),
    )


def list_payload(
    items: list,
    total: int,
    page: int,
    page_size: int,
    request: Request | None = None,
    message: str = "操作成功",
) -> dict:
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": page * page_size < total,
        },
        request,
        message,
    )
