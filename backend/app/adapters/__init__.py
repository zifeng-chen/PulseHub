"""能力适配器（18 §2.5）。"""

from app.adapters.base import AdapterStatus, BaseAdapter, UnconfiguredAdapter
from app.adapters.providers import (
    BaiduOcrAdapter,
    LocalAIAdapter,
    PushPlusAdapter,
    ThirdPartyAIAdapter,
    build_adapters,
)

__all__ = [
    "AdapterStatus", "BaseAdapter", "UnconfiguredAdapter",
    "LocalAIAdapter", "ThirdPartyAIAdapter", "BaiduOcrAdapter", "PushPlusAdapter",
    "build_adapters",
]
