"""能力适配器基座（18 §2.5）。

本地 AI / 第三方 AI / 百度 OCR / 通知四类适配器统一接口，基座提供
"未配置 / 模拟成功 / 失败降级"状态，不依赖具体提供商即可启动主应用。
业务模块只能通过适配器接入，不能直接依赖具体厂商 SDK。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError, ErrorCode


@dataclass
class AdapterStatus:
    key: str
    label: str
    status: str = "unconfigured"  # unconfigured|testing|connected|unreachable|failed|disabled
    detail: str = ""
    last_checked_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def status_label(self) -> str:
        return {
            "unconfigured": "未配置",
            "testing": "检测中",
            "connected": "已连接",
            "unreachable": "不可达",
            "failed": "测试失败",
            "untested": "未测试",
            "disabled": "已停用",
        }.get(self.status, "未配置")

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "status_label": self.status_label,
            "detail": self.detail,
            "last_checked_at": self.last_checked_at,
            "extra": self.extra,
        }


class BaseAdapter:
    """适配器统一接口。"""

    key = "base"
    label = "能力适配器"

    async def status(self) -> AdapterStatus:  # pragma: no cover - 抽象
        raise NotImplementedError

    async def test(self) -> AdapterStatus:
        """连接测试。未配置时抛 PROVIDER_NOT_CONFIGURED，页面显示可操作配置入口。"""
        current = await self.status()
        if current.status in ("unconfigured", "disabled"):
            raise AppError(
                ErrorCode.PROVIDER_NOT_CONFIGURED,
                f"{self.label}尚未配置，请前往设置完成配置",
                status_code=409,
            )
        return current


class UnconfiguredAdapter(BaseAdapter):
    """默认未配置实现：保证未配置时应用仍可运行。"""

    def __init__(self, key: str, label: str, detail: str = "尚未配置") -> None:
        self.key = key
        self.label = label
        self._detail = detail

    async def status(self) -> AdapterStatus:
        return AdapterStatus(key=self.key, label=self.label, status="unconfigured", detail=self._detail)
